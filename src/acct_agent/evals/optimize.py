"""`acct-agent optimize`: use GEPA to evolve the agent's instructions for the
configured model, then save them to prompts/<model>.json.

GEPA reads the textual feedback from `metric` for each failing example,
reflects with a (usually stronger) reflection LM, and proposes new
instructions for the ReAct predictors. Only the prompts change: the tools,
signature fields and program structure stay the same, so a saved prompt file
can be loaded by `build_agent()` for any run with the same model slug.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import dspy

from ..agent import configure_lm
from ..config import ModelConfig, make_lm
from .dataset import get_split
from .harness import EvalProgram
from .metric import metric


def optimize(budget: str = "light", reflection_model: str | None = None, threads: int = 1) -> int:
    cfg = ModelConfig()
    lm = configure_lm(cfg)
    if reflection_model:
        reflection_lm = dspy.LM(reflection_model, temperature=1.0, max_tokens=8000)
    else:
        # reflect with the same local model but a higher temperature so it explores
        reflection_lm = make_lm(cfg)
        reflection_lm.kwargs["temperature"] = 1.0
        reflection_lm.kwargs["max_tokens"] = 8000
        if cfg.provider == "ollama":
            reflection_lm.kwargs["num_ctx"] = max(cfg.num_ctx, 32768)  # reflection prompts carry whole trajectories

    program = EvalProgram(cfg, load_optimized=False)
    train, val = get_split("train"), get_split("val")
    print(f"model={cfg.lm_name}  train={len(train)}  val={len(val)}  budget={budget}  reflection={reflection_model or cfg.lm_name}")

    kwargs: dict = dict(
        metric=metric,
        reflection_lm=reflection_lm,
        num_threads=threads,
        track_stats=True,
        log_dir=str(cfg.optimized_prompt_path().with_suffix("")) + "-gepa-logs",
    )
    if budget.isdigit():
        kwargs["max_metric_calls"] = int(budget)
    else:
        kwargs["auto"] = budget
    gepa = dspy.GEPA(**kwargs)
    optimized = gepa.compile(program, trainset=train, valset=val)
    return save_best_candidate(optimized.agent, Path(kwargs["log_dir"]), cfg.optimized_prompt_path(), len(val))


def pick_candidate(log_dir: Path, n_val: int) -> tuple[int, dict, list[float]]:
    """Choose the candidate to ship from GEPA's saved state.

    GEPA's own `best_idx` is the arg-max of validation score, which resolves
    ties to the *seed* (index 0). Every non-seed candidate was only admitted
    because it beat its parent on a training minibatch, so when several tie on
    the validation set we prefer the one with the deepest lineage (most
    accumulated fixes), then the most recently discovered.
    """
    state = pickle.load(open(log_dir / "gepa_state.bin", "rb"))
    cands = state["program_candidates"]
    subs = state["prog_candidate_val_subscores"]
    parents = state["parent_program_for_candidate"]
    n_val = max(n_val, max((len(d) for d in subs), default=0))
    scores = [sum(d.values()) / n_val if n_val else 0.0 for d in subs]

    def depth(i: int) -> int:
        d = 0
        while parents[i] and parents[i][0] is not None:
            i = parents[i][0]
            d += 1
        return d

    best = max(range(len(cands)), key=lambda i: (round(scores[i], 6), depth(i), i))
    return best, cands[best], scores


def save_best_candidate(agent, log_dir: Path, out: Path, n_val: int) -> int:
    idx, cand, scores = pick_candidate(log_dir, n_val)
    print(f"\ncandidate val scores: {[round(x, 3) for x in scores]}  -> shipping candidate {idx}")
    for name, pred in agent.named_predictors():
        key = f"agent.{name}"
        if key in cand:
            pred.signature = pred.signature.with_instructions(cand[key])
    out.parent.mkdir(parents=True, exist_ok=True)
    agent.save(str(out))
    print(f"saved optimized prompts to {out}")
    for name, pred in agent.named_predictors():
        print(f"\n=== {name} ===\n{pred.signature.instructions}")
    return 0


def reselect(n_val: int | None = None) -> int:
    """`acct-agent optimize --reselect`: re-pick the shipped candidate from an
    earlier run's logs without re-running GEPA."""
    from .dataset import get_split

    cfg = ModelConfig()
    log_dir = Path(str(cfg.optimized_prompt_path().with_suffix("")) + "-gepa-logs")
    program = EvalProgram(cfg, load_optimized=False)
    return save_best_candidate(program.agent, log_dir, cfg.optimized_prompt_path(), n_val or len(get_split("val")))
