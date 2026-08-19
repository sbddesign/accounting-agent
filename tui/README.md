# acct-tui

A terminal chat UI for the local accounting agent (`acct-agent`). It spawns the
agent as a child process and talks to it over stdin/stdout using the
newline-delimited JSON protocol described in [`../PROTOCOL.md`](../PROTOCOL.md).

## Build

```sh
cd tui
cargo build --release
```

The binary is at `target/release/acct-tui`. Copy it somewhere on your `PATH`
if you like (`cp target/release/acct-tui ~/.local/bin/`).

## Run

`acct-tui` must be started from a directory that contains your `*.journal`
file — the working directory is inherited by the agent, which is how it finds
the journal:

```sh
cd ~/books          # contains main.journal
acct-tui
```

By default it runs `acct-agent serve`, so `acct-agent` needs to be on your
`PATH` (for example `uv tool install /path/to/accounting-agent`).

### Overriding the agent command

Set `ACCT_AGENT_CMD` to run any command that speaks the protocol; it is split
on whitespace:

```sh
ACCT_AGENT_CMD="uv run --project /path/to/accounting-agent acct-agent serve" acct-tui
ACCT_AGENT_CMD="/path/to/accounting-agent/.venv/bin/acct-agent serve" acct-tui
```

If the agent cannot be started, the TUI shows an error screen with the command
it tried and how to fix it (press `q` or `Esc` to quit).

## Keys

| Key                | Action                                              |
|--------------------|-----------------------------------------------------|
| `Enter`            | send the message                                    |
| `↑` / `↓`          | scroll transcript one line                          |
| `PgUp` / `PgDn`    | scroll transcript ten lines                         |
| `Tab`              | expand / collapse tool results (global toggle)      |
| `F2` or `Ctrl-L`   | show / hide the agent's stderr log (last 200 lines) |
| `Ctrl-R`           | reset the conversation                              |
| `Ctrl-C` / `Esc`   | quit (sends `quit`, waits ~1s, then kills the agent)|
| `←`/`→`, `Home`/`End`, `Ctrl-A`/`Ctrl-E` | move the cursor in the input   |
| `Ctrl-U` / `Ctrl-W`| clear the input / delete the previous word          |

While a request is in flight a spinner and the agent's latest status text are
shown; you can keep typing but cannot submit another message until the answer
(or an error) arrives.

## Development

```sh
cargo test                     # unit tests (protocol parsing, app state, wrapping)
ACCT_TUI_FAKE_AGENT="python3 /path/to/fake_agent.py" cargo test   # also runs a round-trip test against a fake agent
cargo clippy --all-targets
```
