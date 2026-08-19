"""JSON-lines server used by the Rust TUI (see PROTOCOL.md)."""

from __future__ import annotations

import functools
import inspect
import json
import sys
import threading
import traceback

from .agent import AccountingAgent, Conversation, configure_lm
from .config import ModelConfig
from .ledger import Ledger
from .tools import AccountingTools

_out_lock = threading.Lock()


def emit(**msg) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def log(text: str) -> None:
    sys.stderr.write(text.rstrip() + "\n")
    sys.stderr.flush()


def _wrap_tool(fn, state: dict):
    """Wrap a tool so every call streams tool_call / tool_result events."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            bound = inspect.signature(fn).bind_partial(*args, **kwargs)
            shown = dict(bound.arguments)
        except TypeError:
            shown = {"args": args, "kwargs": kwargs}
        emit(type="tool_call", id=state.get("id"), tool=fn.__name__, args=shown)
        result = fn(*args, **kwargs)
        emit(type="tool_result", id=state.get("id"), tool=fn.__name__, result=str(result))
        return result

    return wrapper


def serve(journal: str | None = None) -> int:
    cfg = ModelConfig()
    state: dict = {"id": None}
    try:
        configure_lm(cfg)
        ledger = Ledger(journal)
        tools = [_wrap_tool(t, state) for t in AccountingTools(ledger).all_tools()]
        agent = AccountingAgent(tools)
        path = cfg.optimized_prompt_path()
        if path.exists():
            agent.load(str(path))
            log(f"loaded optimized prompts from {path}")
        convo = Conversation(ledger, cfg, agent=agent)
    except Exception as e:  # noqa: BLE001
        emit(type="error", id=None, text=f"failed to start agent: {e}")
        log(traceback.format_exc())
        return 1

    emit(type="ready", model=cfg.model, journal=str(ledger.journal), journal_exists=ledger.journal.exists())

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            emit(type="error", id=None, text=f"bad JSON: {raw[:80]}")
            continue
        kind = msg.get("type")
        if kind == "quit":
            break
        if kind == "reset":
            convo.reset()
            continue
        if kind == "ask":
            state["id"] = msg.get("id")
            text = (msg.get("text") or "").strip()
            if not text:
                emit(type="error", id=state["id"], text="empty message")
                continue
            emit(type="status", id=state["id"], text="thinking")
            try:
                pred = convo.ask(text)
                emit(type="answer", id=state["id"], text=pred.answer)
            except Exception as e:  # noqa: BLE001
                log(traceback.format_exc())
                emit(type="error", id=state["id"], text=f"{type(e).__name__}: {e}")
            continue
        emit(type="error", id=None, text=f"unknown message type: {kind}")
    emit(type="bye")
    return 0
