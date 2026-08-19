//! Child-process management and the newline-delimited JSON protocol
//! described in `PROTOCOL.md` (repo root).

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// A message sent by the agent (child stdout → TUI).
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum AgentMsg {
    Ready {
        model: String,
        journal: String,
        #[serde(default)]
        journal_exists: bool,
    },
    Status {
        #[serde(default)]
        id: Option<u64>,
        text: String,
    },
    ToolCall {
        #[serde(default)]
        id: Option<u64>,
        tool: String,
        #[serde(default)]
        args: Map<String, Value>,
    },
    ToolResult {
        #[serde(default)]
        id: Option<u64>,
        tool: String,
        #[serde(default)]
        result: String,
    },
    Answer {
        #[serde(default)]
        id: Option<u64>,
        text: String,
    },
    Error {
        #[serde(default)]
        id: Option<u64>,
        text: String,
    },
    Bye,
}

/// A message sent by the TUI (TUI → child stdin).
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TuiMsg {
    Ask { id: u64, text: String },
    Reset,
    Quit,
}

/// Everything the background reader threads can report to the main loop.
#[derive(Debug)]
pub enum AgentEvent {
    /// A well-formed protocol message.
    Msg(AgentMsg),
    /// A stdout line that was not valid protocol JSON (kept for the debug pane).
    Malformed(String),
    /// A line from the child's stderr (log output).
    Stderr(String),
    /// The child's stdout reached EOF (the process exited or closed the pipe).
    Eof,
}

/// Parse one stdout line into an [`AgentMsg`].
pub fn parse_line(line: &str) -> serde_json::Result<AgentMsg> {
    serde_json::from_str(line.trim())
}

/// Resolve the spawn command: `$ACCT_AGENT_CMD` split on whitespace, or the default.
pub fn resolve_command() -> Vec<String> {
    match std::env::var("ACCT_AGENT_CMD") {
        Ok(v) if !v.trim().is_empty() => v.split_whitespace().map(str::to_owned).collect(),
        _ => vec!["acct-agent".into(), "serve".into()],
    }
}

/// A running agent child process plus the channel its reader threads feed.
pub struct Agent {
    child: Child,
    stdin: ChildStdin,
    events: Receiver<AgentEvent>,
}

impl Agent {
    /// Spawn `argv` with piped stdin/stdout/stderr and start the reader threads.
    pub fn spawn(argv: &[String]) -> Result<Self> {
        let (program, args) = argv
            .split_first()
            .context("agent command is empty")?;
        let mut child = Command::new(program)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .with_context(|| format!("failed to spawn `{}`", argv.join(" ")))?;

        let stdin = child.stdin.take().context("child stdin was not piped")?;
        let stdout = child.stdout.take().context("child stdout was not piped")?;
        let stderr = child.stderr.take().context("child stderr was not piped")?;

        let (tx, events) = mpsc::channel();

        let tx_out = tx.clone();
        thread::Builder::new()
            .name("agent-stdout".into())
            .spawn(move || read_stdout(stdout, tx_out))
            .context("failed to start stdout reader thread")?;

        thread::Builder::new()
            .name("agent-stderr".into())
            .spawn(move || read_stderr(stderr, tx))
            .context("failed to start stderr reader thread")?;

        Ok(Self {
            child,
            stdin,
            events,
        })
    }

    /// Non-blocking: drain everything the reader threads have queued so far.
    pub fn drain_events(&mut self) -> Vec<AgentEvent> {
        let mut out = Vec::new();
        while let Ok(ev) = self.events.try_recv() {
            out.push(ev);
        }
        out
    }

    /// Write one protocol message as a single JSON line to the child's stdin.
    pub fn send(&mut self, msg: &TuiMsg) -> Result<()> {
        let mut line = serde_json::to_string(msg).context("failed to encode message")?;
        line.push('\n');
        self.stdin
            .write_all(line.as_bytes())
            .and_then(|_| self.stdin.flush())
            .context("failed to write to agent stdin")
    }

    /// Ask the agent to quit, wait up to `grace`, then kill it if still running.
    pub fn shutdown(mut self, grace: Duration) {
        // Best effort: the child may already be gone.
        let _ = self.send(&TuiMsg::Quit);
        // Closing stdin also signals EOF to well-behaved agents.
        drop(self.stdin);

        let deadline = Instant::now() + grace;
        loop {
            match self.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(25)),
                _ => break,
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn read_stdout(stdout: std::process::ChildStdout, tx: Sender<AgentEvent>) {
    let reader = BufReader::new(stdout);
    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            continue;
        }
        let ev = match parse_line(&line) {
            Ok(msg) => AgentEvent::Msg(msg),
            Err(_) => AgentEvent::Malformed(line),
        };
        if tx.send(ev).is_err() {
            return;
        }
    }
    let _ = tx.send(AgentEvent::Eof);
}

fn read_stderr(stderr: std::process::ChildStderr, tx: Sender<AgentEvent>) {
    let reader = BufReader::new(stderr);
    for line in reader.lines() {
        let Ok(line) = line else { break };
        if tx.send(AgentEvent::Stderr(line)).is_err() {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_ready() {
        let msg = parse_line(
            r#"{"type":"ready","model":"gemma4:12b","journal":"/home/me/books/main.journal","journal_exists":true}"#,
        )
        .unwrap();
        assert_eq!(
            msg,
            AgentMsg::Ready {
                model: "gemma4:12b".into(),
                journal: "/home/me/books/main.journal".into(),
                journal_exists: true,
            }
        );
    }

    #[test]
    fn parses_status_tool_call_and_result() {
        let status = parse_line(r#"{"type":"status","id":1,"text":"thinking"}"#).unwrap();
        assert_eq!(
            status,
            AgentMsg::Status {
                id: Some(1),
                text: "thinking".into()
            }
        );

        let call = parse_line(r#"{"type":"tool_call","id":1,"tool":"net_worth","args":{}}"#).unwrap();
        assert_eq!(
            call,
            AgentMsg::ToolCall {
                id: Some(1),
                tool: "net_worth".into(),
                args: Map::new()
            }
        );

        let call = parse_line(
            r#"{"type":"tool_call","id":2,"tool":"spending_by_category","args":{"period":"last month"}}"#,
        )
        .unwrap();
        match call {
            AgentMsg::ToolCall { tool, args, .. } => {
                assert_eq!(tool, "spending_by_category");
                assert_eq!(args.get("period"), Some(&json!("last month")));
            }
            other => panic!("unexpected {other:?}"),
        }

        let result = parse_line(
            r#"{"type":"tool_result","id":1,"tool":"net_worth","result":"ASSETS (USD value):\n ..."}"#,
        )
        .unwrap();
        assert_eq!(
            result,
            AgentMsg::ToolResult {
                id: Some(1),
                tool: "net_worth".into(),
                result: "ASSETS (USD value):\n ...".into()
            }
        );
    }

    #[test]
    fn parses_answer_error_bye() {
        let answer =
            parse_line(r#"{"type":"answer","id":1,"text":"Your net worth is $174,581.60."}"#).unwrap();
        assert_eq!(
            answer,
            AgentMsg::Answer {
                id: Some(1),
                text: "Your net worth is $174,581.60.".into()
            }
        );

        // `id` may be null for errors.
        let err = parse_line(r#"{"type":"error","id":null,"text":"model unavailable"}"#).unwrap();
        assert_eq!(
            err,
            AgentMsg::Error {
                id: None,
                text: "model unavailable".into()
            }
        );

        // ...or missing entirely.
        let err = parse_line(r#"{"type":"error","text":"boom"}"#).unwrap();
        assert!(matches!(err, AgentMsg::Error { id: None, .. }));

        assert_eq!(parse_line(r#"{"type":"bye"}"#).unwrap(), AgentMsg::Bye);
        // Trailing whitespace / CRLF is tolerated.
        assert_eq!(parse_line("{\"type\":\"bye\"}\r\n").unwrap(), AgentMsg::Bye);
    }

    #[test]
    fn rejects_garbage_and_unknown_types() {
        assert!(parse_line("not json").is_err());
        assert!(parse_line(r#"{"type":"teleport"}"#).is_err());
        assert!(parse_line(r#"{"text":"missing type"}"#).is_err());
    }

    #[test]
    fn serializes_outgoing_messages() {
        let ask = TuiMsg::Ask {
            id: 1,
            text: "What is my net worth?".into(),
        };
        assert_eq!(
            serde_json::to_string(&ask).unwrap(),
            r#"{"type":"ask","id":1,"text":"What is my net worth?"}"#
        );
        assert_eq!(serde_json::to_string(&TuiMsg::Reset).unwrap(), r#"{"type":"reset"}"#);
        assert_eq!(serde_json::to_string(&TuiMsg::Quit).unwrap(), r#"{"type":"quit"}"#);
    }

    #[test]
    fn resolve_command_defaults() {
        // Only checks the default branch; env-var handling is trivially covered by `split_whitespace`.
        if std::env::var_os("ACCT_AGENT_CMD").is_none() {
            assert_eq!(resolve_command(), vec!["acct-agent".to_string(), "serve".to_string()]);
        }
    }
}

/// End-to-end protocol test against a fake agent script. Runs only when
/// `ACCT_TUI_FAKE_AGENT` points at an executable that speaks the protocol
/// (e.g. a small Python script), so plain `cargo test` stays hermetic.
#[cfg(test)]
mod fake_agent_tests {
    use super::*;

    fn wait_for(agent: &Agent, timeout: Duration) -> Option<AgentEvent> {
        agent.events.recv_timeout(timeout).ok()
    }

    #[test]
    fn round_trip_with_fake_agent() {
        let Ok(script) = std::env::var("ACCT_TUI_FAKE_AGENT") else {
            eprintln!("ACCT_TUI_FAKE_AGENT not set; skipping");
            return;
        };
        let argv: Vec<String> = script.split_whitespace().map(str::to_owned).collect();
        let mut agent = Agent::spawn(&argv).expect("spawn fake agent");
        let t = Duration::from_secs(5);

        // Collect until ready (stderr lines may interleave).
        let mut got_ready = false;
        while let Some(ev) = wait_for(&agent, t) {
            match ev {
                AgentEvent::Msg(AgentMsg::Ready { model, .. }) => {
                    assert_eq!(model, "fake:1b");
                    got_ready = true;
                    break;
                }
                AgentEvent::Stderr(_) => continue,
                other => panic!("unexpected before ready: {other:?}"),
            }
        }
        assert!(got_ready, "never received ready");

        agent
            .send(&TuiMsg::Ask {
                id: 7,
                text: "hello".into(),
            })
            .unwrap();
        let mut types = Vec::new();
        while let Some(ev) = wait_for(&agent, t) {
            match ev {
                AgentEvent::Msg(AgentMsg::Status { id, .. }) => {
                    assert_eq!(id, Some(7));
                    types.push("status");
                }
                AgentEvent::Msg(AgentMsg::ToolCall { tool, .. }) => {
                    assert_eq!(tool, "net_worth");
                    types.push("tool_call");
                }
                AgentEvent::Msg(AgentMsg::ToolResult { result, .. }) => {
                    assert_eq!(result.lines().count(), 3);
                    types.push("tool_result");
                }
                AgentEvent::Msg(AgentMsg::Answer { id, text }) => {
                    assert_eq!(id, Some(7));
                    assert_eq!(text, "echo: hello");
                    types.push("answer");
                    break;
                }
                AgentEvent::Stderr(_) => {}
                other => panic!("unexpected: {other:?}"),
            }
        }
        assert_eq!(types, ["status", "tool_call", "tool_result", "answer"]);

        agent.send(&TuiMsg::Reset).unwrap();
        agent.send(&TuiMsg::Quit).unwrap();
        let mut got_bye = false;
        let mut got_eof = false;
        while let Some(ev) = wait_for(&agent, t) {
            match ev {
                AgentEvent::Msg(AgentMsg::Bye) => got_bye = true,
                AgentEvent::Eof => {
                    got_eof = true;
                    break;
                }
                _ => {}
            }
        }
        assert!(got_bye && got_eof);
        agent.shutdown(Duration::from_secs(1));
    }
}
