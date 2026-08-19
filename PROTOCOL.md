# TUI ⇄ Agent protocol

The Rust TUI (`tui/`) spawns the Python agent as a child process and talks to
it over stdin/stdout using **newline-delimited JSON** (one object per line).
The child inherits the TUI's working directory, which is how the agent finds
the journal. Anything the agent writes to **stderr** is a log line and should
be ignored (or shown in a debug pane).

Spawn command: the value of `$ACCT_AGENT_CMD` if set (split on whitespace),
otherwise `acct-agent serve`.

## Agent → TUI

| type          | fields                                                        | meaning |
|---------------|---------------------------------------------------------------|---------|
| `ready`       | `model`, `journal`, `journal_exists` (bool)                    | sent once after startup |
| `status`      | `id`, `text`                                                  | progress text ("thinking…") |
| `tool_call`   | `id`, `tool`, `args` (object)                                 | the agent is calling a tool |
| `tool_result` | `id`, `tool`, `result` (string, may be multi-line)            | tool output |
| `answer`      | `id`, `text`                                                  | final assistant reply |
| `error`       | `id` (may be null), `text`                                    | something failed |
| `bye`         |                                                               | agent is exiting |

## TUI → Agent

| type    | fields              | meaning |
|---------|---------------------|---------|
| `ask`   | `id` (int), `text`  | user message; every response for it echoes the same `id` |
| `reset` |                     | clear conversation history |
| `quit`  |                     | shut down |

Example session:

```
← {"type":"ready","model":"gemma4:12b","journal":"/home/me/books/main.journal","journal_exists":true}
→ {"type":"ask","id":1,"text":"What is my net worth?"}
← {"type":"status","id":1,"text":"thinking"}
← {"type":"tool_call","id":1,"tool":"net_worth","args":{}}
← {"type":"tool_result","id":1,"tool":"net_worth","result":"ASSETS (USD value):\n ..."}
← {"type":"answer","id":1,"text":"Your net worth is $174,581.60."}
```
