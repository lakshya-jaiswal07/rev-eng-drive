from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _strip_real_gemini_credentials():
    """Ensure tests run deterministically with the mock provider by default,
    avoiding live external API calls during testing unless explicitly
    configured via USE_REAL_GEMINI_TESTS.

    A one-time strip at collection time is not enough: some later import
    partway through the suite - notably ``lk_agent_tool.py`` (an official
    Full-Duplex-Bench file this project does not modify) - has its own
    unconditional ``load_dotenv()`` call and can silently repopulate
    ``GEMINI_API_KEY``/``GOOGLE_API_KEY`` with real credentials from
    ``.env.local`` the moment it is imported (e.g. transitively, via
    ``app.fdb.runner``). Once that happens, every later test that builds a
    real ``AgentRuntime``/``GeminiProvider`` would unexpectedly get a real
    provider instead of ``MockProvider`` and attempt a real network call.
    Stripping both keys before *and* after every test - not just once -
    means no test can ever observe a real key regardless of what an
    intervening import (ours or a third party's) does to the environment.
    """
    if os.environ.get("USE_REAL_GEMINI_TESTS"):
        yield
        return
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("GOOGLE_API_KEY", None)
    yield
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("GOOGLE_API_KEY", None)
