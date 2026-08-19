"""Command line entry point: `acct-agent <command>`."""

from __future__ import annotations

import argparse
import sys


def _cmd_chat(args) -> int:
    """Minimal REPL (no TUI) — handy for debugging."""
    from .agent import Conversation, configure_lm, trajectory_events
    from .config import ModelConfig
    from .ledger import Ledger

    cfg = ModelConfig()
    configure_lm(cfg)
    ledger = Ledger(args.journal)
    print(f"model: {cfg.lm_name}   journal: {ledger.journal}{'' if ledger.journal.exists() else ' (will be created)'}")
    convo = Conversation(ledger, cfg)
    while True:
        try:
            q = input("\nyou › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q in {"/quit", "/exit"}:
            return 0
        if q == "/reset":
            convo.reset()
            print("(conversation reset)")
            continue
        try:
            pred = convo.ask(q)
        except Exception as e:  # noqa: BLE001
            print(f"error: {e}")
            continue
        for ev in trajectory_events(pred):
            if ev["tool"] != "finish":
                print(f"  ⚙ {ev['tool']}({', '.join(f'{k}={v!r}' for k, v in (ev['args'] or {}).items())})")
                if args.verbose:
                    print("    " + str(ev["result"]).replace("\n", "\n    "))
        print(f"\nagent › {pred.answer}")


def _cmd_serve(args) -> int:
    from .server import serve

    return serve(args.journal)


def _cmd_ask(args) -> int:
    from .agent import Conversation, configure_lm, trajectory_events
    from .config import ModelConfig
    from .ledger import Ledger

    cfg = ModelConfig()
    configure_lm(cfg)
    convo = Conversation(Ledger(args.journal), cfg)
    pred = convo.ask(" ".join(args.question))
    if args.verbose:
        for ev in trajectory_events(pred):
            print(f"  ⚙ {ev['tool']} {ev['args']}", file=sys.stderr)
    print(pred.answer)
    return 0


def _cmd_eval(args) -> int:
    from .evals.run import run_eval

    return run_eval(split=args.split, limit=args.limit, verbose=args.verbose, no_optimized=args.no_optimized, threads=args.threads)


def _cmd_optimize(args) -> int:
    from .evals.optimize import optimize, reselect

    if args.reselect:
        return reselect()
    return optimize(budget=args.budget, reflection_model=args.reflection_model, threads=args.threads)


def _cmd_tools(args) -> int:
    """Run a single tool directly (no model). Useful for debugging tools."""
    from .ledger import Ledger
    from .tools import AccountingTools

    tools = AccountingTools(Ledger(args.journal))
    fn = getattr(tools, args.name, None)
    if fn is None or args.name.startswith("_") or args.name == "all_tools":
        print("available tools: " + ", ".join(t.__name__ for t in tools.all_tools()))
        return 1
    kwargs = dict(kv.split("=", 1) for kv in args.kwargs)
    print(fn(**kwargs))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="acct-agent", description="Local accounting agent over an hledger journal.")
    p.add_argument("-f", "--journal", help="journal file (default: $LEDGER_FILE or *.journal in the current directory)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("chat", help="simple REPL in the terminal")
    s.add_argument("-v", "--verbose", action="store_true", help="show tool outputs")
    s.set_defaults(fn=_cmd_chat)

    s = sub.add_parser("serve", help="JSON-lines server for the TUI (see PROTOCOL.md)")
    s.set_defaults(fn=_cmd_serve)

    s = sub.add_parser("ask", help="ask one question and exit")
    s.add_argument("question", nargs="+")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=_cmd_ask)

    s = sub.add_parser("eval", help="run the evaluation suite against the configured model")
    s.add_argument("--split", default="all", choices=["train", "val", "test", "all"])
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--threads", type=int, default=1)
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--no-optimized", action="store_true", help="ignore saved optimized prompts (evaluate the seed prompt)")
    s.set_defaults(fn=_cmd_eval)

    s = sub.add_parser("optimize", help="run GEPA to optimize prompts for the configured model")
    s.add_argument("--budget", default="light", help="GEPA auto budget: light | medium | heavy, or an integer of metric calls")
    s.add_argument("--reflection-model", default=None, help="LM string for GEPA's reflection model (default: same as the agent)")
    s.add_argument("--threads", type=int, default=1)
    s.add_argument("--reselect", action="store_true", help="don't run GEPA; re-pick the best candidate from the last run's logs")
    s.set_defaults(fn=_cmd_optimize)

    s = sub.add_parser("tool", help="call one accounting tool directly (no model)")
    s.add_argument("name")
    s.add_argument("kwargs", nargs="*", help="key=value arguments")
    s.set_defaults(fn=_cmd_tools)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
