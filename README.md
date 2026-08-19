# accounting-agent

A personal accounting agent that runs **entirely on your machine**. You chat
with it in a terminal UI; under the hood it manages an
[hledger](https://hledger.org) plain-text journal in the directory you start
it from. The model never sees hledger — it only sees a small set of
plain-English accounting tools (net worth, debt, spending by category, bitcoin
holdings, unusual transactions, add transaction, …).

* **Model**: `gemma4:12b` via [Ollama](https://ollama.com) (any DSPy/LiteLLM
  provider string can be swapped in via env vars).
* **Harness**: [DSPy](https://dspy.ai) `ReAct` over custom tools, with prompts
  **optimized per model by GEPA** against a test suite so a small local model
  behaves reliably.
* **TUI**: Rust ([ratatui](https://ratatui.rs)) — talks to the Python agent
  over newline-delimited JSON ([PROTOCOL.md](PROTOCOL.md)).
* **Ledger**: hledger CLI, wrapped in `src/acct_agent/ledger.py`. Supports
  multiple commodities (USD + BTC), assets (checking, savings, bitcoin, real
  estate), liabilities (credit cards, auto/mortgage loans), income, expenses.

```
┌───────────────┐  JSON lines   ┌───────────────────────┐   subprocess   ┌─────────┐
│  acct-tui     │ ───────────▶  │ acct-agent serve      │ ─────────────▶ │ hledger │
│  (Rust)       │ ◀───────────  │  DSPy ReAct + tools   │ ◀───────────── │  CLI    │
└───────────────┘               │  ollama_chat/gemma4   │                └─────────┘
                                └───────────────────────┘
```

## Prerequisites

* [hledger](https://hledger.org/install.html) (`brew install hledger`)
* [Ollama](https://ollama.com) with the model pulled: `ollama pull gemma4:12b`
* [uv](https://docs.astral.sh/uv/) for Python, and a Rust toolchain for the TUI

## Install

```sh
git clone <this repo> && cd accounting-agent
uv sync                                  # python deps (dspy, ...)
uv tool install --editable .             # puts `acct-agent` on your PATH
cargo install --path tui                 # puts `acct-tui` on your PATH
```

## Use

```sh
cd ~/finances            # any directory containing a *.journal (or empty — main.journal is created)
acct-tui                 # full TUI
# or, without the TUI:
acct-agent chat          # plain REPL
acct-agent ask "what is my net worth?"
```

Try it on the bundled sample ledger:

```sh
cd examples && acct-tui
```

Journal discovery: `$LEDGER_FILE` → `main.journal` / `ledger.journal` in the
current directory → first `*.journal` there → `main.journal` (created on the
first write). Override with `acct-agent -f path.journal …`.

Example questions the agent handles:

* What is my total net worth / equity?
* What is my current debt? How much is left on the mortgage?
* What categories did I spend the most on last month?
* How much Bitcoin do I have, and what is it worth?
* Are there any transactions that look suspicious?
* Record that I spent $45.20 at Trader Joe's on groceries with my visa today.

## Configuration

| env var                 | default                  | meaning |
|-------------------------|--------------------------|---------|
| `ACCT_AGENT_PROVIDER`   | `ollama`                 | `ollama`, or any LiteLLM provider prefix (`openai`, `anthropic`, …) |
| `ACCT_AGENT_MODEL`      | `gemma4:12b`             | model name |
| `ACCT_AGENT_API_BASE`   | `http://localhost:11434` | for ollama; optional otherwise |
| `ACCT_AGENT_API_KEY`    |                          | for hosted providers |
| `ACCT_AGENT_NUM_CTX`    | `16384`                  | ollama context window |
| `ACCT_AGENT_TEMPERATURE`| `0.0`                    | |
| `LEDGER_FILE`           |                          | explicit journal path |
| `ACCT_AGENT_CMD`        | `acct-agent serve`       | command the TUI spawns |

## Tools the model can call

`list_accounts`, `account_balance`, `net_worth`, `total_debt`,
`bitcoin_holdings`, `spending_by_category`, `income_summary`,
`search_transactions`, `recent_transactions`, `find_unusual_transactions`, `monthly_summary`,
`add_transaction`, `current_date` — see `src/acct_agent/tools.py`. Try one
directly (no model): `acct-agent tool spending_by_category period="last month"`.

## Evaluating and optimizing for a model

The eval suite (`src/acct_agent/evals/dataset.py`, 61 cases split
train/val/test) runs each question against a fresh copy of
`examples/sample.journal` with "today" pinned to 2026‑08‑18, and scores the
answer on: required figures present, no wrong figures, right tool used, journal
side-effects for write requests, and brevity. The metric returns written
feedback, which GEPA uses to rewrite the prompts.

```sh
acct-agent eval --no-optimized          # baseline with the hand-written seed prompt
acct-agent optimize --budget light      # GEPA → src/acct_agent/prompts/<model>.json
acct-agent eval --split test            # held-out score with the optimized prompt
```

`optimize` reflects with the same local model by default; pass
`--reflection-model openai/gpt-5` (or any LiteLLM string, with the matching
API key env var) to reflect with a stronger model — the *agent* still runs on
the local model. Optimized prompts are keyed by model slug and are loaded
automatically by `serve`/`chat`/`ask`.

To support a new model: set `ACCT_AGENT_MODEL`, run `eval --no-optimized`,
then `optimize`, then `eval --split test`.

Notes:

* GEPA's own "best" pick resolves validation ties to the seed prompt; we
  instead ship the candidate with the best validation score, breaking ties by
  lineage depth (each accepted candidate beat its parent on a training
  minibatch). `acct-agent optimize --reselect` re-applies that choice from the
  last run's logs without re-running GEPA.
* The optimized ReAct instructions embed the tool descriptions. If you change
  a tool's name, arguments or docstring, re-run `optimize` (or delete the
  prompt file to fall back to the seed).
* Local Gemma 12B answers in ~40–90 s per question on an Apple-silicon laptop;
  a full 61-case eval takes about an hour, a 150-rollout GEPA run about 2.5 h.
  DSPy caches LM calls on disk, so re-running unchanged cases is instant.

### Results so far (`gemma4:12b`, 2026-08-18)

| run                                   | score | perfect |
|---------------------------------------|-------|---------|
| seed prompt, all 61 cases             | 98.7% | 60/61   |
| GEPA candidate 2, validation (19)     | 100%  | 19/19   |
| GEPA candidate 2, held-out test (18)  | 96.2% | 17/18   |
| `gemma4:e4b-it-qat`, seed, all 61     | 95.1% | 54/61   |
| `gemma4:e4b-it-qat`, GEPA cand. 3, val (19)  | 99.6% (seed 96.8%) | |
| `gemma4:e4b-it-qat`, GEPA cand. 3, test (18) | 96.7% | 17/18 (seed: 17/18) |
| `gemma4:e2b-it-qat`, seed, all 61     | 95.8% | 55/61   |

Speed on an M1 Pro (16 GB), warm, thinking disabled: `12b` ≈ 45–55 s per
question (gen 14 tok/s), `e4b-it-qat` ≈ 20 s (43 tok/s), `e2b-it-qat` ≈ 10–16 s
(75 tok/s). The small models' misses are mostly not quoting the supporting
figures and one percentage misread. A GEPA round on e4b (≈30 min) produced a
prompt with an explicit per-tool routing table and an "aggregate via
search_transactions" rule; it ships in `prompts/ollama__gemma4-e4b-it-qat.json`
and is used automatically with `ACCT_AGENT_MODEL=gemma4:e4b-it-qat`. On the
small held-out set it ties the seed (17/18); the gain shows on validation.

**Fast setup:** `export ACCT_AGENT_MODEL=gemma4:e4b-it-qat` (≈20 s/question)
— add it to your shell profile to make it the default.

The GEPA rewrite mainly added explicit *verbatim-numbers* and *completeness*
rules (list every item a tool returns) and a rewritten answer-extraction
prompt; the seed's one hard failure was a repetition loop on a two-step
question, which is now also covered by an automatic retry at temperature 0.3.
The remaining test miss is the agent computing an average itself
($2,110.43) instead of calling `monthly_summary` ($2,110.42) — a good
candidate for the next optimization round (move a similar case into `train`).

## Development

```sh
uv run pytest                 # offline tests (hledger wrapper + tools, no model needed)
cd tui && cargo test && cargo build --release
```

Layout:

```
src/acct_agent/
  ledger.py     hledger CLI wrapper (the only hledger-aware code)
  tools.py      accounting tools exposed to the model
  agent.py      DSPy signature + ReAct module + conversation state
  config.py     model/provider config, optimized-prompt lookup
  server.py     JSON-lines server for the TUI
  cli.py        acct-agent chat|serve|ask|eval|optimize|tool
  evals/        dataset, metric, harness, run, optimize (GEPA)
  prompts/      GEPA-optimized prompts per model
tui/            Rust ratatui client
examples/       sample.journal used for demos and evals
```
