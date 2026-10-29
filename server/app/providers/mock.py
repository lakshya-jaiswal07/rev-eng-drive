"""Deterministic local engine.

The demo has to run with no API key and no network. This engine composes a
grounded answer out of the retrieved evidence and emits it token by token, with
an ``await`` between tokens. Pacing - being *slow enough to interrupt* - is the
runtime's job, applied the same way to every provider (see
``AgentRuntime._paced``), so it is not repeated here.
"""

from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator

from ..retrieval import tokenize
from .base import GenerationRequest

_SENTENCE = re.compile(r"(?<=[.!?])\s+")

# What the demo agency's knowledge base is about. Without a model, a question
# that names none of this is off-topic: a passage it happens to share one word
# with ("what time is it" -> hotel check-in times) is not an answer to it.
_TRAVEL_WORDS = frozenset(tokenize(
    "flight flights fly flying airline airport plane cabin class economy business fare fares "
    "ticket seat baggage luggage bag bags kg checked change changing cancel cancelled "
    "cancellation refund refunds reschedule rebook booking book booked reservation hotel hotels "
    "room rooms stay property checkin checkout arrival departure depart delay delayed "
    "connection missed itinerary trip trips travel traveller travelling insurance claim "
    "expense expenses reimbursement reimburse diem allowance approval approve policy "
    "corporate desk contact support agency interject rate rates flex saver fee fees "
    "penalty compensation schedule schedules lounge meal visa passport"
))

OFFLINE_NOTE = (
    "I'm in offline demo mode, so I can only answer from the knowledge base of Interject "
    "Travel, a fictional demo agency"
)


def _on_topic(request: GenerationRequest) -> bool:
    """Whether the offline engine has any business answering from the demo knowledge base."""
    asked = set(tokenize(f"{request.utterance} {request.goal}"))
    if asked & _TRAVEL_WORDS:
        return True
    if not request.evidence:
        return False
    top = request.evidence[0]
    # otherwise only a passage that shares several of the question's words
    return len(asked & set(tokenize(f"{top['title']} {top['snippet']}"))) >= 2


def _relevant_sentences(text: str, query: str, limit: int = 2) -> list[str]:
    wanted = set(tokenize(query))
    scored: list[tuple[int, int, str]] = []
    for index, sentence in enumerate(_SENTENCE.split(" ".join(text.split()))):
        if len(sentence) < 25:
            continue
        overlap = len(wanted & set(tokenize(sentence)))
        if overlap:
            scored.append((overlap, -index, sentence.strip()))
    scored.sort(reverse=True)
    return [s for _, _, s in scored[:limit]]


class MockProvider:
    name = "local-deterministic"

    def plan(self, request: GenerationRequest) -> list[str]:
        steps = [f"Read the goal: {request.goal[:60]}"]
        if request.constraints:
            steps.append(f"Honour {len(request.constraints)} constraint(s): {', '.join(request.constraints)}")
        if request.facts:
            steps.append(f"Apply {len(request.facts)} known fact(s) from the session")
        if request.resume_from:
            steps.append("Reuse the checkpoint instead of restarting")
        steps.append("Ground the answer in retrieved passages")
        steps.append("Draft, then hand control back to the user")
        return steps

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        body = self._compose(request)
        for token in re.findall(r"\S+\s*|\n", body):
            await asyncio.sleep(0)  # cooperative: never hog the loop between tokens
            yield token

    # -- composition -----------------------------------------------------
    def _compose(self, request: GenerationRequest) -> str:
        parts: list[str] = []

        if request.resume_from:
            tail = request.resume_from.strip().split()
            tail_text = " ".join(tail[-12:])
            parts.append(
                f"Picking up where I stopped - I had got as far as \"...{tail_text}\". Continuing from there.\n\n"
            )

        if request.notice:
            parts.append(f"{request.notice}\n\n")

        if request.modality == "voice":
            parts.append("(heard via voice transcript) ")
        elif request.modality == "image":
            parts.append("(reading the attached image alongside your question) ")

        evidence = request.evidence
        if not request.resume_from and (not evidence or not _on_topic(request)):
            # No model is running: say so instead of reading out a passage that
            # merely shares a word with the question, or offering an answer
            # "from general knowledge" this engine does not have.
            parts.append(
                f"{OFFLINE_NOTE} - and I don't have anything in the corpus that covers "
                f"\"{request.utterance.strip()}\". Try asking about its flights, baggage, hotels, "
                "refunds or travel policy. General questions need the backend started with a "
                "Groq key (scripts/start-backend-groq.ps1)."
            )
            return "".join(parts)

        already_said = (request.resume_from or "").lower()

        if not request.resume_from:
            top = evidence[0]
            lead = _relevant_sentences(
                top["snippet"], request.utterance + " " + request.goal, limit=1
            )
            parts.append(
                f"Per Interject Travel's demo guide, on {top['title'].lower()}: {lead[0] if lead else top['snippet'][:160]}\n\n"
            )

        bullets: list[str] = []
        for item in evidence[:3]:
            topic = f"{request.goal} {request.utterance}"
            for sentence in _relevant_sentences(item["snippet"], topic, limit=1):
                # Don't repeat a point the interrupted half of the answer made.
                if sentence.lower()[:40] in already_said:
                    continue
                bullets.append(f"- {sentence}  [{item['doc_id']}]\n")

        if not bullets and request.resume_from:
            # Everything relevant was already said before the interruption, so
            # a resume would otherwise be nothing but preamble. Carry on with
            # the next passage instead of trailing off.
            for item in evidence:
                extra = _relevant_sentences(item["snippet"], request.goal, limit=1)
                for sentence in extra:
                    if sentence.lower()[:40] not in already_said:
                        bullets.append(f"- {sentence}  [{item['doc_id']}]\n")
                        break
                if bullets:
                    break

        parts.extend(bullets)

        if request.facts:
            fact_lines = "; ".join(f"{k}: {v}" for k, v in request.facts.items())
            parts.append(
                f"\nI am working with these facts from our conversation: {fact_lines}.\n"
            )

        if request.constraints:
            parts.append(
                f"\nI am holding these constraints from earlier in the session: "
                f"{'; '.join(request.constraints)}.\n"
            )

        parts.append(
            "\nWant me to narrow this down, or move on to the next part of the plan?"
        )
        return "".join(parts)
