//! Rendering. Everything here is a pure function of the [`App`] state.

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, Paragraph};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use crate::app::{App, Entry};

const SPINNER: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
const HINT: &str = "Enter send · ↑/↓ or PgUp/PgDn scroll · Tab tool output · F2 log · Ctrl-R reset · Ctrl-C/Esc quit";

pub fn draw(frame: &mut Frame, app: &mut App) {
    if let Some(err) = &app.spawn_error {
        draw_spawn_error(frame, app, &err.message);
        return;
    }

    let debug_height = if app.show_debug {
        (frame.area().height / 3).clamp(3, 15)
    } else {
        0
    };
    let [header, transcript, debug, input, hint] = Layout::vertical([
        Constraint::Length(1),
        Constraint::Min(1),
        Constraint::Length(debug_height),
        Constraint::Length(3),
        Constraint::Length(1),
    ])
    .areas(frame.area());

    draw_header(frame, app, header);
    draw_transcript(frame, app, transcript);
    if app.show_debug {
        draw_debug(frame, app, debug);
    }
    draw_input(frame, app, input);
    frame.render_widget(Paragraph::new(HINT).dim(), hint);
}

fn draw_header(frame: &mut Frame, app: &App, area: Rect) {
    let line = match &app.info {
        None if app.agent_gone => Line::from(vec![
            Span::styled(" acct-tui ", Style::default().bold().reversed()),
            Span::raw(" agent exited before it was ready").red(),
        ]),
        None => Line::from(vec![
            Span::styled(" acct-tui ", Style::default().bold().reversed()),
            Span::raw(format!(
                " {} starting agent… ({})",
                SPINNER[(app.tick as usize) % SPINNER.len()],
                app.command_string()
            ))
            .dim(),
        ]),
        Some(info) => {
            let mut spans = vec![
                Span::styled(" acct-tui ", Style::default().bold().reversed()),
                Span::raw(" model ").dim(),
                Span::raw(info.model.clone()).bold(),
                Span::raw("  journal ").dim(),
                Span::raw(info.journal.clone()).bold(),
            ];
            if !info.journal_exists {
                spans.push(Span::raw(" (missing)").red().bold());
            }
            if app.agent_gone {
                spans.push(Span::raw("  [agent exited]").red().bold());
            }
            Line::from(spans)
        }
    };
    frame.render_widget(Paragraph::new(line), area);
}

fn draw_transcript(frame: &mut Frame, app: &mut App, area: Rect) {
    let width = area.width.max(1) as usize;
    let mut lines = transcript_lines(app, width);

    if let Some(f) = &app.in_flight {
        lines.push(Line::from(vec![
            Span::raw(format!(
                "{} ",
                SPINNER[(app.tick as usize) % SPINNER.len()]
            ))
            .yellow(),
            Span::raw(format!("{}…", f.status)).yellow(),
        ]));
    }

    let height = area.height as usize;
    let max_scroll = lines.len().saturating_sub(height);
    // Clamp persistent scroll state so we never scroll past the top.
    app.scroll = app.scroll.min(max_scroll);
    let offset = max_scroll - app.scroll;
    let visible: Vec<Line> = lines.into_iter().skip(offset).take(height).collect();

    frame.render_widget(Paragraph::new(visible), area);

    if app.scroll > 0 && area.width > 12 {
        // Small "more below" marker so users know they're scrolled up.
        let marker = format!(" ↓ {} more ", app.scroll);
        let w = marker.width() as u16;
        let rect = Rect::new(area.right().saturating_sub(w), area.bottom() - 1, w, 1);
        frame.render_widget(Clear, rect);
        frame.render_widget(Paragraph::new(marker).reversed(), rect);
    }
}

fn draw_debug(frame: &mut Frame, app: &App, area: Rect) {
    let block = Block::default()
        .borders(Borders::TOP)
        .title(format!(
            " agent stderr (last {} lines, F2 to hide) ",
            app.stderr_lines.len()
        ))
        .dim();
    let inner = block.inner(area);
    frame.render_widget(block, area);
    let width = inner.width.max(1) as usize;
    let mut lines: Vec<Line> = Vec::new();
    for l in &app.stderr_lines {
        for w in wrap(l, width, 0) {
            lines.push(Line::from(w).dim());
        }
    }
    let height = inner.height as usize;
    let skip = lines.len().saturating_sub(height);
    let visible: Vec<Line> = lines.into_iter().skip(skip).collect();
    frame.render_widget(Paragraph::new(visible), inner);
}

fn draw_input(frame: &mut Frame, app: &App, area: Rect) {
    let (title, style) = if app.in_flight.is_some() {
        (" waiting for agent… (typing still allowed) ", Style::default().yellow())
    } else if app.agent_gone {
        (" agent not running ", Style::default().red())
    } else if app.info.is_none() {
        (" starting… ", Style::default().dim())
    } else {
        (" ask ", Style::default().cyan())
    };
    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(style)
        .title(title);
    let inner = block.inner(area);
    frame.render_widget(block, area);

    // Horizontal scroll so the cursor is always visible on a single line.
    let width = inner.width.max(1) as usize;
    let chars: Vec<char> = app.input.chars().collect();
    let cursor = app.cursor.min(chars.len());
    // Compute the char index at which to start so the cursor fits.
    let mut start = 0;
    loop {
        let shown: String = chars[start..cursor].iter().collect();
        if shown.width() < width || start >= cursor {
            break;
        }
        start += 1;
    }
    let text: String = chars[start..].iter().collect();
    let prefix_width: String = chars[start..cursor].iter().collect();
    frame.render_widget(Paragraph::new(text), inner);
    frame.set_cursor_position((
        inner.x + (prefix_width.width() as u16).min(inner.width.saturating_sub(1)),
        inner.y,
    ));
}

fn draw_spawn_error(frame: &mut Frame, app: &App, message: &str) {
    let area = frame.area();
    let width = area.width.saturating_sub(4).max(10) as usize;
    let mut lines: Vec<Line> = vec![
        Line::from(Span::raw("Could not start the accounting agent").red().bold()),
        Line::raw(""),
        Line::from(vec![
            Span::raw("command tried: ").dim(),
            Span::raw(app.command_string()).bold(),
        ]),
    ];
    for l in wrap(message, width, 0) {
        lines.push(Line::from(l).red());
    }
    lines.push(Line::raw(""));
    lines.push(Line::from("How to fix:").bold());
    for l in [
        "• Install the agent so `acct-agent` is on your PATH, e.g.:",
        "      uv tool install /path/to/accounting-agent",
        "  (or `pipx install`, or `uv sync` and use the venv below)",
        "• Or point ACCT_AGENT_CMD at any command that speaks the protocol:",
        "      ACCT_AGENT_CMD=\"uv run --project /path/to/accounting-agent acct-agent serve\" acct-tui",
        "      ACCT_AGENT_CMD=\"/path/to/.venv/bin/acct-agent serve\" acct-tui",
        "• Run acct-tui from the directory that contains your *.journal file.",
    ] {
        for w in wrap(l, width, 6) {
            lines.push(Line::raw(w));
        }
    }
    lines.push(Line::raw(""));
    lines.push(Line::from("Press q or Esc to quit.").dim());

    let block = Block::default().borders(Borders::ALL).title(" acct-tui ");
    let inner = block.inner(area);
    frame.render_widget(block, area);
    let inner = Rect {
        x: inner.x + 1,
        width: inner.width.saturating_sub(2),
        ..inner
    };
    frame.render_widget(Paragraph::new(lines), inner);
}

/// Build the styled, wrapped transcript lines for the given width.
fn transcript_lines(app: &App, width: usize) -> Vec<Line<'static>> {
    let user = Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD);
    let agent = Style::default().fg(Color::Green).add_modifier(Modifier::BOLD);
    let dim = Style::default().add_modifier(Modifier::DIM);
    let err = Style::default().fg(Color::Red);
    let sys = Style::default().fg(Color::Magenta).add_modifier(Modifier::ITALIC);

    let mut out = Vec::new();
    for entry in &app.entries {
        match entry {
            Entry::User(text) => {
                push_prefixed(&mut out, "you › ", user, text, Style::default(), width);
                out.push(Line::raw(""));
            }
            Entry::Agent(text) => {
                push_prefixed(&mut out, "agent › ", agent, text, Style::default(), width);
                out.push(Line::raw(""));
            }
            Entry::ToolCall { tool, args } => {
                let rendered = format!("⚙ {}", format_call(tool, args));
                for w in wrap(&rendered, width, 2) {
                    out.push(Line::styled(w, dim));
                }
            }
            Entry::ToolResult { result, .. } => {
                let n = result.lines().count();
                if app.show_tool_results {
                    out.push(Line::styled("  ↳", dim));
                    for l in result.lines() {
                        for w in wrap(&format!("    {l}"), width, 4) {
                            out.push(Line::styled(w, dim));
                        }
                    }
                } else {
                    let noun = if n == 1 { "line" } else { "lines" };
                    out.push(Line::styled(format!("  ↳ {n} {noun} (Tab to expand)"), dim));
                }
            }
            Entry::System(text) => {
                for w in wrap(&format!("— {text}"), width, 2) {
                    out.push(Line::styled(w, sys));
                }
            }
            Entry::Error(text) => {
                push_prefixed(&mut out, "error › ", err.bold(), text, err, width);
                out.push(Line::raw(""));
            }
        }
    }
    out
}

/// "prefix" on the first line, hanging indent of the prefix width on the rest.
fn push_prefixed(
    out: &mut Vec<Line<'static>>,
    prefix: &'static str,
    prefix_style: Style,
    text: &str,
    text_style: Style,
    width: usize,
) {
    let indent = prefix.width();
    let body_width = width.saturating_sub(indent).max(1);
    let mut first = true;
    for para in text.split('\n') {
        // wrap() with width=body_width and no indent; we add the prefix/indent ourselves.
        for chunk in wrap(para, body_width, 0) {
            if first {
                out.push(Line::from(vec![
                    Span::styled(prefix, prefix_style),
                    Span::styled(chunk, text_style),
                ]));
                first = false;
            } else {
                out.push(Line::from(vec![
                    Span::raw(" ".repeat(indent)),
                    Span::styled(chunk, text_style),
                ]));
            }
        }
    }
    if first {
        // Empty text: still show the prefix.
        out.push(Line::from(Span::styled(prefix, prefix_style)));
    }
}

/// Render `tool(k=v, ...)` with JSON-encoded values, e.g.
/// `spending_by_category(period="last month")`.
pub fn format_call(tool: &str, args: &serde_json::Map<String, serde_json::Value>) -> String {
    let parts: Vec<String> = args
        .iter()
        .map(|(k, v)| {
            let v = serde_json::to_string(v).unwrap_or_else(|_| "?".into());
            format!("{k}={v}")
        })
        .collect();
    format!("{tool}({})", parts.join(", "))
}

/// Word-wrap `text` (a single logical line) to `width` display columns.
/// Continuation lines are indented by `indent` spaces. Long words are hard-broken.
/// Always returns at least one line.
pub fn wrap(text: &str, width: usize, indent: usize) -> Vec<String> {
    let width = width.max(1);
    let indent = indent.min(width.saturating_sub(1));
    let pad = " ".repeat(indent);
    let mut lines: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut cur_w = 0usize;
    let avail = |lines: &Vec<String>| if lines.is_empty() { width } else { width - indent };

    let flush = |lines: &mut Vec<String>, cur: &mut String, cur_w: &mut usize| {
        let prefix = if lines.is_empty() { "" } else { pad.as_str() };
        lines.push(format!("{prefix}{}", cur.trim_end()));
        cur.clear();
        *cur_w = 0;
    };

    let mut at_line_start = true;
    for word in text.split(' ') {
        let ww = word.width();
        let sep = usize::from(!at_line_start);
        if cur_w + sep + ww <= avail(&lines) {
            if sep == 1 {
                cur.push(' ');
            }
            cur.push_str(word);
            cur_w += sep + ww;
            at_line_start = false;
            continue;
        }
        if !at_line_start {
            flush(&mut lines, &mut cur, &mut cur_w);
        }
        at_line_start = false;
        if ww <= avail(&lines) {
            cur.push_str(word);
            cur_w = ww;
            continue;
        }
        // Hard-break an over-long word char by char.
        for ch in word.chars() {
            let cw = unicode_width::UnicodeWidthChar::width(ch).unwrap_or(0);
            if cur_w + cw > avail(&lines) && !cur.is_empty() {
                flush(&mut lines, &mut cur, &mut cur_w);
            }
            cur.push(ch);
            cur_w += cw;
        }
    }
    if !cur.is_empty() || lines.is_empty() {
        flush(&mut lines, &mut cur, &mut cur_w);
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn wraps_words_with_hanging_indent() {
        let out = wrap("the quick brown fox jumps over", 10, 2);
        assert_eq!(out, vec!["the quick", "  brown", "  fox", "  jumps", "  over"]);
    }

    #[test]
    fn hard_breaks_long_words() {
        let out = wrap("abcdefghij", 4, 0);
        assert_eq!(out, vec!["abcd", "efgh", "ij"]);
    }

    #[test]
    fn preserves_leading_and_inner_spaces() {
        assert_eq!(wrap("    ASSETS  x", 40, 4), vec!["    ASSETS  x"]);
    }

    #[test]
    fn empty_gives_one_line() {
        assert_eq!(wrap("", 10, 0), vec![""]);
    }

    #[test]
    fn formats_tool_calls() {
        let mut args = serde_json::Map::new();
        assert_eq!(format_call("net_worth", &args), "net_worth()");
        args.insert("period".into(), json!("last month"));
        assert_eq!(
            format_call("spending_by_category", &args),
            r#"spending_by_category(period="last month")"#
        );
        args.insert("top".into(), json!(5));
        assert_eq!(
            format_call("spending_by_category", &args),
            r#"spending_by_category(period="last month", top=5)"#
        );
    }
}
