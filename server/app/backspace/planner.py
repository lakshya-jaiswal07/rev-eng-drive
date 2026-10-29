"""RecomputationPlan data model, and the recomputation planner built on it.

What deciding "which stale work actually needs rerunning this turn, and in
what order" produces, given an ``Invalidation``. The planner ONLY calculates
this - it never runs a plan step. Nothing in this file calls a provider,
retrieval, a tool, awaits anything, or mutates a Fact or a Claim. Its one
side effect boundary is documented at ``plan_recompute``.

``plan_recompute`` is a plain function, not a class, for the same reason
``invalidate``/``invalidate_many`` are plain functions in ``invalidation.py``
rather than an "engine" object: it holds no state of its own between calls,
so there is nothing a class would add except ceremony.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .graph import DependencyGraph, DependencyKind, WorkStatus
from .invalidation import Invalidation


class PlanStatus(str, Enum):
    """Where a recomputation plan stands.

    ``EMPTY`` - nothing needs recomputing (e.g. the Invalidation reached no
    eligible work). ``READY`` - a complete, dependency-respecting order was
    produced and can be trusted. ``BLOCKED`` - recomputation is needed but a
    complete, trustworthy order could not be produced (a missing dependency,
    or a cycle among the candidate work) - ``work_items_to_recompute`` is
    still populated for visibility, but its order is *not* a validated
    topological order and must not be executed as one. ``PENDING`` is the
    dataclass default for a hand-built plan that has not been computed by
    ``plan_recompute`` at all; the function itself never returns ``PENDING``.
    """

    PENDING = "pending"
    READY = "ready"
    EMPTY = "empty"
    EXECUTED = "executed"
    BLOCKED = "blocked"


@dataclass
class RecomputationPlan:
    """An ordered set of work items to redo, and what was spared.

    ``reasons`` (Phase 6) is additive: a structured, deterministic (never
    natural-language-generated) one-line explanation per recomputed work id,
    built from what the planner actually found in the graph - "depends on
    changed fact 'party_size'" or "depends on stale work 'W3'". ``rationale``
    (Phase 2) remains a single overall summary line.
    """

    invalidation: Invalidation
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    work_items_to_recompute: list[str] = field(default_factory=list)
    preserved_work_ids: list[str] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    rationale: str = ""
    reasons: dict[str, str] = field(default_factory=dict)
    status: PlanStatus = PlanStatus.PENDING
    created_at: float = field(default_factory=time.time)

    @property
    def invalidated_work_ids(self) -> list[str]:
        return self.invalidation.invalidated_work_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "invalidation": self.invalidation.to_dict(),
            "invalidated_work_ids": self.invalidated_work_ids,
            "work_items_to_recompute": list(self.work_items_to_recompute),
            "preserved_work_ids": list(self.preserved_work_ids),
            "missing_dependencies": list(self.missing_dependencies),
            "rationale": self.rationale,
            "reasons": dict(self.reasons),
            "status": self.status.value,
            "created_at": self.created_at,
        }


# Statuses a WorkItem can be in and still be a genuine recomputation
# candidate. VALID ("current, trust it") and RECOMPUTED ("already redone
# since invalidation") are deliberately excluded - see test_planner.py's
# "already valid work" tests. RETRACTED is excluded too: a retracted work
# item was explicitly withdrawn, not a recomputation target.
_ELIGIBLE_STATUSES = {WorkStatus.PENDING, WorkStatus.STALE, WorkStatus.INVALIDATED}


def plan_recompute(graph: DependencyGraph, invalidation: Invalidation) -> RecomputationPlan:
    """Turn an Invalidation into a deterministic, dependency-respecting
    recomputation plan against ``graph``.

    Read-only: this function never registers anything, never mutates a
    WorkItem's/Claim's status, and never touches a Fact. The one thing it
    trusts less than its input is the ``Invalidation`` itself - every
    candidate id is re-checked against the *live* WorkStatus in ``graph``
    before being scheduled, in case the two have drifted since the
    Invalidation was computed.

    Ordering: Kahn's algorithm (in-degree / ready-queue topological sort),
    restricted to WORK_TO_WORK edges whose *both* endpoints are in the
    candidate set - an edge to or from something not being recomputed is
    ignored, which is what lets a stale item's still-valid upstream
    dependency be silently used as-is rather than dragged into the plan.
    Ties (multiple ready nodes at once) are broken by each node's position
    in the candidate list, which is itself the Invalidation's own BFS
    discovery order (Phase 4/5) - i.e. registration order - never Python
    set/dict iteration order.
    """
    # 1. Deduplicate the candidates, preserving the Invalidation's own
    #    deterministic discovery order.
    seen: set[str] = set()
    candidates: list[str] = []
    for work_id in invalidation.invalidated_work_ids:
        if work_id in seen:
            continue
        seen.add(work_id)
        candidates.append(work_id)

    # 2. Defensive status filter, against live graph state.
    to_recompute: list[str] = []
    missing_dependencies: list[str] = []
    for work_id in candidates:
        work = graph.get_work(work_id)
        if work is None:
            missing_dependencies.append(work_id)
            continue
        if work.status not in _ELIGIBLE_STATUSES:
            continue  # VALID / RECOMPUTED / RETRACTED - not scheduled
        to_recompute.append(work_id)

    # 3. Declared-dependency check: everything a to-be-recomputed item says
    #    (via WorkItem.depends_on_work) it needs must actually exist in the
    #    graph - it does not need to be a recomputation *target* itself
    #    (it may be preserved, valid, upstream input), just present.
    for work_id in to_recompute:
        work = graph.get_work(work_id)
        for dep_id in work.depends_on_work:
            if graph.get_work(dep_id) is None and dep_id not in missing_dependencies:
                missing_dependencies.append(dep_id)

    to_recompute_set = set(to_recompute)
    preserved_work_ids = [wid for wid in graph.all_work_ids() if wid not in to_recompute_set]

    if missing_dependencies:
        return RecomputationPlan(
            invalidation=invalidation,
            work_items_to_recompute=to_recompute,
            preserved_work_ids=preserved_work_ids,
            missing_dependencies=missing_dependencies,
            rationale=f"blocked: missing {len(missing_dependencies)} declared dependenc"
                      f"{'y' if len(missing_dependencies) == 1 else 'ies'}",
            status=PlanStatus.BLOCKED,
        )

    if not to_recompute:
        return RecomputationPlan(
            invalidation=invalidation,
            work_items_to_recompute=[],
            preserved_work_ids=preserved_work_ids,
            status=PlanStatus.EMPTY,
            rationale="nothing to recompute",
        )

    order, ok = _topological_order(to_recompute, graph)
    if not ok:
        return RecomputationPlan(
            invalidation=invalidation,
            # Discovery order, explicitly NOT claimed to be a valid
            # topological order - that is exactly what BLOCKED signals.
            work_items_to_recompute=to_recompute,
            preserved_work_ids=preserved_work_ids,
            rationale="blocked: a cycle was detected among the work items scheduled for recomputation",
            status=PlanStatus.BLOCKED,
        )

    reasons = {
        work_id: _reason_for(work_id, graph, invalidation, to_recompute_set) for work_id in order
    }
    return RecomputationPlan(
        invalidation=invalidation,
        work_items_to_recompute=order,
        preserved_work_ids=preserved_work_ids,
        rationale=f"{len(order)} work item{'s' if len(order) != 1 else ''} scheduled for recomputation",
        reasons=reasons,
        status=PlanStatus.READY,
    )


def _topological_order(candidate_ids: list[str], graph: DependencyGraph) -> tuple[list[str], bool]:
    """Kahn's algorithm restricted to ``candidate_ids``, tie-broken by their
    position in ``candidate_ids``. Returns ``(order, ok)``; ``ok`` is
    ``False`` if a cycle left nodes that could never become ready - in which
    case this never hangs, it just stops and reports the failure.
    """
    candidate_set = set(candidate_ids)
    in_degree: dict[str, int] = {cid: 0 for cid in candidate_ids}
    successors: dict[str, list[str]] = {cid: [] for cid in candidate_ids}

    for cid in candidate_ids:
        for edge in graph.get_outgoing_edges(cid):
            if edge.kind is DependencyKind.WORK_TO_WORK and edge.to_id in candidate_set:
                successors[cid].append(edge.to_id)
                in_degree[edge.to_id] += 1

    remaining = set(candidate_ids)
    order: list[str] = []
    while remaining:
        next_id = None
        for cid in candidate_ids:  # scan in the stable tie-break order
            if cid in remaining and in_degree[cid] == 0:
                next_id = cid
                break
        if next_id is None:
            return order, False
        order.append(next_id)
        remaining.discard(next_id)
        for successor in successors[next_id]:
            in_degree[successor] -= 1
    return order, True


def _reason_for(
    work_id: str, graph: DependencyGraph, invalidation: Invalidation, to_recompute_set: set[str]
) -> str:
    """A structured, deterministic one-line reason - never natural-language
    generation, just a template filled from what the graph and Invalidation
    actually contain."""
    for changeset in invalidation.changesets:
        previous = changeset.previous_fact
        if previous is not None and graph.has_dependency(previous.fact_id, work_id):
            return f"depends on changed fact {changeset.key!r}"
    for upstream_id in graph.get_dependencies(work_id):
        if upstream_id in to_recompute_set:
            return f"depends on stale work {upstream_id!r}"
    return "transitively affected by a changed fact"
