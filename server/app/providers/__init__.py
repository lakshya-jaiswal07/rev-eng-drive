from __future__ import annotations

from ..config import settings
from .base import GenerationRequest, Provider
from .gemini import GeminiProvider
from .offline import OfflineProvider
from .openai_compat import OpenAICompatProvider


def build_provider() -> Provider:
    if settings.use_gemini:
        return GeminiProvider()
    if settings.use_real_llm:
        return OpenAICompatProvider()
    return OfflineProvider()


__all__ = [
    "GenerationRequest",
    "Provider",
    "OfflineProvider",
    "OpenAICompatProvider",
    "GeminiProvider",
    "build_provider",
]
