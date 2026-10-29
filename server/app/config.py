"""Runtime configuration.

Everything has a working default, so Drive runs with zero setup. Set
``LLM_API_KEY`` (or ``GEMINI_API_KEY``) to let a real model answer questions
that are not about driving; without one those get an honest offline reply.

This module deliberately does not load a ``.env`` file: keys come from the
environment of the process that starts the server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default


_UNSET = object()


@dataclass(frozen=True)
class Settings:
    # --- provider -------------------------------------------------------
    llm_api_key: Any = _UNSET
    llm_base_url: str = field(
        default_factory=lambda: os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    )
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "gpt-4o-mini"))
    # A spoken answer should be short; the cap is a backstop for when a model
    # ignores the prompt (0 disables it).
    llm_max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 900))
    # Reasoning models (gpt-oss) think before the first word; "low" keeps the
    # silence before an answer short, which matters more in voice than depth.
    llm_reasoning_effort: str = field(
        default_factory=lambda: os.environ.get("LLM_REASONING_EFFORT", "low")
    )
    # Optional backup model (another OpenAI-compatible endpoint), asked when the
    # primary fails before sending any text - e.g. Groq's free rate limit is
    # reached and NVIDIA answers instead. Unset: the primary alone.
    llm_fallback_api_key: str = field(default_factory=lambda: os.environ.get("LLM_FALLBACK_API_KEY", ""))
    llm_fallback_base_url: str = field(
        default_factory=lambda: os.environ.get("LLM_FALLBACK_BASE_URL", "https://integrate.api.nvidia.com/v1")
    )
    llm_fallback_model: str = field(
        default_factory=lambda: os.environ.get("LLM_FALLBACK_MODEL", "openai/gpt-oss-20b")
    )

    # Gemini configuration
    gemini_api_key: Any = _UNSET
    gemini_model: str = field(
        default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    )

    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    )

    def __getattribute__(self, name: str) -> Any:
        val = super().__getattribute__(name)
        if name == "gemini_api_key" and val is _UNSET:
            return os.environ.get("GEMINI_API_KEY")
        if name == "llm_api_key" and val is _UNSET:
            return os.environ.get("LLM_API_KEY")
        return val

    @property
    def use_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def use_real_llm(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
