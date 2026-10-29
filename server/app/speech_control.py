"""Spoken control phrases shared by the assistant and the in-car agent.

"Hold on" or "stop" said over the agent is the barge-in itself, not a new
request: the agent goes quiet, keeps what was not yet heard, and waits for
"go on". Treating it as a request made the agent answer the words "hold on"
by starting to talk again.
"""

from __future__ import annotations

import re

_HOLD = re.compile(
    r"^(?:ok(?:ay)?\s+|no\s+|please\s+|hey\s+)?"
    r"(?:hold on|hang on|hold it|wait|wait up|stop|stop talking|stop it|stop there|pause|quiet|be quiet|silence"
    r"|sh+|shush|shut up|enough|that'?s enough|one (?:sec|second|moment|minute)"
    r"|just a (?:sec|second|moment|minute)|give me a (?:sec|second|moment|minute))"
    r"(?:\s+(?:a\s+)?(?:sec|second|moment|minute|bit))?(?:\s+please)?$"
)
_RESUME = re.compile(
    r"^(?:ok(?:ay)?\s+|yes\s+|yeah\s+|sure\s+|please\s+)?"
    r"(?:go on|continue|carry on|keep going|go ahead|you were saying|finish that|and then|resume)\b"
)


def _normalise(text: str) -> str:
    text = re.sub(r"[^a-z' ]+", " ", text.lower().replace("’", "'"))
    return re.sub(r"\s+", " ", text).strip()


def is_hold(text: str) -> bool:
    """The whole utterance only asks the agent to stop talking and wait."""
    return bool(_HOLD.match(_normalise(text)))


def is_resume(text: str) -> bool:
    """The utterance asks the agent to carry on from where it stopped."""
    return bool(_RESUME.match(_normalise(text)))
