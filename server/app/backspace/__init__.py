"""BACKSPACE Core - data model layer.

Phase 2 scope only: the dataclasses and enums the approved architecture is
built from. Nothing here has behaviour yet - no notebook lookups, no graph
traversal, no invalidation, no recomputation, and nothing that reads a raw
utterance. Those land in later phases, composed on top of these models
through a ``BackspaceCore`` facade.

Deliberately independent of the rest of the server: no module in this
package imports ``app.goals``, ``app.retrieval``'s tokenizer, ``asyncio``, or
anything FastAPI/WebSocket-shaped. That independence is load-bearing, not
incidental - it is what keeps this package synchronous, deterministic and
testable without spinning up the agent runtime.
"""

from __future__ import annotations

from .actions import ActionCommit, ActionCommitResult, ActionLedger, action_id_for
from .changes import ChangeKind, ChangeSet
from .claims import (
    Claim,
    ClaimNotFoundError,
    ClaimNotInvalidatedError,
    ClaimNotSpokenError,
    ClaimStatus,
    Evidence,
)
from .claims import get_claim_history
from .claims import invalidate_claim
from .claims import mark_claim_spoken
from .claims import require_claim_retractable
from .claims import supersede_claim
from .core import BackspaceCore, BackspaceEvent, BackspaceEventType, FactUpdate, Retraction
from .explanation import (
    BackspaceExplanation,
    ChangeExplanation,
    ChangeSetNotFoundError,
    ClaimExplanation,
    ExplanationSummary,
    RecomputeStep,
    WorkExplanation,
)
from .explanation import build_explanation
from .explanation import render_explanation_text
from .facts import Fact, FactNotebook, FactNotFoundError, FactStatus
from .graph import (
    Dependency,
    DependencyCycleError,
    DependencyGraph,
    DependencyGraphError,
    DependencyKind,
    NodeKind,
    NodeKindMismatchError,
    NodeNotRegisteredError,
    UnsupportedDependencyKindError,
    WorkItem,
    WorkStatus,
)
from .invalidation import Invalidation, invalidate, invalidate_many
from .planner import PlanStatus, RecomputationPlan, plan_recompute
from .runtime_adapter import BackspaceIntegrationResult, FactObservation, process_backspace_observation
from .work_lifecycle import (
    IllegalWorkTransitionError,
    StaleExecutionError,
    cancel_work,
    complete_work,
    fail_work,
    invalidate_work,
    is_result_current,
    start_work,
)

__all__ = [
    "ActionCommit",
    "ActionCommitResult",
    "ActionLedger",
    "action_id_for",
    "BackspaceIntegrationResult",
    "BackspaceCore",
    "BackspaceEvent",
    "BackspaceEventType",
    "BackspaceExplanation",
    "build_explanation",
    "cancel_work",
    "ChangeExplanation",
    "ChangeKind",
    "ChangeSet",
    "ChangeSetNotFoundError",
    "Claim",
    "ClaimExplanation",
    "ClaimNotFoundError",
    "ClaimNotInvalidatedError",
    "ClaimNotSpokenError",
    "ClaimStatus",
    "complete_work",
    "Dependency",
    "DependencyCycleError",
    "DependencyGraph",
    "DependencyGraphError",
    "DependencyKind",
    "Evidence",
    "ExplanationSummary",
    "fail_work",
    "Fact",
    "FactNotFoundError",
    "FactNotebook",
    "FactObservation",
    "FactStatus",
    "FactUpdate",
    "get_claim_history",
    "IllegalWorkTransitionError",
    "Invalidation",
    "invalidate",
    "invalidate_claim",
    "invalidate_many",
    "invalidate_work",
    "is_result_current",
    "mark_claim_spoken",
    "NodeKind",
    "NodeKindMismatchError",
    "NodeNotRegisteredError",
    "PlanStatus",
    "process_backspace_observation",
    "RecomputationPlan",
    "plan_recompute",
    "RecomputeStep",
    "render_explanation_text",
    "require_claim_retractable",
    "Retraction",
    "StaleExecutionError",
    "start_work",
    "supersede_claim",
    "UnsupportedDependencyKindError",
    "WorkExplanation",
    "WorkItem",
    "WorkStatus",
]
