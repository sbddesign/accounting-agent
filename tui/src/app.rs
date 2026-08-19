//! Application state and key handling. This module does no I/O; the main loop
//! feeds it terminal keys and agent events and acts on the [`Outgoing`] values
//! it returns.

use std::collections::VecDeque;

use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use serde_json::{Map, Value};

use crate::agent::{AgentEvent, AgentMsg, TuiMsg};

/// How many stderr lines to keep for the debug pane.
pub const STDERR_CAPACITY: usize = 200;

/// One item in the transcript.
#[derive(Debug, Clone, PartialEq)]
pub enum Entry {
    User(String),
    Agent(String),
    ToolCall { tool: String, args: Map<String, Value> },
    ToolResult { tool: String, result: String },
    System(String),
    Error(String),
}

/// Info from the agent's `ready` message, shown in the header.
#[derive(Debug, Clone, PartialEq)]
pub struct AgentInfo {
    pub model: String,
    pub journal: String,
    pub journal_exists: bool,
}

/// A request that has been sent but not yet answered.
#[derive(Debug, Clone, PartialEq)]
pub struct InFlight {
    pub id: u64,
    pub status: String,
}

/// Something the main loop must do as a result of a key press.
#[derive(Debug, Clone, PartialEq)]
pub enum Outgoing {
    Send(TuiMsg),
    Quit,
}

#[derive(Debug)]
pub struct App {
    pub entries: Vec<Entry>,
    pub input: String,
    /// Cursor position in `input`, in chars.
    pub cursor: usize,
    pub next_id: u64,
    pub in_flight: Option<InFlight>,
    pub info: Option<AgentInfo>,
    /// Lines scrolled up from the bottom of the transcript (0 = pinned to bottom).
    pub scroll: usize,
    pub show_tool_results: bool,
    pub show_debug: bool,
    pub stderr_lines: VecDeque<String>,
    pub tick: u64,
    /// True once the child's stdout has closed.
    pub agent_gone: bool,
    /// Set when the agent could not be spawned; the UI shows an error screen.
    pub spawn_error: Option<SpawnError>,
    /// The command that was (attempted to be) run.
    pub command: Vec<String>,
    pub should_quit: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SpawnError {
    pub message: String,
}

impl App {
    pub fn new(command: Vec<String>) -> Self {
        Self {
            entries: Vec::new(),
            input: String::new(),
            cursor: 0,
            next_id: 1,
            in_flight: None,
            info: None,
            scroll: 0,
            show_tool_results: false,
            show_debug: false,
            stderr_lines: VecDeque::with_capacity(STDERR_CAPACITY),
            tick: 0,
            agent_gone: false,
            spawn_error: None,
            command,
            should_quit: false,
        }
    }

    pub fn command_string(&self) -> String {
        self.command.join(" ")
    }

    /// Whether the user may submit a new message right now.
    pub fn can_submit(&self) -> bool {
        self.in_flight.is_none()
            && self.info.is_some()
            && !self.agent_gone
            && self.spawn_error.is_none()
            && !self.input.trim().is_empty()
    }

    pub fn on_tick(&mut self) {
        self.tick = self.tick.wrapping_add(1);
    }

    /// Apply an event coming from the agent process.
    pub fn on_agent_event(&mut self, ev: AgentEvent) {
        match ev {
            AgentEvent::Msg(msg) => self.on_agent_msg(msg),
            AgentEvent::Malformed(line) => self.push_stderr(format!("[malformed stdout] {line}")),
            AgentEvent::Stderr(line) => self.push_stderr(line),
            AgentEvent::Eof => {
                if !self.agent_gone {
                    self.agent_gone = true;
                    self.in_flight = None;
                    self.entries.push(Entry::System(
                        "agent process exited (press F2 for its log, Ctrl-C to quit)".into(),
                    ));
                }
            }
        }
    }

    fn on_agent_msg(&mut self, msg: AgentMsg) {
        match msg {
            AgentMsg::Ready {
                model,
                journal,
                journal_exists,
            } => {
                if !journal_exists {
                    self.entries.push(Entry::System(format!(
                        "warning: journal not found at {journal}"
                    )));
                }
                self.info = Some(AgentInfo {
                    model,
                    journal,
                    journal_exists,
                });
            }
            AgentMsg::Status { text, .. } => {
                if let Some(f) = &mut self.in_flight {
                    f.status = text;
                }
            }
            AgentMsg::ToolCall { tool, args, .. } => {
                self.entries.push(Entry::ToolCall { tool, args });
            }
            AgentMsg::ToolResult { tool, result, .. } => {
                self.entries.push(Entry::ToolResult { tool, result });
            }
            AgentMsg::Answer { text, .. } => {
                self.in_flight = None;
                self.entries.push(Entry::Agent(text));
            }
            AgentMsg::Error { text, .. } => {
                self.in_flight = None;
                self.entries.push(Entry::Error(text));
            }
            AgentMsg::Bye => {
                self.entries.push(Entry::System("agent said goodbye".into()));
            }
        }
    }

    fn push_stderr(&mut self, line: String) {
        if self.stderr_lines.len() >= STDERR_CAPACITY {
            self.stderr_lines.pop_front();
        }
        self.stderr_lines.push_back(line);
    }

    /// Handle a terminal key press. Returns what (if anything) the main loop should do.
    pub fn on_key(&mut self, key: KeyEvent) -> Option<Outgoing> {
        if key.kind == KeyEventKind::Release {
            return None;
        }
        if self.spawn_error.is_some() {
            return self.on_key_error_screen(key);
        }
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        match (key.code, ctrl) {
            (KeyCode::Char('c'), true) | (KeyCode::Esc, _) => {
                self.should_quit = true;
                Some(Outgoing::Quit)
            }
            (KeyCode::Char('r'), true) => {
                self.entries.clear();
                self.in_flight = None;
                self.scroll = 0;
                self.entries.push(Entry::System("conversation reset".into()));
                Some(Outgoing::Send(TuiMsg::Reset))
            }
            (KeyCode::Char('l'), true) | (KeyCode::F(2), _) => {
                self.show_debug = !self.show_debug;
                None
            }
            (KeyCode::Tab, _) | (KeyCode::BackTab, _) => {
                self.show_tool_results = !self.show_tool_results;
                None
            }
            (KeyCode::Enter, _) => self.submit(),
            (KeyCode::Up, _) => {
                self.scroll = self.scroll.saturating_add(1);
                None
            }
            (KeyCode::Down, _) => {
                self.scroll = self.scroll.saturating_sub(1);
                None
            }
            (KeyCode::PageUp, _) => {
                self.scroll = self.scroll.saturating_add(10);
                None
            }
            (KeyCode::PageDown, _) => {
                self.scroll = self.scroll.saturating_sub(10);
                None
            }
            (KeyCode::Left, _) | (KeyCode::Char('b'), true) => {
                self.cursor = self.cursor.saturating_sub(1);
                None
            }
            (KeyCode::Right, _) | (KeyCode::Char('f'), true) => {
                self.cursor = (self.cursor + 1).min(self.input_len());
                None
            }
            (KeyCode::Home, _) | (KeyCode::Char('a'), true) => {
                self.cursor = 0;
                None
            }
            (KeyCode::End, _) | (KeyCode::Char('e'), true) => {
                self.cursor = self.input_len();
                None
            }
            (KeyCode::Char('u'), true) => {
                self.input.clear();
                self.cursor = 0;
                None
            }
            (KeyCode::Char('w'), true) => {
                self.delete_word_back();
                None
            }
            (KeyCode::Backspace, _) => {
                if self.cursor > 0 {
                    let idx = self.byte_index(self.cursor - 1);
                    self.input.remove(idx);
                    self.cursor -= 1;
                }
                None
            }
            (KeyCode::Delete, _) => {
                if self.cursor < self.input_len() {
                    let idx = self.byte_index(self.cursor);
                    self.input.remove(idx);
                }
                None
            }
            (KeyCode::Char(c), _) => {
                self.insert_str(&c.to_string());
                None
            }
            _ => None,
        }
    }

    fn on_key_error_screen(&mut self, key: KeyEvent) -> Option<Outgoing> {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        match key.code {
            KeyCode::Char('q') | KeyCode::Esc => {
                self.should_quit = true;
                Some(Outgoing::Quit)
            }
            KeyCode::Char('c') if ctrl => {
                self.should_quit = true;
                Some(Outgoing::Quit)
            }
            _ => None,
        }
    }

    /// Insert pasted text at the cursor (newlines become spaces: single-line input).
    pub fn on_paste(&mut self, text: &str) {
        let cleaned: String = text
            .chars()
            .map(|c| if c == '\n' || c == '\r' { ' ' } else { c })
            .collect();
        self.insert_str(&cleaned);
    }

    fn submit(&mut self) -> Option<Outgoing> {
        if !self.can_submit() {
            return None;
        }
        let text = std::mem::take(&mut self.input).trim().to_owned();
        self.cursor = 0;
        let id = self.next_id;
        self.next_id += 1;
        self.entries.push(Entry::User(text.clone()));
        self.in_flight = Some(InFlight {
            id,
            status: "sending".into(),
        });
        self.scroll = 0;
        Some(Outgoing::Send(TuiMsg::Ask { id, text }))
    }

    fn insert_str(&mut self, s: &str) {
        let idx = self.byte_index(self.cursor);
        self.input.insert_str(idx, s);
        self.cursor += s.chars().count();
    }

    fn delete_word_back(&mut self) {
        let chars: Vec<char> = self.input.chars().collect();
        let mut i = self.cursor;
        while i > 0 && chars[i - 1].is_whitespace() {
            i -= 1;
        }
        while i > 0 && !chars[i - 1].is_whitespace() {
            i -= 1;
        }
        let start = self.byte_index(i);
        let end = self.byte_index(self.cursor);
        self.input.replace_range(start..end, "");
        self.cursor = i;
    }

    fn input_len(&self) -> usize {
        self.input.chars().count()
    }

    fn byte_index(&self, char_pos: usize) -> usize {
        self.input
            .char_indices()
            .nth(char_pos)
            .map(|(i, _)| i)
            .unwrap_or(self.input.len())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }
    fn ctrl(c: char) -> KeyEvent {
        KeyEvent::new(KeyCode::Char(c), KeyModifiers::CONTROL)
    }
    fn ready_app() -> App {
        let mut app = App::new(vec!["fake".into()]);
        app.on_agent_event(AgentEvent::Msg(AgentMsg::Ready {
            model: "m".into(),
            journal: "j".into(),
            journal_exists: true,
        }));
        app
    }
    fn type_str(app: &mut App, s: &str) {
        for c in s.chars() {
            app.on_key(key(KeyCode::Char(c)));
        }
    }

    #[test]
    fn cannot_submit_before_ready() {
        let mut app = App::new(vec!["fake".into()]);
        type_str(&mut app, "hi");
        assert_eq!(app.on_key(key(KeyCode::Enter)), None);
        assert_eq!(app.entries, vec![]);
    }

    #[test]
    fn submit_increments_ids_and_blocks_until_answer() {
        let mut app = ready_app();
        type_str(&mut app, "net worth?");
        let out = app.on_key(key(KeyCode::Enter));
        assert_eq!(
            out,
            Some(Outgoing::Send(TuiMsg::Ask {
                id: 1,
                text: "net worth?".into()
            }))
        );
        assert_eq!(app.input, "");
        assert!(app.in_flight.is_some());

        // Typing is allowed, submitting is not, while in flight.
        type_str(&mut app, "again");
        assert_eq!(app.on_key(key(KeyCode::Enter)), None);

        app.on_agent_event(AgentEvent::Msg(AgentMsg::Status {
            id: Some(1),
            text: "thinking".into(),
        }));
        assert_eq!(app.in_flight.as_ref().unwrap().status, "thinking");

        app.on_agent_event(AgentEvent::Msg(AgentMsg::Answer {
            id: Some(1),
            text: "lots".into(),
        }));
        assert!(app.in_flight.is_none());
        assert_eq!(app.entries.last(), Some(&Entry::Agent("lots".into())));

        let out = app.on_key(key(KeyCode::Enter));
        assert!(matches!(out, Some(Outgoing::Send(TuiMsg::Ask { id: 2, .. }))));
    }

    #[test]
    fn error_clears_in_flight() {
        let mut app = ready_app();
        type_str(&mut app, "x");
        app.on_key(key(KeyCode::Enter));
        app.on_agent_event(AgentEvent::Msg(AgentMsg::Error {
            id: None,
            text: "nope".into(),
        }));
        assert!(app.in_flight.is_none());
        assert_eq!(app.entries.last(), Some(&Entry::Error("nope".into())));
    }

    #[test]
    fn ctrl_r_resets() {
        let mut app = ready_app();
        type_str(&mut app, "x");
        app.on_key(key(KeyCode::Enter));
        assert_eq!(app.on_key(ctrl('r')), Some(Outgoing::Send(TuiMsg::Reset)));
        assert_eq!(app.entries, vec![Entry::System("conversation reset".into())]);
        assert!(app.in_flight.is_none());
    }

    #[test]
    fn quit_keys() {
        let mut app = ready_app();
        assert_eq!(app.on_key(ctrl('c')), Some(Outgoing::Quit));
        let mut app = ready_app();
        assert_eq!(app.on_key(key(KeyCode::Esc)), Some(Outgoing::Quit));
        assert!(app.should_quit);
    }

    #[test]
    fn toggles() {
        let mut app = ready_app();
        assert!(!app.show_tool_results);
        app.on_key(key(KeyCode::Tab));
        assert!(app.show_tool_results);
        app.on_key(key(KeyCode::F(2)));
        assert!(app.show_debug);
        app.on_key(ctrl('l'));
        assert!(!app.show_debug);
    }

    #[test]
    fn editing_is_char_aware() {
        let mut app = ready_app();
        type_str(&mut app, "héllo");
        app.on_key(key(KeyCode::Left));
        app.on_key(key(KeyCode::Left));
        app.on_key(key(KeyCode::Backspace));
        assert_eq!(app.input, "hélo");
        app.on_key(key(KeyCode::Home));
        app.on_key(key(KeyCode::Delete));
        assert_eq!(app.input, "élo");
        app.on_key(key(KeyCode::End));
        app.on_key(ctrl('w'));
        assert_eq!(app.input, "");
        app.on_paste("a\nb");
        assert_eq!(app.input, "a b");
    }

    #[test]
    fn stderr_ring_buffer_is_bounded() {
        let mut app = ready_app();
        for i in 0..(STDERR_CAPACITY + 5) {
            app.on_agent_event(AgentEvent::Stderr(format!("line {i}")));
        }
        assert_eq!(app.stderr_lines.len(), STDERR_CAPACITY);
        assert_eq!(app.stderr_lines.front().unwrap(), "line 5");
    }

    #[test]
    fn eof_marks_agent_gone() {
        let mut app = ready_app();
        type_str(&mut app, "x");
        app.on_key(key(KeyCode::Enter));
        app.on_agent_event(AgentEvent::Eof);
        assert!(app.agent_gone);
        assert!(app.in_flight.is_none());
        assert!(!app.can_submit());
    }
}
