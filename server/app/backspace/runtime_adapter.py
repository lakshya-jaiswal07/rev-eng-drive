"""The one boundary between the existing runtime and BackspaceCore.

This file still imports nothing outside ``app.backspace`` - not
``app.runtime``, not ``app.goals``, not ``asyncio``, not FastAPI. That is
what keeps it safe to live inside this package rather than in the runtime
layer: the dependency direction stays RUNTIME -> BACKSPACE CORE, never the
reverse, and ``test_package_isolation.py``'s static import check covers this
file exactly like every other one in the package.

``FactObservation`` is the structured boundary the brief asks for: the
runtime (or, later, Member 2's LLM/RAG layer) hands over a plain, opaque
observation - it never touches ``FactNotebook``, ``ChangeSet``,
``DependencyGraph`` or the claim ledger directly. ``process_backspace_
observation`` is the only function that translates one observation into the
``assert_fact`` -> (``invalidate`` -> ``plan_recompute`` -> ``build_
explanation``, only if the fact actually changed) sequence - it contains no
graph traversal or domain mutation of its own; every step is a single call
into ``BackspaceCore``, in the order the brief requires and for the same
reason Phase 5 already had to learn the hard way: invalidation must be
triggered through ``BackspaceCore.invalidate()``, never reimplemented here,
so the "walk from the *previous* fact_id" invariant stays centralized in one
place.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .changes import ChangeKind, ChangeSet
from .core import BackspaceCore, FactUpdate
from .explanation import BackspaceExplanation
from .invalidation import Invalidation
from .planner import RecomputationPlan


@dataclass(frozen=True)
class FactObservation:
    """A structured fact observation crossing into BackspaceCore.

    Deliberately not a second ``Fact`` model: it carries only what
    ``BackspaceCore.assert_fact`` needs, and BackspaceCore remains the only
    thing that ever constructs an actual ``Fact``. ``goal_id`` is passed
    through opaquely - nothing here (or anywhere in ``backspace/``) parses it.
    """

    key: str
    value: Any
    source: str
    turn_id: str
    goal_id: str | None = None
    confidence: float = 1.0
    observation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "turn_id": self.turn_id,
            "goal_id": self.goal_id,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }


@dataclass
class BackspaceIntegrationResult:
    """Everything the runtime needs from one observation, and nothing else.

    ``changeset``/``invalidation``/``recomputation_plan``/``explanation`` are
    ``None`` for a NEW or UNCHANGED observation - by design, not omission:
    the brief is explicit that UNCHANGED especially must not produce any of
    this machinery. Holds references to the existing domain result objects
    directly (no re-modelling of ``FactUpdate``/``ChangeSet``/etc.) - the
    runtime reads whichever of Phase 2-8's own structures it needs.
    """

    fact_update: FactUpdate
    changeset: ChangeSet | None = None
    invalidation: Invalidation | None = None
    recomputation_plan: RecomputationPlan | None = None
    explanation: BackspaceExplanation | None = None

    @property
    def change_kind(self) -> ChangeKind:
        return self.fact_update.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_update": self.fact_update.to_dict(),
            "changeset": self.changeset.to_dict() if self.changeset is not None else None,
            "invalidation": self.invalidation.to_dict() if self.invalidation is not None else None,
            "recomputation_plan": (
                self.recomputation_plan.to_dict() if self.recomputation_plan is not None else None
            ),
            "explanation": self.explanation.to_dict() if self.explanation is not None else None,
        }


def process_backspace_observation(
    core: BackspaceCore, observation: FactObservation
) -> BackspaceIntegrationResult:
    """Route one structured observation through ``core`` and return exactly
    what the runtime needs to decide what happens next.

    * NEW - the fact is recorded (via ``BackspaceCore.assert_fact``, which is
      the only thing that touches ``FactNotebook``); nothing else runs.
    * UNCHANGED - same: ``assert_fact`` already guarantees no ChangeSet is
      produced, so there is nothing further to call and nothing noisy to
      report.
    * CHANGED - the ChangeSet ``assert_fact`` produced is passed to
      ``core.invalidate()`` (never reimplemented here), the resulting
      ``Invalidation`` to ``core.plan_recompute()``, and the changeset's own
      id to ``core.build_explanation()`` - in exactly that order, since each
      step's output is the next step's required input.

    Never executes any recomputation, never calls ``retract_claim``, never
    raises anything of its own - a malformed observation surfaces whatever
    error ``BackspaceCore`` itself raises (e.g. a bad ``key``/``value``
    combination), unhandled, rather than being swallowed here.
    """
    fact_update = core.assert_fact(
        observation.key,
        observation.value,
        source=observation.source,
        turn_id=observation.turn_id,
        goal_id=observation.goal_id,
        confidence=observation.confidence,
    )

    if fact_update.status is not ChangeKind.CHANGED:
        return BackspaceIntegrationResult(fact_update=fact_update)

    changeset = fact_update.changeset
    assert changeset is not None  # guaranteed by assert_fact for CHANGED (facts.py, Phase 3)

    invalidation = core.invalidate(changeset)
    plan = core.plan_recompute(invalidation)
    explanation = core.build_explanation(changeset.changeset_id)

    return BackspaceIntegrationResult(
        fact_update=fact_update,
        changeset=changeset,
        invalidation=invalidation,
        recomputation_plan=plan,
        explanation=explanation,
    )
