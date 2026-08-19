"""The DSPy agent: a ReAct loop over the accounting tools.

The signature docstring below is the *seed* instruction. GEPA (see
`evals/optimize.py`) rewrites the instructions of the ReAct predictors for a
given model and saves them under `prompts/<model>.json`; `build_agent()`
loads that file automatically when it exists.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Callable

import dspy

from .config import ModelConfig, make_lm
from .ledger import Ledger
from .tools import AccountingTools

MAX_ITERS = 6


class AccountingQuestion(dspy.Signature):
    """You are a careful personal accounting assistant. You answer questions about the user's finances by calling the provided accounting tools and reporting exactly what they return.

Rules:
- Always call a tool before answering a question about balances, net worth, debt, spending, income, bitcoin, or transactions. Never guess or invent numbers.
- Pick the single most specific tool for the question (e.g. net_worth for net worth/equity, total_debt for debt, bitcoin_holdings for bitcoin, spending_by_category for what was spent on, find_unusual_transactions for suspicious activity).
- Copy numbers verbatim from tool output, including the currency symbol and cents.
- When done, finish immediately: give a short, direct answer in one or two sentences, stating the key figures.
- If the user asks to record a transaction, call add_transaction once with the details, then confirm what was recorded.
- If a tool returns an ERROR, tell the user plainly what went wrong."""

    today: str = dspy.InputField(desc="today's date, YYYY-MM-DD")
    history: dspy.History = dspy.InputField(desc="previous turns of this conversation")
    question: str = dspy.InputField(desc="the user's latest message")
    answer: str = dspy.OutputField(desc="direct, factual answer for the user")


class AccountingAgent(dspy.Module):
    def __init__(self, tools: list[Callable], max_iters: int = MAX_ITERS):
        super().__init__()
        self.react = dspy.ReAct(AccountingQuestion, tools=tools, max_iters=max_iters)

    def forward(self, question: str, today: str, history: dspy.History | None = None):
        history = history or dspy.History(messages=[])
        # Local models occasionally fall into a repetition loop (truncated at max_tokens)
        # or return an empty completion, which surfaces as AdapterParseError. Retry once
        # at a slightly higher temperature: that both breaks the loop and gives DSPy a
        # different cache key, so the bad response is not replayed.
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                if attempt == 0:
                    return self.react(question=question, today=today, history=history)
                lm = dspy.settings.lm
                retry_lm = lm.copy(temperature=max(0.3, lm.kwargs.get("temperature") or 0.0), rollout_id=attempt) if lm else lm
                with dspy.context(lm=retry_lm):
                    return self.react(question=question, today=today, history=history)
            except dspy.utils.exceptions.AdapterParseError as e:
                last_exc = e
        raise last_exc  # type: ignore[misc]


def build_agent(
    ledger: Ledger,
    cfg: ModelConfig | None = None,
    *,
    load_optimized: bool = True,
    max_iters: int = MAX_ITERS,
) -> AccountingAgent:
    cfg = cfg or ModelConfig()
    tools = AccountingTools(ledger).all_tools()
    agent = AccountingAgent(tools, max_iters=max_iters)
    path = cfg.optimized_prompt_path()
    if load_optimized and path.exists():
        agent.load(str(path))
    return agent


def configure_lm(cfg: ModelConfig | None = None) -> dspy.LM:
    lm = make_lm(cfg)
    dspy.configure(lm=lm)
    return lm


class Conversation:
    """Stateful chat wrapper: keeps history and exposes tool-call events."""

    def __init__(self, ledger: Ledger, cfg: ModelConfig | None = None, agent: AccountingAgent | None = None):
        self.ledger = ledger
        self.cfg = cfg or ModelConfig()
        self.agent = agent or build_agent(ledger, self.cfg)
        self.history = dspy.History(messages=[])

    def ask(self, question: str) -> dspy.Prediction:
        today = self.ledger.today.isoformat()
        pred = self.agent(question=question, today=today, history=self.history)
        msgs = list(self.history.messages) + [{"today": today, "question": question, "answer": pred.answer}]
        # keep the last few turns only; small models have small contexts
        self.history = dspy.History(messages=msgs[-6:])
        return pred

    def reset(self) -> None:
        self.history = dspy.History(messages=[])


def trajectory_events(pred: dspy.Prediction) -> list[dict]:
    """Flatten a ReAct trajectory into [{thought, tool, args, result}] for display."""
    traj = getattr(pred, "trajectory", {}) or {}
    events = []
    i = 0
    while f"tool_name_{i}" in traj:
        events.append(
            {
                "thought": traj.get(f"thought_{i}", ""),
                "tool": traj.get(f"tool_name_{i}"),
                "args": traj.get(f"tool_args_{i}", {}),
                "result": traj.get(f"observation_{i}", ""),
            }
        )
        i += 1
    return events
