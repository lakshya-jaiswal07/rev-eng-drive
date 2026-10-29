from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _strip_real_gemini_credentials():
    """Ensure tests run deterministically with the offline provider by default,
    avoiding live external API calls during testing unless explicitly
    configured via USE_REAL_GEMINI_TESTS.

    A one-time strip at collection time is not enough: any import partway
    through the suite that calls ``load_dotenv()`` could silently repopulate
    ``GEMINI_API_KEY``/``GOOGLE_API_KEY`` with real credentials, and a later
    test would then get a real provider and attempt a real network call.
    Stripping both keys before *and* after every test - not just once -
    means no test can ever observe a real key regardless of what an
    intervening import does to the environment.
    """
    if os.environ.get("USE_REAL_GEMINI_TESTS"):
        yield
        return
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("GOOGLE_API_KEY", None)
    yield
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("GOOGLE_API_KEY", None)
