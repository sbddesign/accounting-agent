"""Eval harness: runs the agent on a fresh copy of the fixture journal per
example, so write cases cannot pollute each other, and attaches the journal
text to the prediction for the metric."""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
import threading
from pathlib import Path

import dspy

from ..agent import AccountingAgent, MAX_ITERS
from ..config import ModelConfig
from ..ledger import Ledger
from ..tools import AccountingTools
from .dataset import TODAY

FIXTURE = Path(__file__).resolve().parents[3] / "examples" / "sample.journal"


class ThreadLocalLedger(Ledger):
    """A Ledger whose journal path is per-thread, so parallel evals are isolated."""

    def __init__(self, today: dt.date):
        super().__init__(FIXTURE, today=today)
        self._local = threading.local()

    @property
    def journal(self) -> Path:  # type: ignore[override]
        return getattr(self._local, "path", FIXTURE)

    @journal.setter
    def journal(self, value):  # the base __init__ assigns this; ignore
        pass

    def use(self, path: Path) -> None:
        self._local.path = path


class EvalProgram(dspy.Module):
    """Wraps AccountingAgent so each forward() runs on a scratch journal copy."""

    def __init__(self, cfg: ModelConfig | None = None, load_optimized: bool = True, max_iters: int = MAX_ITERS):
        super().__init__()
        self.cfg = cfg or ModelConfig()
        self.ledger = ThreadLocalLedger(today=dt.date.fromisoformat(TODAY))
        self.tools = AccountingTools(self.ledger).all_tools()
        self.agent = AccountingAgent(self.tools, max_iters=max_iters)
        path = self.cfg.optimized_prompt_path()
        if load_optimized and path.exists():
            self.agent.load(str(path))
        self._tmpdir = Path(tempfile.mkdtemp(prefix="acct-eval-"))
        self._counter = 0
        self._lock = threading.Lock()

    def forward(self, question: str, today: str = TODAY):
        with self._lock:
            self._counter += 1
            scratch = self._tmpdir / f"case-{self._counter}-{threading.get_ident()}.journal"
        shutil.copy(FIXTURE, scratch)
        self.ledger.use(scratch)
        try:
            pred = self.agent(question=question, today=today, history=dspy.History(messages=[]))
        except Exception as e:  # noqa: BLE001 — a crash is a zero-score prediction, not an eval abort
            pred = dspy.Prediction(answer=f"[agent error: {type(e).__name__}: {e}]", trajectory={})
        pred.journal_text = scratch.read_text(encoding="utf-8")
        return pred
