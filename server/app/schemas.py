"""Wire protocol between the browser and the agent runtime.

The socket is full duplex: the client keeps sending while the server keeps
streaming. Every server frame carries a monotonic ``ts`` so the UI can draw a
real latency timeline rather than guessing.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def now_ms() -> float:
    return time.monotonic() * 1000.0


class Stage(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    SPECULATING = "speculating"
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    TOOLING = "tooling"
    REASONING = "reasoning"
    RESPONDING = "responding"
    INTERRUPTED = "interrupted"
    RECOVERING = "recovering"
    DONE = "done"
    HEADSUP = "headsup"


class GoalAction(str, Enum):
    PUSH = "push"
    CONTINUE = "continue"
    REFINE = "refine"
    SWITCH = "switch"
    REVERT = "revert"
    COMPLETE = "complete"
    # Not a classification of what the user said - a plan-progress update.
    PROGRESS = "progress"


# --------------------------------------------------------------------------
# client -> server
# --------------------------------------------------------------------------


class ClientHello(BaseModel):
    t: Literal["hello"] = "hello"
    session_id: str | None = None


class ClientPartial(BaseModel):
    """A partial utterance. Arrives while the user is still typing/speaking."""

    t: Literal["partial"] = "partial"
    text: str
    seq: int = 0


class ClientFinal(BaseModel):
    t: Literal["final"] = "final"
    text: str
    seq: int = 0
    modality: Literal["text", "voice", "image"] = "text"
    attachment: str | None = None


class ClientInterrupt(BaseModel):
    t: Literal["interrupt"] = "interrupt"
    reason: Literal["barge_in", "stop"] = "barge_in"


class ClientResume(BaseModel):
    t: Literal["resume"] = "resume"
    checkpoint_id: str | None = None


class ClientReset(BaseModel):
    t: Literal["reset"] = "reset"


class ClientConfig(BaseModel):
    t: Literal["config"] = "config"
    speculation: bool | None = None
    strict_harness: bool | None = None
    token_delay_ms: int | None = Field(default=None, ge=0, le=400)


ClientEvent = (
    ClientHello
    | ClientPartial
    | ClientFinal
    | ClientInterrupt
    | ClientResume
    | ClientReset
    | ClientConfig
)


# --------------------------------------------------------------------------
# server -> client
# --------------------------------------------------------------------------


class ServerFrame(BaseModel):
    t: str
    ts: float = Field(default_factory=now_ms)


class StageFrame(ServerFrame):
    t: Literal["stage"] = "stage"
    stage: Stage
    detail: str = ""
    turn_id: str | None = None


class TokenFrame(ServerFrame):
    t: Literal["token"] = "token"
    turn_id: str
    text: str


class MessageFrame(ServerFrame):
    t: Literal["message"] = "message"
    turn_id: str
    role: Literal["user", "agent", "system"]
    content: str
    modality: str = "text"
    status: Literal["complete", "interrupted", "resumed"] = "complete"
    meta: dict[str, Any] = Field(default_factory=dict)


class FillerFrame(ServerFrame):
    """Ephemeral chatter while the agent works. Never enters the transcript."""

    t: Literal["filler"] = "filler"
    turn_id: str
    text: str


class NudgeFrame(ServerFrame):
    """An offer to return to a goal that was parked during a detour."""

    t: Literal["nudge"] = "nudge"
    goal_id: str
    text: str
    prompt: str


class GoalFrame(ServerFrame):
    t: Literal["goal"] = "goal"
    action: GoalAction
    stack: list[dict[str, Any]]
    rationale: str = ""


class SpecFrame(ServerFrame):
    t: Literal["spec"] = "spec"
    status: Literal["started", "hit", "miss", "discarded"]
    query: str
    saved_ms: float = 0.0
    docs: list[str] = Field(default_factory=list)


class HeadsUpFrame(ServerFrame):
    t: Literal["headsup"] = "headsup"
    turn_id: str
    claim: str
    contradiction: str
    confidence: float
    source_doc_id: str
    cut_in_text: str


class ToolFrame(ServerFrame):
    t: Literal["tool"] = "tool"
    call_id: str
    name: str
    status: Literal["requested", "allowed", "blocked", "ok", "error", "timeout"]
    verdict: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0


class CheckpointFrame(ServerFrame):
    t: Literal["checkpoint"] = "checkpoint"
    checkpoint_id: str
    turn_id: str
    summary: str
    kept_tokens: int
    retrieved_docs: list[str] = Field(default_factory=list)
    plan_progress: str = ""


class MetricFrame(ServerFrame):
    t: Literal["metric"] = "metric"
    name: str
    value: float
    unit: str = "ms"
    note: str = ""


class ErrorFrame(ServerFrame):
    t: Literal["error"] = "error"
    message: str
    recoverable: bool = True
