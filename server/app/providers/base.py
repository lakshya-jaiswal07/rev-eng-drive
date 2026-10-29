from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

# System prompt shared by the real-model providers (Gemini, OpenAI-compatible).
# Only questions that are not about the drive reach a model; routes, stops and
# ETAs are handled by RouteVoice itself.
SYSTEM = (
    "You are RouteVoice, an in-car voice assistant. Your answer is read aloud to "
    "someone who is driving, and they can interrupt you at any moment. Answer "
    "whatever they ask using your general knowledge. Lead with the direct answer "
    "and keep it short: one or two spoken sentences, unless they ask for more. Write "
    "plain text, as you would speak it: no markdown, lists, headings or tables.\n\n"
    "You cannot browse the web or see live data. For things that change - traffic, "
    "weather, prices, opening hours, schedules - say briefly that you have no live "
    "data and suggest checking a live source. Never name a specific business or "
    "place as current fact unless you are sure it exists; if unsure, say so. Never "
    "suggest the driver look at or type on a screen while driving.\n\n"
    "If you are resuming after an interruption, continue from where you stopped; do "
    "not restart or repeat what you already said. When session facts are provided, "
    "treat them as what the driver has told you so far and stay consistent with them."
)


@dataclass
class GenerationRequest:
    goal: str
    utterance: str
    constraints: list[str] = field(default_factory=list)
    context: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    resume_from: str | None = None
    modality: str = "text"
    # Set when the harness refused something the user asked for. The answer has
    # to say so rather than quietly pretending the request never happened.
    notice: str | None = None
    # Phase 2D: canonical session facts from BackspaceCore, keyed by fact key.
    # Providers use these to ground answers in what the user has actually said
    # across the conversation, not just the current utterance.
    facts: dict[str, object] = field(default_factory=dict)


class Provider(Protocol):
    name: str

    def plan(self, request: GenerationRequest) -> list[str]:
        """Short, human-readable plan steps shown in the UI while thinking."""

    def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        """Yield response chunks. Must be cancellable between chunks."""
