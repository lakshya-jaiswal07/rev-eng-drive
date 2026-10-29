"""Real Gemini provider using Google GenAI SDK.

Enabled by setting ``GEMINI_API_KEY`` (and optionally ``GEMINI_MODEL``).
Speaks directly to Google's Gemini models via the official ``google-genai`` SDK.
Streaming yields chunks asynchronously, and each yielded chunk is a cancellation
point, so interruption/cancellation behaves cleanly with the AgentRuntime.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import TYPE_CHECKING, Any, AsyncIterator

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from ..config import settings
from .base import SYSTEM, GenerationRequest


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any = None,
    ) -> None:
        self.api_key = api_key or (settings.gemini_api_key if settings is not None else None) or os.environ.get("GEMINI_API_KEY")
        self.model = model or (settings.gemini_model if settings is not None else None) or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if genai is None:
            raise RuntimeError(
                "google-genai package is not installed. Install it with: pip install google-genai"
            )
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        self._client = genai.Client(api_key=self.api_key)
        return self._client

    def plan(self, request: GenerationRequest) -> list[str]:
        steps = [f"Read the goal: {request.goal[:60]}"]
        if request.constraints:
            steps.append(f"Honour constraints: {', '.join(request.constraints)}")
        if request.facts:
            steps.append(f"Ground in {len(request.facts)} session fact(s)")
        steps.append(f"Call Gemini ({self.model}) with {len(request.evidence)} grounded passage(s)")
        steps.append("Stream the answer, staying interruptible")
        return steps

    def _build_contents(self, request: GenerationRequest) -> list[Any]:
        if types is None:
            raise RuntimeError(
                "google-genai package is not installed. Install it with: pip install google-genai"
            )

        contents: list[Any] = []

        def append_turn(role: str, text: str) -> None:
            if not text.strip():
                return
            if contents and contents[-1].role == role:
                existing_parts = list(contents[-1].parts)
                existing_parts.append(types.Part.from_text(text=text))
                contents[-1] = types.Content(role=role, parts=existing_parts)
            else:
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=text)],
                    )
                )

        # Map conversation context turns to Gemini roles (user / model)
        for item in request.context:
            role = "model" if item.get("role") in ("assistant", "model") else "user"
            append_turn(role, item.get("content", ""))

        # Build current prompt turn
        evidence = "\n\n".join(
            f"[{item['doc_id']}] {item['title']}: {item['snippet']}" for item in request.evidence
        ) or "(no matching passages)"

        facts_block = ""
        if request.facts:
            facts_lines = "\n".join(f"  {k}: {v}" for k, v in request.facts.items())
            facts_block = f"Session facts (canonical):\n{facts_lines}\n"

        user = (
            f"Active goal: {request.goal}\n"
            f"Constraints: {', '.join(request.constraints) or 'none'}\n"
            f"{facts_block}"
            f"Evidence (Interject Travel demo knowledge base - fictional sample data):\n{evidence}\n\n"
            f"User just said: {request.utterance}"
        )
        if request.notice:
            user += (
                "\n\nThe safety harness refused part of this request. Tell the user plainly, "
                f"in your own words: {request.notice}"
            )
        if request.resume_from:
            user += (
                "\n\nYou were interrupted mid-answer. Here is what you had already said; "
                f"continue it, do not repeat it:\n\"\"\"{request.resume_from}\"\"\""
            )
        append_turn("user", user)
        return contents

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        client = self._get_client()
        contents = self._build_contents(request)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            temperature=0.3,
        )

        # Gemini's 429 (RESOURCE_EXHAUSTED) carries a short RetryInfo delay for
        # its per-minute rate-limit component, distinct from a genuinely
        # exhausted daily quota (which this cannot fix, and still raises once
        # the retry budget is spent). Retried only while no chunk of THIS
        # attempt has been yielded yet, so a mid-stream failure never
        # re-sends already-delivered text.
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            yielded_any = False
            try:
                stream_or_coro = client.aio.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                if inspect.iscoroutine(stream_or_coro):
                    response_stream = await stream_or_coro
                else:
                    response_stream = stream_or_coro

                async for chunk in response_stream:
                    text = getattr(chunk, "text", None)
                    if text:
                        yielded_any = True
                        yield text
                return
            except asyncio.CancelledError:
                # Re-raise cancellation immediately so AgentRuntime can save checkpoint
                raise
            except Exception as exc:
                rate_limited = not yielded_any and getattr(exc, "code", None) == 429
                if rate_limited and attempt < max_attempts:
                    await asyncio.sleep(2 * attempt)
                    continue
                raise RuntimeError(f"Gemini streaming error: {exc}") from exc
