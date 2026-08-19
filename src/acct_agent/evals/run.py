"""`acct-agent eval`: score the agent on the dataset and print a report."""

from __future__ import annotations

import json
import time
from pathlib import Path

import dspy

from ..agent import configure_lm
from ..config import ModelConfig
from .dataset import get_split
from .harness import EvalProgram
from .metric import _tools_called, score_example

REPORTS_DIR = Path(__file__).resolve().parents[3] / "eval-reports"


def run_eval(split: str = "all", limit: int | None = None, verbose: bool = False, no_optimized: bool = False, threads: int = 1) -> int:
    cfg = ModelConfig()
    configure_lm(cfg)
    program = EvalProgram(cfg, load_optimized=not no_optimized)
    examples = get_split(split)
    if limit:
        examples = examples[:limit]
    print(f"model={cfg.lm_name}  split={split}  cases={len(examples)}  optimized_prompt={'no' if no_optimized else cfg.optimized_prompt_path().exists()}")

    results = []
    t0 = time.time()

    def one(ex):
        t = time.time()
        pred = program(question=ex.question, today=ex.today)
        s, fb = score_example(ex, pred, getattr(pred, "journal_text", None))
        return dict(id=ex.id, split=ex.split, question=ex.question, answer=pred.answer, score=s, feedback=fb,
                    tools=_tools_called(pred), seconds=round(time.time() - t, 1))

    if threads > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(threads) as pool:
            results = list(pool.map(one, examples))
    else:
        for ex in examples:
            r = one(ex)
            results.append(r)
            mark = "✓" if r["score"] >= 0.99 else ("~" if r["score"] >= 0.6 else "✗")
            print(f"{mark} [{r['score']:.2f}] {r['question']}")
            print(f"      → {r['answer'][:200]}")
            if verbose or r["score"] < 0.99:
                print(f"      tools={r['tools']}  {r['feedback']}")

    avg = sum(r["score"] for r in results) / max(1, len(results))
    perfect = sum(1 for r in results if r["score"] >= 0.99)
    print(f"\nSCORE {avg*100:.1f}%   perfect {perfect}/{len(results)}   {time.time()-t0:.0f}s")

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"{cfg.slug}-{split}-{'seed' if no_optimized else 'current'}.json"
    out.write_text(json.dumps(dict(model=cfg.lm_name, split=split, average=avg, results=results), indent=2))
    print(f"report: {out}")
    return 0
