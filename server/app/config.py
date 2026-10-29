"""Runtime configuration.

Everything has a working default so the demo runs with zero setup. Set
``LLM_API_KEY`` to swap the deterministic local engine for a real model.

This module deliberately does not load a ``.env`` file. The repository-root
``.env`` is the LiveKit worker's credential file (see ``.env.example``);
loading it here, where the web backend imports it too, silently switched the
browser agent from the offline engine to Gemini whenever that file held a
``GEMINI_API_KEY``. Entry points that need it (``app.livekit_worker``,
``app.fdb.runner``) load it themselves before importing this module.
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


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    # reached and NVIDIA answers instead. Unset: the primary alone, as before.
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
    gemini_live_model: str = field(
        default_factory=lambda: os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live")
    )

    # LiveKit configuration
    livekit_url: str | None = field(default_factory=lambda: os.environ.get("LIVEKIT_URL"))
    livekit_api_key: str | None = field(default_factory=lambda: os.environ.get("LIVEKIT_API_KEY"))
    livekit_api_secret: str | None = field(default_factory=lambda: os.environ.get("LIVEKIT_API_SECRET"))

    # --- timing ---------------------------------------------------------
    # Token emission delay for the local engine. Slow enough that a human can
    # actually barge in mid-sentence, which is the whole point of the demo.
    token_delay_ms: int = field(default_factory=lambda: _env_int("TOKEN_DELAY_MS", 22))
    plan_step_ms: int = field(default_factory=lambda: _env_int("PLAN_STEP_MS", 90))
    retrieval_latency_ms: int = field(default_factory=lambda: _env_int("RETRIEVAL_LATENCY_MS", 260))
    tool_latency_ms: int = field(default_factory=lambda: _env_int("TOOL_LATENCY_MS", 180))

    # --- speculation ----------------------------------------------------
    speculation_enabled: bool = field(default_factory=lambda: _env_bool("SPECULATION", True))
    speculation_debounce_ms: int = field(default_factory=lambda: _env_int("SPEC_DEBOUNCE_MS", 110))
    speculation_min_chars: int = field(default_factory=lambda: _env_int("SPEC_MIN_CHARS", 12))
    # Deliberately loose. Scoring only has to pick a plausible candidate; the
    # runtime then verifies the speculated passages against the finished
    # utterance before using them, so a generous threshold costs a discarded
    # prefetch rather than a wrong answer.
    speculation_match_threshold: float = field(
        default_factory=lambda: _env_float("SPEC_MATCH_THRESHOLD", 0.45)
    )

    # --- harness --------------------------------------------------------
    tool_timeout_ms: int = field(default_factory=lambda: _env_int("TOOL_TIMEOUT_MS", 4000))
    max_tool_calls_per_turn: int = field(default_factory=lambda: _env_int("MAX_TOOL_CALLS", 4))
    strict_harness: bool = field(default_factory=lambda: _env_bool("STRICT_HARNESS", True))

    # --- accounts -------------------------------------------------------
    # The assistant and Drive need a logged-in user (app/auth.py); 0 opens them.
    auth_required: bool = field(default_factory=lambda: _env_bool("AUTH_REQUIRED", True))

    # --- session --------------------------------------------------------
    # Session-scoped memory only; no cross-session user profile is ever built.
    session_ttl_s: int = field(default_factory=lambda: _env_int("SESSION_TTL_S", 3600))
    max_turns_in_context: int = field(default_factory=lambda: _env_int("MAX_TURNS", 12))

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
