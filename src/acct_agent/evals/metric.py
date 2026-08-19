"""Scoring for eval cases. Returns a score in [0, 1] plus written feedback,
which is what GEPA uses to reflect on and rewrite the prompts."""

from __future__ import annotations

import re

import dspy


def _norm(s: str) -> str:
    return re.sub(r"[,\s]+", " ", str(s or "").lower()).replace(" ", "")


def _contains(answer: str, needle: str) -> bool:
    a, n = _norm(answer), _norm(needle)
    if n in a:
        return True
    # "$1,000" is an acceptable rendering of "1,000.00"
    if n.endswith(".00"):
        stem = n[:-3]
        return re.search(re.escape(stem) + r"(?![\d.,])", a) is not None
    return False


def _tools_called(pred) -> list[str]:
    traj = getattr(pred, "trajectory", {}) or {}
    names = []
    i = 0
    while f"tool_name_{i}" in traj:
        names.append(traj[f"tool_name_{i}"])
        i += 1
    return names


def score_example(example, pred, journal_text: str | None = None) -> tuple[float, str]:
    answer = getattr(pred, "answer", "") or ""
    tools = _tools_called(pred)
    real_tools = [t for t in tools if t != "finish"]
    fb: list[str] = []
    score = 0.0
    weight_total = 0.0

    # 1. required facts (dominant)
    expect = list(example.expect or [])
    if expect:
        hits = [e for e in expect if _contains(answer, e)]
        missing = [e for e in expect if e not in hits]
        frac = len(hits) / len(expect)
        score += 0.6 * frac
        weight_total += 0.6
        if missing:
            fb.append(f"The answer is missing these required facts: {missing}. Copy the exact figures from the tool output.")
    anyof = list(getattr(example, "anyof", []) or [])
    if anyof:
        ok = any(_contains(answer, a) for a in anyof)
        score += 0.6 if ok else 0.0
        weight_total += 0.6
        if not ok:
            fb.append(f"The answer should have said one of {anyof} (e.g. that nothing was found).")
    if not expect and not anyof:
        weight_total += 0.6
        score += 0.6

    # 2. forbidden content
    reject = list(example.reject or [])
    weight_total += 0.1
    bad = [r for r in reject if _contains(answer, r)]
    if bad:
        fb.append(f"The answer wrongly contains {bad}, which is not correct for the question's time period.")
    else:
        score += 0.1

    # 3. tool usage
    weight_total += 0.15
    if not real_tools and getattr(example, "no_tool_ok", False):
        score += 0.15
    elif not real_tools:
        fb.append("No tool was called. Every finance question must be answered by calling a tool; never guess.")
    elif example.tool and not (set([example.tool] if isinstance(example.tool, str) else example.tool) & set(real_tools)):
        score += 0.07
        fb.append(f"Expected the `{example.tool}` tool to be used (tools called: {real_tools}).")
    else:
        score += 0.15
    if len(real_tools) > 3:
        fb.append(f"Too many tool calls ({len(real_tools)}); the question needs at most one or two. Call the most specific tool once, then finish.")
        score -= 0.05

    # 4. journal effects (write cases)
    jexp = list(getattr(example, "journal_expect", []) or [])
    if jexp:
        weight_total += 0.15
        if journal_text is None:
            fb.append("Could not verify the journal.")
        else:
            miss = [j for j in jexp if _norm(j) not in _norm(journal_text)]
            if miss:
                fb.append(f"After the request the journal should contain {jexp} but is missing {miss}. Use add_transaction with the correct date, amount, from_account and to_account.")
                score += 0.15 * (1 - len(miss) / len(jexp))
            else:
                score += 0.15
    else:
        weight_total += 0.15
        if "add_transaction" in real_tools:
            fb.append("add_transaction was called although the user did not ask to record anything.")
        else:
            score += 0.15

    # 5. brevity
    if len(answer) > 600:
        fb.append(f"Answer is too long ({len(answer)} chars); keep it to one or two sentences.")
        score -= 0.05
    if not answer.strip():
        fb.append("The answer is empty.")

    final = max(0.0, min(1.0, score / weight_total)) if weight_total else 0.0
    if not fb:
        fb.append("Correct: the answer contains the right figures, used the right tool, and is concise.")
    return final, " ".join(fb)


def metric(example, pred, trace=None, pred_name=None, pred_trace=None):
    journal_text = getattr(pred, "journal_text", None)
    s, fb = score_example(example, pred, journal_text)
    return dspy.Prediction(score=s, feedback=fb)
