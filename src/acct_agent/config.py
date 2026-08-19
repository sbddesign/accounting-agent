"""Model + runtime configuration.

The MVP talks to Ollama, but everything model-specific goes through
`make_lm()` so other providers can be plugged in later (any LiteLLM-style
provider string DSPy understands works: "openai/...", "anthropic/...", ...).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import dspy

PACKAGE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = PACKAGE_DIR / "prompts"

DEFAULT_MODEL = "gemma4:12b"
DEFAULT_PROVIDER = "ollama"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


@dataclass
class ModelConfig:
    provider: str = field(default_factory=lambda: os.environ.get("ACCT_AGENT_PROVIDER", DEFAULT_PROVIDER))
    model: str = field(default_factory=lambda: os.environ.get("ACCT_AGENT_MODEL", DEFAULT_MODEL))
    api_base: str | None = field(default_factory=lambda: os.environ.get("ACCT_AGENT_API_BASE"))
    api_key: str | None = field(default_factory=lambda: os.environ.get("ACCT_AGENT_API_KEY"))
    temperature: float = float(os.environ.get("ACCT_AGENT_TEMPERATURE", "0.0"))
    max_tokens: int = int(os.environ.get("ACCT_AGENT_MAX_TOKENS", "4000"))
    num_ctx: int = int(os.environ.get("ACCT_AGENT_NUM_CTX", "16384"))
    # Ollama "thinking" models (Gemma 4, Qwen 3, ...) emit hidden reasoning tokens on every
    # call by default — ~10x more generated tokens for a tool call. The ReAct loop already
    # has an explicit thought field, so thinking is off unless ACCT_AGENT_THINK=1.
    think: bool = os.environ.get("ACCT_AGENT_THINK", "0").lower() in ("1", "true", "yes")

    @property
    def lm_name(self) -> str:
        if self.provider == "ollama":
            return f"ollama_chat/{self.model}"
        return f"{self.provider}/{self.model}"

    @property
    def slug(self) -> str:
        """Filesystem-safe name used to store optimized prompts per model."""
        return f"{self.provider}__{self.model}".replace("/", "_").replace(":", "-")

    def optimized_prompt_path(self) -> Path:
        return PROMPTS_DIR / f"{self.slug}.json"


def make_lm(cfg: ModelConfig | None = None) -> dspy.LM:
    cfg = cfg or ModelConfig()
    kwargs: dict = dict(temperature=cfg.temperature, max_tokens=cfg.max_tokens)
    if cfg.provider == "ollama":
        kwargs["api_base"] = cfg.api_base or DEFAULT_OLLAMA_URL
        kwargs["api_key"] = cfg.api_key or ""
        kwargs["num_ctx"] = cfg.num_ctx
        kwargs["think"] = cfg.think
    else:
        if cfg.api_base:
            kwargs["api_base"] = cfg.api_base
        if cfg.api_key:
            kwargs["api_key"] = cfg.api_key
    return dspy.LM(cfg.lm_name, **kwargs)
