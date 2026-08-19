//! acct-tui: a terminal chat UI for the local accounting agent.
//!
//! Spawns `acct-agent serve` (or `$ACCT_AGENT_CMD`) as a child process and
//! talks newline-delimited JSON over its stdin/stdout.

mod agent;
mod app;
mod ui;

use std::io;
use std::time::Duration;

use anyhow::Result;
use crossterm::event::{self, DisableBracketedPaste, EnableBracketedPaste, Event};
use crossterm::execute;
use ratatui::DefaultTerminal;

use crate::agent::Agent;
use crate::app::{App, Outgoing, SpawnError};

const TICK: Duration = Duration::from_millis(50);
const QUIT_GRACE: Duration = Duration::from_secs(1);

fn main() -> Result<()> {
    let command = agent::resolve_command();
    let mut app = App::new(command.clone());

    // Spawn before touching the terminal so a spawn error can be shown cleanly.
    let mut agent = match Agent::spawn(&command) {
        Ok(a) => Some(a),
        Err(e) => {
            app.spawn_error = Some(SpawnError {
                message: format!("{e:#}"),
            });
            None
        }
    };

    install_panic_hook();
    let mut terminal = ratatui::init();
    // Bracketed paste is optional; ignore terminals that don't support it.
    let _ = execute!(io::stdout(), EnableBracketedPaste);

    let result = run(&mut terminal, &mut app, &mut agent);

    let _ = execute!(io::stdout(), DisableBracketedPaste);
    ratatui::restore();

    if let Some(agent) = agent.take() {
        agent.shutdown(QUIT_GRACE);
    }
    result
}

fn run(terminal: &mut DefaultTerminal, app: &mut App, agent: &mut Option<Agent>) -> Result<()> {
    loop {
        // 1. Agent → app.
        if let Some(a) = agent.as_mut() {
            for ev in a.drain_events() {
                app.on_agent_event(ev);
            }
        }

        // 2. Draw.
        terminal.draw(|f| ui::draw(f, app))?;

        // 3. Terminal → app (poll doubles as the ~50 ms tick).
        if event::poll(TICK)? {
            match event::read()? {
                Event::Key(key) => {
                    if let Some(out) = app.on_key(key) {
                        handle_outgoing(app, agent, out);
                    }
                }
                Event::Paste(text) => app.on_paste(&text),
                _ => {}
            }
        } else {
            app.on_tick();
        }

        if app.should_quit {
            return Ok(());
        }
    }
}

fn handle_outgoing(app: &mut App, agent: &mut Option<Agent>, out: Outgoing) {
    match out {
        Outgoing::Quit => app.should_quit = true,
        Outgoing::Send(msg) => {
            let Some(a) = agent.as_mut() else { return };
            if let Err(e) = a.send(&msg) {
                app.agent_gone = true;
                app.in_flight = None;
                app.entries
                    .push(app::Entry::Error(format!("could not talk to agent: {e:#}")));
            }
        }
    }
}

/// Restore the terminal before printing a panic, so the message is readable
/// and the shell isn't left in raw mode.
fn install_panic_hook() {
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let _ = execute!(io::stdout(), DisableBracketedPaste);
        ratatui::restore();
        prev(info);
    }));
}
