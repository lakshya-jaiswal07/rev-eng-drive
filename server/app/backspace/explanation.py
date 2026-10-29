"""Provenance / explanation layer: turns already-computed BACKSPACE state
into a structured, deterministic explanation of one fact change.

Read-only by construction: every function in this file only *reads* -
``graph.get_work``/``get_claim``, the fields already on a ``ChangeSet``/
``Invalidation``/``RecomputationPlan`` - and never calls a mutating method
(``register_*``, ``invalidate*``, ``mark_claim_spoken``, ``retract_claim``,
``assert_fact``, ...). It does not even call ``plan_recompute`` itself -
that stays ``BackspaceCore``'s job (see ``core.py``), so this module never
needs to decide *whether* a plan should be computed, only how to describe
one it is handed.

Nothing here duplicates a domain model: a "change" is described by reading
the ``Fact`` objects a ``ChangeSet`` already holds, a "kept"/"invalidated
work" entry is read from the live ``WorkItem`` the graph already has, an
"invalidated claim" entry is read from the live ``Claim`` plus whatever
``Retraction`` (if any) ``BackspaceCore`` already recorded for it, and a
"recompute" entry is read from an already-computed ``RecomputationPlan``.
This file only decides how to *present* that state, never what it is.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from .changes import ChangeSet
from .graph import DependencyGraph
from .invalidation import Invalidation
from .planner import PlanStatus, RecomputationPlan


class ChangeSetNotFoundError(Exception):
    """Raised when a ``changeset_id`` is referenced but the session has
    never seen it. Mirrors ``facts.py``'s ``FactNotFoundError``."""

    def __init__(self, changeset_id: str) -> None:
        super().__init__(f"No changeset with id {changeset_id!r}.")
        self.changeset_id = changeset_id


# ===========================================================================
# structured sections
# ===========================================================================


@dataclass(frozen=True)
class ChangeExplanation:
    """One fact transition, whatever kind it is.

    ``fact_id``/``new_value``/``new_version`` describe the *current* fact
    after the observation; ``previous_*`` describe what it replaced, and are
    ``None`` for a NEW fact (there was nothing to replace). This is the same
    shape for every ``change_kind`` rather than a different field set per
    kind, since a NEW fact is exactly a CHANGED fact with no ``previous_*``.
    """

    changeset_id: str
    key: str
    change_kind: str  # "new" | "unchanged" | "changed"
    fact_id: str
    new_value: Any
    new_version: int
    previous_fact_id: str | None = None
    previous_value: Any = None
    previous_version: int | None = None
    source: str = "unknown"
    turn_id: str = ""
    goal_id: str | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "changeset_id": self.changeset_id,
            "key": self.key,
            "change_kind": self.change_kind,
            "fact_id": self.fact_id,
            "new_value": self.new_value,
            "new_version": self.new_version,
            "previous_fact_id": self.previous_fact_id,
            "previous_value": self.previous_value,
            "previous_version": self.previous_version,
            "source": self.source,
            "turn_id": self.turn_id,
            "goal_id": self.goal_id,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class WorkExplanation:
    """One WorkItem, either kept (``reason`` empty) or invalidated
    (``reason``/``caused_by_fact_keys`` populated from the actual dependency
    graph - never a generic "something changed")."""

    work_id: str
    kind: str
    turn_id: str
    goal_id: str | None
    status: str
    reason: str = ""
    caused_by_fact_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "kind": self.kind,
            "turn_id": self.turn_id,
            "goal_id": self.goal_id,
            "status": self.status,
            "reason": self.reason,
            "caused_by_fact_keys": list(self.caused_by_fact_keys),
        }


@dataclass(frozen=True)
class ClaimExplanation:
    """One invalidated claim, with the three-way spoken/retraction
    distinction the brief calls essential:

    * unspoken: ``spoken=False``, ``retraction_required=False``, ``retracted=False``
    * spoken, not yet retracted: ``spoken=True``, ``retraction_required=True``, ``retracted=False``
    * spoken and retracted: ``spoken=True``, ``retraction_required=False``, ``retracted=True``

    ``spoken`` is read from ``claim.spoken_at is not None`` only - never
    inferred from ``status``, per the brief's explicit instruction.
    """

    claim_id: str
    text: str
    work_id: str | None
    turn_id: str
    status: str
    spoken: bool
    evidence_doc_id: str | None
    invalidation_reason: str | None
    retraction_required: bool
    retracted: bool
    retraction: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "work_id": self.work_id,
            "turn_id": self.turn_id,
            "status": self.status,
            "spoken": self.spoken,
            "evidence_doc_id": self.evidence_doc_id,
            "invalidation_reason": self.invalidation_reason,
            "retraction_required": self.retraction_required,
            "retracted": self.retracted,
            "retraction": self.retraction,
        }


@dataclass(frozen=True)
class RecomputeStep:
    """One entry in the recomputation order - read straight from an
    already-``READY`` ``RecomputationPlan``; nothing here recomputes an
    order of its own."""

    work_id: str
    kind: str
    order: int

    def to_dict(self) -> dict[str, Any]:
        return {"work_id": self.work_id, "kind": self.kind, "order": self.order}


@dataclass(frozen=True)
class ExplanationSummary:
    """The counts ``explain_change``'s text is generated from - computed
    once here so the string form never recomputes (and risks disagreeing
    with) a number the structured form already has."""

    changed_fact_count: int
    new_fact_count: int
    kept_work_count: int
    invalidated_work_count: int
    invalidated_claim_count: int
    retraction_required_count: int
    retracted_count: int
    recompute_count: int
    plan_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_fact_count": self.changed_fact_count,
            "new_fact_count": self.new_fact_count,
            "kept_work_count": self.kept_work_count,
            "invalidated_work_count": self.invalidated_work_count,
            "invalidated_claim_count": self.invalidated_claim_count,
            "retraction_required_count": self.retraction_required_count,
            "retracted_count": self.retracted_count,
            "recompute_count": self.recompute_count,
            "plan_status": self.plan_status,
        }


@dataclass(frozen=True)
class BackspaceExplanation:
    """The authoritative, structured explanation of one or more ChangeSets.
    ``explain_change``'s string form is convenience output generated *from*
    this, never the other way around."""

    changeset_ids: list[str]
    changes: list[ChangeExplanation]
    kept_work: list[WorkExplanation]
    invalidated_work: list[WorkExplanation]
    invalidated_claims: list[ClaimExplanation]
    recompute: list[RecomputeStep]
    missing_dependencies: list[str]
    summary: ExplanationSummary
    explanation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "explanation_id": self.explanation_id,
            "changeset_ids": list(self.changeset_ids),
            "changes": [c.to_dict() for c in self.changes],
            "kept_work": [w.to_dict() for w in self.kept_work],
            "invalidated_work": [w.to_dict() for w in self.invalidated_work],
            "invalidated_claims": [c.to_dict() for c in self.invalidated_claims],
            "recompute": [r.to_dict() for r in self.recompute],
            "missing_dependencies": list(self.missing_dependencies),
            "summary": self.summary.to_dict(),
            "created_at": self.created_at,
        }


# ===========================================================================
# building
# ===========================================================================


def _explain_change(changeset: ChangeSet) -> ChangeExplanation:
    new_fact = changeset.new_fact
    previous_fact = changeset.previous_fact
    return ChangeExplanation(
        changeset_id=changeset.changeset_id,
        key=changeset.key,
        change_kind=changeset.kind.value,
        fact_id=new_fact.fact_id if new_fact is not None else "",
        new_value=new_fact.value if new_fact is not None else None,
        new_version=new_fact.version if new_fact is not None else 0,
        previous_fact_id=previous_fact.fact_id if previous_fact is not None else None,
        previous_value=previous_fact.value if previous_fact is not None else None,
        previous_version=previous_fact.version if previous_fact is not None else None,
        source=new_fact.source if new_fact is not None else changeset.source,
        turn_id=new_fact.turn_id if new_fact is not None else changeset.turn_id,
        goal_id=new_fact.goal_id if new_fact is not None else changeset.goal_id,
        confidence=new_fact.confidence if new_fact is not None else 1.0,
    )


def _explain_kept_work(graph: DependencyGraph, work_id: str) -> WorkExplanation:
    work = graph.get_work(work_id)
    if work is None:
        return WorkExplanation(work_id=work_id, kind="", turn_id="", goal_id=None, status="unknown")
    return WorkExplanation(
        work_id=work.work_id, kind=work.kind, turn_id=work.turn_id, goal_id=work.goal_id,
        status=work.status.value,
    )


def _explain_invalidated_work(
    graph: DependencyGraph, work_id: str, invalidation: Invalidation
) -> WorkExplanation:
    """Same dependency reasoning ``planner.py``'s ``_reason_for`` uses for
    the work items it schedules, generalised to *every* invalidated work
    item (not just the plan's own candidate set, which may exclude some on
    a BLOCKED plan) - deliberately a small, separate implementation rather
    than importing planner internals, so this module never has to reach
    into another module's private helpers.
    """
    work = graph.get_work(work_id)
    invalidated_set = set(invalidation.invalidated_work_ids)

    caused_by_fact_keys: list[str] = []
    for changeset in invalidation.changesets:
        previous = changeset.previous_fact
        if previous is not None and graph.has_dependency(previous.fact_id, work_id):
            if changeset.key not in caused_by_fact_keys:
                caused_by_fact_keys.append(changeset.key)

    if caused_by_fact_keys:
        if len(caused_by_fact_keys) == 1:
            reason = f"depends on changed fact {caused_by_fact_keys[0]!r}"
        else:
            reason = "depends on changed facts " + ", ".join(repr(k) for k in caused_by_fact_keys)
    else:
        upstream_stale = [d for d in graph.get_dependencies(work_id) if d in invalidated_set]
        if upstream_stale:
            reason = "depends on stale work " + ", ".join(repr(d) for d in upstream_stale)
        else:
            reason = "transitively affected by a changed fact"

    if work is None:
        return WorkExplanation(
            work_id=work_id, kind="", turn_id="", goal_id=None, status="unknown",
            reason=reason, caused_by_fact_keys=caused_by_fact_keys,
        )
    return WorkExplanation(
        work_id=work.work_id, kind=work.kind, turn_id=work.turn_id, goal_id=work.goal_id,
        status=work.status.value, reason=reason, caused_by_fact_keys=caused_by_fact_keys,
    )


def _explain_claim(
    graph: DependencyGraph, claim_id: str, retractions: dict[str, Any]
) -> ClaimExplanation:
    claim = graph.get_claim(claim_id)
    retraction = retractions.get(claim_id)
    spoken = claim.spoken_at is not None if claim is not None else False
    retracted = retraction is not None
    retraction_required = spoken and not retracted

    return ClaimExplanation(
        claim_id=claim_id,
        text=claim.text if claim is not None else "",
        work_id=claim.work_id if claim is not None else None,
        turn_id=claim.turn_id if claim is not None else "",
        status=claim.status.value if claim is not None else "unknown",
        spoken=spoken,
        evidence_doc_id=(claim.evidence.doc_id if claim is not None and claim.evidence is not None else None),
        invalidation_reason=claim.invalidation_reason if claim is not None else None,
        retraction_required=retraction_required,
        retracted=retracted,
        retraction=retraction.to_dict() if retraction is not None else None,
    )


def build_explanation(
    graph: DependencyGraph,
    changesets: Sequence[ChangeSet],
    invalidation: Invalidation | None,
    plan: RecomputationPlan | None,
    retractions: dict[str, Any],
) -> BackspaceExplanation:
    """Assemble a ``BackspaceExplanation`` from already-computed state.

    ``invalidation``/``plan`` may be ``None`` - meaning ``invalidate()``/
    ``plan_recompute()`` simply have not been called yet for this changeset.
    That is reported honestly (empty kept/invalidated/recompute sections,
    ``plan_status="not_computed"``) rather than computed here, since
    computing it here would mean this "read-only" function silently
    performing the invalidation itself.
    """
    changes = [_explain_change(cs) for cs in changesets]

    kept_work: list[WorkExplanation] = []
    invalidated_work: list[WorkExplanation] = []
    invalidated_claims: list[ClaimExplanation] = []
    missing_dependencies: list[str] = []
    plan_status = "not_computed"

    if invalidation is not None:
        kept_work = [_explain_kept_work(graph, work_id) for work_id in invalidation.kept_work_ids]
        invalidated_work = [
            _explain_invalidated_work(graph, work_id, invalidation)
            for work_id in invalidation.invalidated_work_ids
        ]
        invalidated_claims = [
            _explain_claim(graph, claim_id, retractions) for claim_id in invalidation.invalidated_claim_ids
        ]
        if plan is not None:
            missing_dependencies = list(plan.missing_dependencies)
            plan_status = plan.status.value

    recompute: list[RecomputeStep] = []
    if plan is not None and plan.status is PlanStatus.READY:
        for index, work_id in enumerate(plan.work_items_to_recompute, start=1):
            work = graph.get_work(work_id)
            recompute.append(RecomputeStep(work_id=work_id, kind=work.kind if work is not None else "", order=index))

    summary = ExplanationSummary(
        changed_fact_count=sum(1 for c in changes if c.change_kind == "changed"),
        new_fact_count=sum(1 for c in changes if c.change_kind == "new"),
        kept_work_count=len(kept_work),
        invalidated_work_count=len(invalidated_work),
        invalidated_claim_count=len(invalidated_claims),
        retraction_required_count=sum(1 for c in invalidated_claims if c.retraction_required),
        retracted_count=sum(1 for c in invalidated_claims if c.retracted),
        recompute_count=len(recompute),
        plan_status=plan_status,
    )

    return BackspaceExplanation(
        changeset_ids=[cs.changeset_id for cs in changesets],
        changes=changes,
        kept_work=kept_work,
        invalidated_work=invalidated_work,
        invalidated_claims=invalidated_claims,
        recompute=recompute,
        missing_dependencies=missing_dependencies,
        summary=summary,
    )


def _pluralize(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def render_explanation_text(explanation: BackspaceExplanation) -> str:
    """A deterministic, concise human-readable rendering of an already-built
    ``BackspaceExplanation``. Every number here comes from ``explanation.
    summary`` - nothing is recomputed or hard-coded."""
    lines: list[str] = []

    changed = [c for c in explanation.changes if c.change_kind == "changed"]
    if changed:
        lines.append(
            "; ".join(f"{c.key} changed from {c.previous_value!r} to {c.new_value!r}" for c in changed) + "."
        )
    new = [c for c in explanation.changes if c.change_kind == "new"]
    if new:
        lines.append(
            "; ".join(f"{c.key} was newly observed as {c.new_value!r}" for c in new) + "."
        )
    if not changed and not new:
        lines.append("No semantic change.")

    summary = explanation.summary
    lines.append(f"{_pluralize(summary.invalidated_work_count, 'work item')} {'was' if summary.invalidated_work_count == 1 else 'were'} invalidated.")
    lines.append(f"{_pluralize(summary.kept_work_count, 'work item')} {'was' if summary.kept_work_count == 1 else 'were'} preserved.")
    if summary.retraction_required_count:
        lines.append(f"{_pluralize(summary.retraction_required_count, 'spoken claim')} require{'s' if summary.retraction_required_count == 1 else ''} retraction.")
    if summary.retracted_count:
        lines.append(f"{_pluralize(summary.retracted_count, 'claim')} {'was' if summary.retracted_count == 1 else 'were'} retracted.")
    if summary.plan_status == "blocked":
        lines.append("Recomputation is blocked.")
    else:
        lines.append(f"{_pluralize(summary.recompute_count, 'work item')} require{'s' if summary.recompute_count == 1 else ''} recomputation.")

    return "\n".join(lines)
