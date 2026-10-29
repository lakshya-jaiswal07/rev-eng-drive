"""Invalidation result data model, and the invalidation engine built on it.

``Invalidation`` (Phase 2) is what a dependency-graph walk produces for a
given fact change: which work survived, which was invalidated, which claims
went with it, and why. Phase 5 completes it - the walk itself now exists,
built on ``DependencyGraph`` (Phase 4).

The core invariant this file exists to guarantee, stated once here because
every design decision below serves it: **if a fact changes, only work
transitively dependent on that fact may become invalid; everything else must
remain exactly as it was.** ``invalidate()``/``invalidate_many()`` never
touch a WORK or CLAIM node that is not reachable from a changed fact, and
never touch the graph's structure (edges, registrations) at all - the only
mutation is flipping ``status`` on the WorkItem/Claim objects the walk
actually reaches.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from .changes import ChangeKind, ChangeSet
from .claims import ClaimStatus
from .facts import Fact
from .graph import Dependency, DependencyGraph, NodeKind
from .work_lifecycle import invalidate_work


@dataclass
class Invalidation:
    """The outcome of invalidating everything downstream of one or more
    ChangeSets.

    ``changeset`` is the Phase 2 field, unchanged: for a single-changeset
    ``invalidate()`` call it is that changeset; for a multi-changeset
    ``invalidate_many()`` call it is the first one, kept for backward
    compatibility with code that only knows about a single changeset.
    ``changesets`` (Phase 5) is the authoritative full list either way - a
    single-element list for ``invalidate()``, the full batch for
    ``invalidate_many()``.

    The four ``*_ids`` properties below are convenience views derived from
    the stored lists/objects, not separately stored fields - there is
    exactly one place each of them can get out of sync with reality: nowhere.
    """

    changeset: ChangeSet
    invalidation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    changesets: list[ChangeSet] = field(default_factory=list)
    changed_facts: list[Fact] = field(default_factory=list)
    kept_work_ids: list[str] = field(default_factory=list)
    invalidated_work_ids: list[str] = field(default_factory=list)
    invalidated_claim_ids: list[str] = field(default_factory=list)
    # Phase 7: which of invalidated_claim_ids had already been spoken to the
    # user at the moment of invalidation, and which hadn't - the exact
    # distinction the claim ledger's silent-vs-retraction-eligible rule
    # turns on. Both are subsets of invalidated_claim_ids, computed once by
    # invalidate_many() (which already visits each claim to flip its status,
    # so checking spoken_at there is free) rather than left for every caller
    # to recompute by re-reading claim.spoken_at themselves.
    spoken_invalidated_claim_ids: list[str] = field(default_factory=list)
    unspoken_invalidated_claim_ids: list[str] = field(default_factory=list)
    affected_dependencies: list[Dependency] = field(default_factory=list)
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def changeset_id(self) -> str:
        return self.changeset.changeset_id

    @property
    def changeset_ids(self) -> list[str]:
        return [cs.changeset_id for cs in self.changesets]

    @property
    def changed_fact_ids(self) -> list[str]:
        return [f.fact_id for f in self.changed_facts]

    @property
    def affected_dependency_ids(self) -> list[str]:
        return [d.dependency_id for d in self.affected_dependencies]

    def to_dict(self) -> dict[str, Any]:
        return {
            "invalidation_id": self.invalidation_id,
            "changeset": self.changeset.to_dict(),
            "changeset_id": self.changeset_id,
            "changesets": [cs.to_dict() for cs in self.changesets],
            "changeset_ids": self.changeset_ids,
            "changed_facts": [f.to_dict() for f in self.changed_facts],
            "changed_fact_ids": self.changed_fact_ids,
            "kept_work_ids": list(self.kept_work_ids),
            "invalidated_work_ids": list(self.invalidated_work_ids),
            "invalidated_claim_ids": list(self.invalidated_claim_ids),
            "spoken_invalidated_claim_ids": list(self.spoken_invalidated_claim_ids),
            "unspoken_invalidated_claim_ids": list(self.unspoken_invalidated_claim_ids),
            "affected_dependencies": [d.to_dict() for d in self.affected_dependencies],
            "affected_dependency_ids": self.affected_dependency_ids,
            "reason": self.reason,
            "created_at": self.created_at,
            "provenance": dict(self.provenance),
        }


def invalidate(graph: DependencyGraph, changeset: ChangeSet) -> Invalidation:
    """Invalidate everything downstream of one ChangeSet. See ``invalidate_many``."""
    return invalidate_many(graph, [changeset])


def invalidate_many(graph: DependencyGraph, changesets: Sequence[ChangeSet]) -> Invalidation:
    """Invalidate everything downstream of a batch of ChangeSets, as one
    merged, deduplicated result.

    Per changeset kind:

    * ``UNCHANGED`` - skipped entirely. Required: an unchanged observation
      must never invalidate anything.
    * ``NEW`` - walked exactly like ``CHANGED`` (not special-cased away).
      A brand-new fact's ``fact_id`` cannot already have any dependents
      registered against it, since nothing could reference an id that did
      not exist until this call - so the walk is guaranteed to find nothing.
      That guarantee is a property of the graph, not something this
      function assumes by skipping NEW outright.
    * ``CHANGED`` - walked, and contributes to ``changed_facts``/``reason``.

    Across the whole batch: a WORK/CLAIM id reachable from more than one
    changed fact (or reachable transitively more than once) appears exactly
    once in the result. ``kept_work_ids`` is every registered WORK id minus
    whatever was invalidated - so it is always accurate even for a batch
    that changes nothing.

    Mutation: the only side effect is setting ``status`` on the WorkItem/
    Claim objects actually reached (``STALE`` / ``INVALIDATED`` - see below).
    Nothing about the graph's structure changes. Calling this repeatedly
    with equivalent input is safe: every mutation just re-sets a field to
    the value it already holds, and no list here is ever appended to across
    calls - each call builds its result from scratch by reading current
    graph state.
    """
    if not changesets:
        raise ValueError("invalidate_many requires at least one changeset")
    changesets = list(changesets)

    changed_facts: list[Fact] = []
    invalidated_work_ids: list[str] = []
    invalidated_claim_ids: list[str] = []
    affected_dependencies: list[Dependency] = []
    seen_work: set[str] = set()
    seen_claim: set[str] = set()
    seen_dependency: set[str] = set()
    reasons: list[str] = []

    for changeset in changesets:
        if changeset.kind is ChangeKind.UNCHANGED:
            continue

        fact = changeset.new_fact
        if fact is None:
            continue

        # Which fact_id to walk from is the one subtle part of this function.
        # A CHANGED fact's *new* fact_id is brand new - nothing could have
        # registered a dependency against an id that did not exist until
        # this call. Any existing FACT_TO_WORK edges were necessarily
        # registered against the fact_id that was current *before* this
        # change, i.e. `changeset.previous_fact.fact_id` (unchanged by the
        # supersession - `FactNotebook.observe` preserves a fact's own id
        # when it closes it out, only its status/superseded_by move). So
        # CHANGED walks from `previous_fact`, not `new_fact`. NEW has no
        # previous fact at all, so `new_fact.fact_id` is the only id there
        # is - and, per the docstring above, is guaranteed to have no edges
        # yet regardless.
        if changeset.kind is ChangeKind.CHANGED:
            changed_facts.append(fact)
            previous = changeset.previous_fact
            if previous is not None:
                reasons.append(f"{changeset.key} changed ({previous.value!r} -> {fact.value!r})")
            else:
                reasons.append(f"{changeset.key} changed (-> {fact.value!r})")
            walk_from = previous.fact_id if previous is not None else fact.fact_id
        else:
            walk_from = fact.fact_id

        order, edges = graph.get_transitive_dependents_with_edges(walk_from)

        for dependency in edges:
            if dependency.dependency_id not in seen_dependency:
                seen_dependency.add(dependency.dependency_id)
                affected_dependencies.append(dependency)

        for node_id in order:
            kind = graph.node_kind(node_id)
            if kind is NodeKind.WORK:
                if node_id not in seen_work:
                    seen_work.add(node_id)
                    invalidated_work_ids.append(node_id)
            elif kind is NodeKind.CLAIM:
                if node_id not in seen_claim:
                    seen_claim.add(node_id)
                    invalidated_claim_ids.append(node_id)
            # Anything else (e.g. an id with no registration at all) cannot
            # occur here: edges never point *to* a FACT node, so a FACT id
            # is never a dependent of anything.

    # Status transitions: mutate the registered objects in place, the same
    # convention `goals.py` already uses for `Goal.status` (not the
    # supersede-a-copy discipline `FactNotebook` uses for `Fact` - a WorkItem
    # has no version history to protect, it is just a status flag). Routed
    # through `invalidate_work` (Phase M1-B) rather than a direct assignment
    # so PENDING/RUNNING/VALID all move to STALE through one place that
    # understands the execution-lifecycle state machine - a RUNNING item
    # goes stale exactly like a PENDING or already-completed one, per the
    # brief's invalidation semantics, and CANCELLED/FAILED items (already not
    # current) are correctly left alone rather than resurrected into STALE.
    for work_id in invalidated_work_ids:
        work = graph.get_work(work_id)
        if work is not None:
            invalidate_work(work)

    spoken_invalidated_claim_ids: list[str] = []
    unspoken_invalidated_claim_ids: list[str] = []
    for claim_id in invalidated_claim_ids:
        claim = graph.get_claim(claim_id)
        if claim is not None:
            claim.status = ClaimStatus.INVALIDATED
            # `claim.spoken_at` is deliberately left untouched. A claim that
            # was already spoken keeps its spoken_at timestamp even after
            # being invalidated, so a later phase (the claim ledger, Phase 7)
            # can tell "safe to drop silently" (spoken_at is None) apart from
            # "needs a user-visible retraction" (spoken_at is not None) just
            # by reading that one field. This function still does not decide
            # what to do about that distinction - it only classifies the
            # already-computed invalidated set by it, below, so a caller
            # does not have to re-read claim.spoken_at itself. Whether a
            # Retraction actually gets created is a separate, explicit
            # decision made through ``BackspaceCore.retract_claim`` - never
            # automatic just because a claim was spoken and invalidated.
            if claim.spoken_at is not None:
                spoken_invalidated_claim_ids.append(claim_id)
            else:
                unspoken_invalidated_claim_ids.append(claim_id)

    kept_work_ids = [work_id for work_id in graph.all_work_ids() if work_id not in seen_work]

    reason = "; ".join(reasons) if reasons else "no semantic change"

    return Invalidation(
        changeset=changesets[0],
        changesets=changesets,
        changed_facts=changed_facts,
        kept_work_ids=kept_work_ids,
        invalidated_work_ids=invalidated_work_ids,
        invalidated_claim_ids=invalidated_claim_ids,
        spoken_invalidated_claim_ids=spoken_invalidated_claim_ids,
        unspoken_invalidated_claim_ids=unspoken_invalidated_claim_ids,
        affected_dependencies=affected_dependencies,
        reason=reason,
    )
