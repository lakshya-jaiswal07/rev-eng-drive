"""The provider used when no model key is set.

RouteVoice answers everything about the drive itself; this provider only marks
that no model is connected, so questions outside driving get the honest
offline reply in ``drive/runtime.py`` instead of a made-up answer.
"""

from __future__ import annotations

from typing import AsyncIterator

from .base import GenerationRequest


class OfflineProvider:
    name = "local-deterministic"

    def plan(self, request: GenerationRequest) -> list[str]:
        return []

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        return
        yield  # an async generator that yields nothing
