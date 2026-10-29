"""Composite result/domain-event models, and the BackspaceCore facade.

``FactUpdate`` and ``Retraction`` are composite result objects; ``BackspaceEvent``
is an internal domain concept only - not a ``ServerFrame`` (see
``app/schemas.py``), does not know how to serialise onto a WebSocket, and
nothing here imports ``asyncio`` or anything FastAPI-shaped. Translating a
``BackspaceEvent`` into a wire frame is the runtime layer's job, not this
package's.

``BackspaceCore`` (from Phase 3 onward) is the facade: it owns a session-local
``FactNotebook`` and ``DependencyGraph`` and exposes their operations as one
surface. It exposes the full read side of the pipeline - ``assert_fact`` ->
(nothing automatic yet) -> ``invalidate`` -> ``plan_recompute`` - but
deliberately does not wire the arrows itself: a CHANGED ``assert_fact`` still
returns ``invalidation=None``/``plan=None``, and ``invalidate`` never calls
``plan_recompute`` on its own. Phase 7 adds the claim ledger on top the same
way - ``invalidate`` still only flips a spoken claim's status to
``INVALIDATED`` and classifies it into ``Invalidation.
spoken_invalidated_claim_ids``; nothing calls ``retract_claim`` automatically
just because a claim landed there. Wiring these stages together end to end is
a later phase's work, once each has been independently verified. Every facade
method here is a thin delegation; this file decides nothing on its own that
``facts.py``, ``graph.py``, ``invalidation.py``, ``planner.py`` or
``claims.py`` doesn't already decide. The one piece of state this file itself
owns is ``_retractions`` (Phase 7) - the session-scoped record of "has this
claim already been retracted", which belongs on the facade rather than in any
one of those modules since it is about the retraction *act*'s idempotency,
not about any single object's own fields.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

from .actions import ActionCommit, ActionCommitResult, ActionLedger
from .changes import ChangeKind, ChangeSet
from .claims import Claim, ClaimStatus
from .claims import get_claim_history as _get_claim_history
from .claims import invalidate_claim as _invalidate_claim
from .claims import mark_claim_spoken as _mark_claim_spoken
from .claims import require_claim_retractable as _require_claim_retractable
from .claims import supersede_claim as _supersede_claim
from .explanation import BackspaceExplanation, ChangeSetNotFoundError
from .explanation import build_explanation as _build_explanation
from .explanation import render_explanation_text as _render_explanation_text
from .facts import Fact, FactNotebook
from .graph import Dependency, DependencyGraph, DependencyKind, NodeKind, NodeNotRegisteredError, WorkItem
from .invalidation import Invalidation
from .invalidation import invalidate as _invalidate
from .invalidation import invalidate_many as _invalidate_many
from .planner import RecomputationPlan
from .planner import plan_recompute as _plan_recompute


@dataclass
class FactUpdate:
    """The result ``assert_fact``/``update_fact`` will eventually return.

    ``status`` reuses ``ChangeKind`` rather than a second new/unchanged/
    changed enum - the two questions ("what changed?" and "did this fact
    observation change anything?") are the same question asked from two
    sides, and giving them separate enums would just invite the two to drift.
    """

    fact: Fact
    status: ChangeKind
    previous: Fact | None = None
    changeset: ChangeSet | None = None
    invalidation: Invalidation | None = None
    plan: RecomputationPlan | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact.to_dict(),
            "status": self.status.value,
            "previous": self.previous.to_dict() if self.previous is not None else None,
            "changeset": self.changeset.to_dict() if self.changeset is not None else None,
            "invalidation": self.invalidation.to_dict() if self.invalidation is not None else None,
            "plan": self.plan.to_dict() if self.plan is not None else None,
        }


@dataclass
class Retraction:
    """The result ``BackspaceCore.retract_claim`` (Phase 7) returns.

    Distinct from ``Invalidation``: an invalidation is graph-wide bookkeeping
    triggered by a fact change; a retraction is the user-facing act of taking
    back one already-spoken claim, and carries the reason a person reading
    the transcript would need. ``previous_claim`` is a snapshot of the claim
    as it stood *before* retraction (status ``INVALIDATED``, ``spoken_at``
    set, original ``text``) - not the live, now-``RETRACTED`` object.
    """

    claim_id: str
    reason: str
    retraction_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    previous_claim: Claim | None = None
    changed_facts: list[Fact] = field(default_factory=list)
    source: str = "unknown"
    created_at: float = field(default_factory=time.time)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "retraction_id": self.retraction_id,
            "claim_id": self.claim_id,
            "reason": self.reason,
            "previous_claim": self.previous_claim.to_dict() if self.previous_claim is not None else None,
            "changed_facts": [f.to_dict() for f in self.changed_facts],
            "source": self.source,
            "created_at": self.created_at,
            "provenance": dict(self.provenance),
        }


class BackspaceEventType(str, Enum):
    """Internal domain events a future pipeline will raise.

    Not wire frames. A later phase may translate a subset of these into
    ``ServerFrame`` subclasses in ``app/schemas.py``; that translation does
    not live here.
    """

    FACT_ADDED = "fact_added"
    FACT_CHANGED = "fact_changed"
    WORK_REGISTERED = "work_registered"
    DEPENDENCY_REGISTERED = "dependency_registered"
    CLAIM_REGISTERED = "claim_registered"
    WORK_INVALIDATED = "work_invalidated"
    CLAIM_INVALIDATED = "claim_invalidated"
    CLAIM_RETRACTED = "claim_retracted"
    RECOMPUTATION_PLANNED = "recomputation_planned"
    BACKSPACE_COMPLETED = "backspace_completed"


@dataclass
class BackspaceEvent:
    """One internal domain event, carrying whatever payload it describes."""

    event_type: BackspaceEventType
    turn_id: str = ""
    goal_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "turn_id": self.turn_id,
            "goal_id": self.goal_id,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


class BackspaceCore:
    """The facade: owns a session-local ``FactNotebook`` and exposes the
    public fact API.

    This phase implements fact operations only. It composes ``FactNotebook``
    rather than reimplementing anything from it - every method below is a
    thin, direct delegation, so there is exactly one place (``facts.py``)
    that actually decides NEW/UNCHANGED/CHANGED. Dependency registration,
    invalidation, recomputation planning and claim lifecycle transitions are
    not implemented here yet; they compose on top of this facade in a later
    phase.

    Phase 4 adds the dependency-graph surface: registering work/claims and
    edges, and querying dependencies/dependents. It is a thin delegation to
    ``DependencyGraph``, the same way the fact methods delegate to
    ``FactNotebook`` - this facade still decides nothing itself. Nothing
    here connects the two: ``assert_fact`` does not touch the graph, and no
    graph query triggers invalidation. That wiring is later phases' job.

    Each instance owns its own ``FactNotebook`` and ``DependencyGraph`` - two
    ``BackspaceCore()``s never share state, matching the session-scoped-
    memory constraint the rest of the server already follows.
    """

    def __init__(self) -> None:
        self.facts = FactNotebook()
        self.graph = DependencyGraph()
        # Session-scoped: the one canonical Retraction per claim_id, once
        # retract_claim succeeds for it. Lives here, not on DependencyGraph
        # or Claim, because "has this been retracted" is state about the
        # retraction *act* (idempotency), not about the claim's own fields.
        self._retractions: dict[str, Retraction] = {}
        # Phase 8: a small session-scoped history so explain_change(id) has
        # something to look up. ChangeSets are remembered whenever
        # assert_fact/update_fact actually produces one (CHANGED only - NEW
        # and UNCHANGED never carry a changeset, unchanged since Phase 3).
        # Invalidations are remembered whenever invalidate()/invalidate_many()
        # runs, keyed under *every* changeset_id they cover, so explaining
        # any one of a batch's changeset_ids finds the same merged result.
        self._changesets: dict[str, ChangeSet] = {}
        self._invalidations: dict[str, Invalidation] = {}
        # Phase M1-C: the session-scoped at-most-once commit guard for
        # state-changing actions - see actions.py. Owned here, not on
        # DependencyGraph, for the same reason _retractions is owned here
        # rather than on Claim: "has this action already been committed" is
        # about the commit *act*'s idempotency, not a field of any one
        # WorkItem.
        self._actions = ActionLedger()

    # -- fact operations ---------------------------------------------------
    def assert_fact(
        self,
        key: str,
        value: Any,
        *,
        source: str,
        turn_id: str,
        goal_id: str | None = None,
        confidence: float = 1.0,
    ) -> FactUpdate:
        """Record a structured fact observation. See ``FactNotebook.observe``
        for the NEW/UNCHANGED/CHANGED semantics this delegates to."""
        update = self.facts.observe(
            key, value, source=source, turn_id=turn_id, goal_id=goal_id, confidence=confidence
        )
        self._remember_changeset(update.changeset)
        return update

    def update_fact(
        self,
        fact_id: str,
        value: Any,
        *,
        source: str,
        turn_id: str,
        goal_id: str | None = None,
        confidence: float = 1.0,
    ) -> FactUpdate:
        """Correct the fact identified by ``fact_id``. Raises
        ``FactNotFoundError`` (from ``facts.py``) if it does not exist."""
        update = self.facts.update_fact(
            fact_id, value, source=source, turn_id=turn_id, goal_id=goal_id, confidence=confidence
        )
        self._remember_changeset(update.changeset)
        return update

    def _remember_changeset(self, changeset: ChangeSet | None) -> None:
        if changeset is not None:
            self._changesets[changeset.changeset_id] = changeset

    def get_fact(self, key: str) -> Fact | None:
        return self.facts.get_fact(key)

    def get_fact_history(self, key: str) -> list[Fact]:
        return self.facts.get_fact_history(key)

    def snapshot(self) -> dict[str, Any]:
        """A read-only, serialisation-friendly dump of this session's facts,
        claims (with their lifecycle state - status, spoken_at, supersession,
        invalidation reason) and retractions. Every value is built fresh via
        each object's own ``to_dict()``; nothing here exposes a live,
        mutable internal collection."""
        return {
            "facts": self.facts.snapshot(),
            "claims": {
                claim_id: self.graph.get_claim(claim_id).to_dict()
                for claim_id in self.graph.all_claim_ids()
            },
            "retractions": {
                claim_id: retraction.to_dict() for claim_id, retraction in self._retractions.items()
            },
            "actions": {
                action_id: self._actions.get(action_id).to_dict()
                for action_id in self._actions.all_action_ids()
            },
        }

    def reset(self) -> None:
        """Clear this instance's notebook, graph, retraction ledger,
        changeset/invalidation history and action commit ledger. Other
        ``BackspaceCore`` instances are untouched."""
        self.facts.reset()
        self.graph.reset()
        self._retractions.clear()
        self._changesets.clear()
        self._invalidations.clear()
        self._actions.reset()

    # -- dependency-graph operations -----------------------------------------
    def register_work(self, work: WorkItem) -> WorkItem:
        return self.graph.register_work(work)

    def register_claim(self, claim: Claim) -> Claim:
        return self.graph.register_claim(claim)

    def register_dependency(self, kind: DependencyKind, from_id: str, to_id: str) -> Dependency:
        return self.graph.register_dependency(kind, from_id, to_id)

    def get_dependencies(self, node_id: str) -> list[str]:
        return self.graph.get_dependencies(node_id)

    def get_direct_dependents(self, node_id: str) -> list[str]:
        return self.graph.get_direct_dependents(node_id)

    def get_transitive_dependents(self, node_id: str) -> list[str]:
        return self.graph.get_transitive_dependents(node_id)

    # -- claim ledger -----------------------------------------------------------
    def get_claim(self, claim_id: str) -> Claim | None:
        return self.graph.get_claim(claim_id)

    def get_claim_history(self, claim_id: str) -> list[Claim]:
        """The full supersession chain containing ``claim_id`` - see
        ``claims.get_claim_history``."""
        return _get_claim_history(self.graph, claim_id)

    def mark_claim_spoken(self, claim_id: str) -> None:
        """Record that ``claim_id`` was actually said to the user. Idempotent
        - see ``claims.mark_claim_spoken``. Raises ``ClaimNotFoundError`` for
        an unregistered id."""
        _mark_claim_spoken(self.graph, claim_id)

    def invalidate_claim(self, claim_id: str, reason: str) -> None:
        """Mark one claim invalid directly, outside the bulk graph walk -
        see ``claims.invalidate_claim``. Does not create a Retraction; call
        ``retract_claim`` explicitly for a claim that was spoken."""
        _invalidate_claim(self.graph, claim_id, reason)

    def retract_claim(
        self, claim_id: str, reason: str, *, changed_facts: list[Fact] | None = None
    ) -> Retraction:
        """Record the retraction of an already-spoken, already-invalidated
        claim.

        Raises ``ClaimNotFoundError``/``ClaimNotSpokenError``/
        ``ClaimNotInvalidatedError`` (from ``claims.py``) if ``claim_id``
        cannot be retracted - see ``claims.require_claim_retractable`` for
        the exact rule. Idempotent: a second call for the same ``claim_id``
        returns the *same* ``Retraction`` unchanged (the given ``reason``/
        ``changed_facts`` are ignored on a repeat call) - one canonical
        retraction per claim, matching ``register_work``/``register_claim``'s
        established first-registration-wins convention.

        ``changed_facts`` is optional context ("which fact change caused
        this"), not required: a caller with only a ``claim_id`` and a reason
        string can still retract; a caller that already has an
        ``Invalidation``'s ``changed_facts`` on hand can thread it through
        for a more complete structured record.
        """
        existing = self._retractions.get(claim_id)
        if existing is not None:
            return existing

        claim = _require_claim_retractable(self.graph, claim_id)  # raises if ineligible
        # Snapshot *before* mutating - the same discipline FactNotebook uses
        # for a superseded Fact: `previous_claim` must show the claim as it
        # was (status=INVALIDATED, spoken_at set, original text) rather than
        # the post-retraction state the live object moves on to.
        previous_claim = replace(claim)

        claim.status = ClaimStatus.RETRACTED

        retraction = Retraction(
            claim_id=claim_id,
            reason=reason,
            previous_claim=previous_claim,
            changed_facts=list(changed_facts) if changed_facts else [],
            source="backspace_core",
        )
        self._retractions[claim_id] = retraction
        claim.retraction_id = retraction.retraction_id
        return retraction

    def get_retraction(self, claim_id: str) -> Retraction | None:
        """The canonical Retraction for ``claim_id``, if one has been
        recorded - ``None`` otherwise (never raises for an unknown id,
        matching this package's lenient-read convention for queries)."""
        return self._retractions.get(claim_id)

    def supersede_claim(self, old_claim_id: str, new_claim: Claim) -> Claim:
        """Register ``new_claim`` as the replacement for ``old_claim_id`` -
        see ``claims.supersede_claim``. Does not generate ``new_claim``
        itself; that is recomputation's job, out of scope for this phase."""
        return _supersede_claim(self.graph, old_claim_id, new_claim)

    # -- invalidation ---------------------------------------------------------
    def invalidate(self, changeset: ChangeSet) -> Invalidation:
        """Compute what becomes stale downstream of one ChangeSet, against
        this instance's own graph. Does not touch FactNotebook state and is
        not called automatically from ``assert_fact`` - that wiring is a
        later phase's job."""
        result = _invalidate(self.graph, changeset)
        self._remember_invalidation(result)
        return result

    def invalidate_many(self, changesets: Sequence[ChangeSet]) -> Invalidation:
        """Same as ``invalidate``, merged across a batch of ChangeSets - see
        ``invalidation.invalidate_many`` for the exact semantics."""
        result = _invalidate_many(self.graph, changesets)
        self._remember_invalidation(result)
        return result

    def _remember_invalidation(self, invalidation: Invalidation) -> None:
        for changeset_id in invalidation.changeset_ids:
            self._invalidations[changeset_id] = invalidation

    # -- recomputation planning -------------------------------------------------
    def plan_recompute(self, invalidation: Invalidation) -> RecomputationPlan:
        """Turn an Invalidation into a deterministic recomputation plan
        against this instance's own graph. Read-only, never executes
        anything - see ``planner.plan_recompute``. Not called automatically
        from ``invalidate`` or ``assert_fact``; that wiring is a later
        phase's job."""
        return _plan_recompute(self.graph, invalidation)

    # -- action commit guard (Phase M1-C) --------------------------------------
    def commit_action(self, work_id: str, attempt: int, result: Any = None) -> ActionCommitResult:
        """The at-most-once guard the execution layer calls right before (or
        right after) performing a state-changing action's real-world effect.

        Raises ``NodeNotRegisteredError`` if ``work_id`` was never registered
        (there is nothing to check currency against). Raises
        ``StaleExecutionError`` (from ``work_lifecycle.py``) if ``attempt``
        is not this work item's current, in-flight/just-completed attempt -
        this is the call that must happen *before* an old, superseded
        execution's result is allowed to take effect. See ``actions.py`` for
        the exact identity/idempotency rules and the precise guarantee this
        provides.
        """
        work = self.graph.get_work(work_id)
        if work is None:
            raise NodeNotRegisteredError(work_id, NodeKind.WORK)
        return self._actions.commit(work, attempt, result)

    def get_action_commit(self, action_id: str) -> ActionCommit | None:
        """The recorded commit for ``action_id``, if one exists - ``None``
        otherwise (lenient read, matching this package's convention)."""
        return self._actions.get(action_id)

    def is_action_committed(self, action_id: str) -> bool:
        return self._actions.is_committed(action_id)

    # -- provenance / explanation (Phase 8) --------------------------------------
    def get_changeset(self, changeset_id: str) -> ChangeSet | None:
        """The remembered ChangeSet for ``changeset_id``, if any - ``None``
        for an id this session never produced (lenient read, matching this
        package's convention elsewhere)."""
        return self._changesets.get(changeset_id)

    def get_invalidation_for_changeset(self, changeset_id: str) -> Invalidation | None:
        """The Invalidation last computed that covered ``changeset_id``, if
        ``invalidate``/``invalidate_many`` has been called for it - ``None``
        otherwise. Read-only: this never computes one on demand."""
        return self._invalidations.get(changeset_id)

    def build_explanation(self, changeset_id: str) -> BackspaceExplanation:
        """The structured, authoritative explanation of one changeset.

        Raises ``ChangeSetNotFoundError`` if ``changeset_id`` was never
        produced by ``assert_fact``/``update_fact`` on this instance.
        Entirely read-only: it looks up the already-remembered ``ChangeSet``
        and ``Invalidation`` (if ``invalidate``/``invalidate_many`` has
        already been called for it - if not, the invalidated/kept/recompute
        sections come back empty rather than this method computing them
        itself) and computes a fresh ``RecomputationPlan`` via
        ``plan_recompute``, which is itself already guaranteed read-only
        (Phase 6). Nothing here mutates a Fact, WorkItem or Claim.
        """
        return self.build_explanation_many([changeset_id])

    def build_explanation_many(self, changeset_ids: Sequence[str]) -> BackspaceExplanation:
        """Same as ``build_explanation``, for a batch of changeset_ids that
        share one ``invalidate_many`` call - e.g. two facts changed in the
        same turn. Raises ``ChangeSetNotFoundError`` for the first unknown id."""
        changesets: list[ChangeSet] = []
        for changeset_id in changeset_ids:
            changeset = self._changesets.get(changeset_id)
            if changeset is None:
                raise ChangeSetNotFoundError(changeset_id)
            changesets.append(changeset)

        invalidation: Invalidation | None = None
        for changeset_id in changeset_ids:
            candidate = self._invalidations.get(changeset_id)
            if candidate is not None:
                invalidation = candidate
                break
        plan = self.plan_recompute(invalidation) if invalidation is not None else None

        return _build_explanation(self.graph, changesets, invalidation, plan, self._retractions)

    def explain_change(self, changeset_id: str) -> str:
        """A deterministic, concise human-readable rendering of
        ``build_explanation(changeset_id)``. The structured explanation is
        authoritative; this string is convenience output derived from it."""
        return _render_explanation_text(self.build_explanation(changeset_id))

    def explain_changes(self, changeset_ids: Sequence[str]) -> str:
        """Same as ``explain_change``, for a batch of changeset_ids."""
        return _render_explanation_text(self.build_explanation_many(changeset_ids))
