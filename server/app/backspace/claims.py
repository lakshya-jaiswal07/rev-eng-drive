"""Claim and Evidence data models, and the claim-ledger lifecycle functions.

A ``Claim`` is one unit of the answer the agent actually said (or is about to
say) - the structured counterpart of a bulleted, cited line like the ones
``providers/mock.py`` already produces (``"- sentence  [doc_id]"``). Giving
it its own lifecycle is what lets BACKSPACE retract *part* of an answer
instead of the whole thing.

``Evidence`` mirrors the plain dict shape already produced by
``runtime.py``'s ``_hits_to_evidence`` (``doc_id`` / ``title`` / ``snippet`` /
``source``) so BACKSPACE can wrap that output without retrieval.py or
runtime.py changing at all.

Phase 7 adds the lifecycle functions - ``mark_claim_spoken``,
``invalidate_claim``, ``require_claim_retractable``, ``supersede_claim``,
``get_claim_history`` - each taking a graph and a claim_id, resolving and
raising a typed error if the claim is not registered. They accept the graph
as a plain ``graph`` parameter, type-hinted as ``DependencyGraph`` in a
string that is never evaluated (``from __future__ import annotations``
postpones every annotation) and never imported: this file calls exactly two
methods on it (``get_claim``, and ``register_claim`` for supersession),
resolved by ordinary duck typing at call time. That is deliberate, not an
oversight - ``graph.py`` already imports *this* file for ``Claim``, so an
import the other way would be circular. The same two methods work on any
object shaped like a ``DependencyGraph``, so nothing here actually needs the
class itself.

Constructing the actual ``Retraction`` result object is deliberately **not**
done here: ``Retraction`` lives in ``core.py`` (it already did, from Phase
2), and this file has no reason to import it just to avoid a *different*
circular import (``core.py`` imports this file for ``Claim``/``ClaimStatus``
already). ``require_claim_retractable`` does the validation and hands back
the live, still-un-mutated ``Claim``; ``BackspaceCore.retract_claim`` (in
``core.py``) does the snapshot-then-mutate-then-construct-the-Retraction
step, and owns the one piece of state a retraction genuinely needs that nothing
here does: "have we already retracted this claim once?" (session-scoped, so
it lives on the facade, not in this stateless module).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClaimStatus(str, Enum):
    """The lifecycle a claim can occupy.

    Transitions between these (e.g. SPOKEN -> INVALIDATED) are claim-lifecycle
    behaviour and land in a later phase; this enum only has to be able to
    represent every state, not move between them.
    """

    GENERATED = "generated"
    SPOKEN = "spoken"
    HEARD = "heard"
    VALID = "valid"
    INVALIDATED = "invalidated"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Evidence:
    """Compatible with the evidence dict already threaded through the runtime.

    Deliberately the same four fields, in the same names, as
    ``{"doc_id": ..., "title": ..., "snippet": ..., "source": ...}`` - so
    existing evidence dicts convert both ways without loss.
    """

    doc_id: str
    title: str = ""
    snippet: str = ""
    source: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        return cls(
            doc_id=data["doc_id"],
            title=data.get("title", ""),
            snippet=data.get("snippet", ""),
            source=data.get("source", ""),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "snippet": self.snippet,
            "source": self.source,
        }


@dataclass
class Claim:
    """One claim the answer makes, with what it depends on to remain true.

    Phase 7 additions, all optional/additive: ``supersedes``/``superseded_by``
    (mirroring ``Fact``'s own supersession-chain fields exactly, for the
    ``INVALIDATED -> SUPERSEDED`` transition once a replacement claim exists),
    ``invalidation_reason`` (set by ``invalidate_claim``), and
    ``retraction_id`` (set once ``BackspaceCore.retract_claim`` succeeds - the
    id of the one canonical ``Retraction`` for this claim, not the object
    itself, matching the id-linking convention already used everywhere else
    in this package rather than embedding a nested object).
    """

    text: str
    claim_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    work_id: str | None = None
    turn_id: str = ""
    goal_id: str | None = None
    evidence: Evidence | None = None
    status: ClaimStatus = ClaimStatus.GENERATED
    depends_on_facts: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    spoken_at: float | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    invalidation_reason: str | None = None
    retraction_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "work_id": self.work_id,
            "turn_id": self.turn_id,
            "goal_id": self.goal_id,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
            "status": self.status.value,
            "depends_on_facts": list(self.depends_on_facts),
            "created_at": self.created_at,
            "spoken_at": self.spoken_at,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "invalidation_reason": self.invalidation_reason,
            "retraction_id": self.retraction_id,
            "provenance": dict(self.provenance),
        }


class ClaimNotFoundError(Exception):
    """Raised when a ``claim_id`` is referenced but the graph has never
    registered it. Mirrors ``facts.py``'s ``FactNotFoundError``."""

    def __init__(self, claim_id: str) -> None:
        super().__init__(f"No claim with id {claim_id!r}.")
        self.claim_id = claim_id


class ClaimNotSpokenError(Exception):
    """Raised by ``require_claim_retractable``: only a claim the user
    actually heard (``spoken_at`` set) can be retracted - retracting
    something nobody heard has nothing to take back."""

    def __init__(self, claim_id: str) -> None:
        super().__init__(f"Claim {claim_id!r} was never spoken; nothing to retract.")
        self.claim_id = claim_id


class ClaimNotInvalidatedError(Exception):
    """Raised by ``require_claim_retractable``: a claim that is still valid
    (or was never invalidated) has nothing to retract yet."""

    def __init__(self, claim_id: str, status: ClaimStatus) -> None:
        super().__init__(
            f"Claim {claim_id!r} is {status.value!r}, not invalidated; nothing to retract."
        )
        self.claim_id = claim_id
        self.status = status


def mark_claim_spoken(graph: DependencyGraph, claim_id: str) -> None:
    """Record that ``claim_id`` was actually said to the user.

    Idempotent: a second call leaves ``spoken_at`` exactly as it was (never
    re-timestamped) and never regresses ``status`` - it only advances
    ``GENERATED -> SPOKEN``. A claim already past that (``INVALIDATED``,
    ``RETRACTED``, ``SUPERSEDED``, ...) keeps its own status; "spoken" is a
    historical fact layered on top via ``spoken_at``, not a status value
    itself, precisely so marking a claim spoken can never erase or downgrade
    whatever else has since happened to it.
    """
    claim = graph.get_claim(claim_id)
    if claim is None:
        raise ClaimNotFoundError(claim_id)
    if claim.spoken_at is None:
        claim.spoken_at = time.time()
    if claim.status is ClaimStatus.GENERATED:
        claim.status = ClaimStatus.SPOKEN


def invalidate_claim(graph: DependencyGraph, claim_id: str, reason: str) -> None:
    """Mark one claim invalid, directly - the single-claim counterpart of
    what the bulk graph-walking ``invalidate()`` (invalidation.py) already
    does per claim it reaches. Does **not** create a Retraction - see the
    module docstring for why that split exists.

    Idempotent: a repeat call just re-sets ``status``/``invalidation_reason``
    to the same (or latest) values - no history of reasons is kept, and a
    claim already past INVALIDATED (``RETRACTED``/``SUPERSEDED``) is left
    alone rather than regressed backward.
    """
    claim = graph.get_claim(claim_id)
    if claim is None:
        raise ClaimNotFoundError(claim_id)
    if claim.status in (ClaimStatus.RETRACTED, ClaimStatus.SUPERSEDED):
        return
    claim.status = ClaimStatus.INVALIDATED
    claim.invalidation_reason = reason


def require_claim_retractable(graph: DependencyGraph, claim_id: str) -> Claim:
    """Validate that ``claim_id`` can be retracted and return the live
    ``Claim`` - unmutated by this function. Raises ``ClaimNotFoundError``,
    ``ClaimNotSpokenError`` or ``ClaimNotInvalidatedError``.

    Deliberately read-only: constructing the ``Retraction`` and flipping
    ``status`` to ``RETRACTED`` is ``BackspaceCore.retract_claim``'s job (see
    the module docstring), so a caller that only wants to check eligibility
    without committing to a retraction can call this safely.
    """
    claim = graph.get_claim(claim_id)
    if claim is None:
        raise ClaimNotFoundError(claim_id)
    if claim.spoken_at is None:
        raise ClaimNotSpokenError(claim_id)
    if claim.status not in (ClaimStatus.INVALIDATED, ClaimStatus.RETRACTED):
        raise ClaimNotInvalidatedError(claim_id, claim.status)
    return claim


def supersede_claim(graph: DependencyGraph, old_claim_id: str, new_claim: Claim) -> Claim:
    """Register ``new_claim`` as the replacement for ``old_claim_id``.

    Links both directions (``new_claim.supersedes`` / ``old.superseded_by``)
    and moves the old claim to ``SUPERSEDED`` - unless it was already
    ``RETRACTED`` (a user-facing retraction is not quietly overwritten by a
    later supersession). This function only records the relationship; it
    does not decide *when* a replacement claim should be generated - that is
    recomputation's job, explicitly out of scope for this phase.
    """
    old_claim = graph.get_claim(old_claim_id)
    if old_claim is None:
        raise ClaimNotFoundError(old_claim_id)
    registered = graph.register_claim(new_claim)
    registered.supersedes = old_claim_id
    old_claim.superseded_by = registered.claim_id
    if old_claim.status is not ClaimStatus.RETRACTED:
        old_claim.status = ClaimStatus.SUPERSEDED
    return registered


def get_claim_history(graph: DependencyGraph, claim_id: str) -> list[Claim]:
    """The full supersession chain containing ``claim_id``, oldest first.

    A claim with no supersession relationships at all returns as a single-
    element list containing itself - "history of one" generalises naturally
    rather than being a special case. An unknown ``claim_id`` returns ``[]``,
    matching the lenient-read convention ``FactNotebook``/``DependencyGraph``
    already use elsewhere in this package. Guarded against a corrupted/
    cyclic chain by tracking visited ids in both directions.
    """
    claim = graph.get_claim(claim_id)
    if claim is None:
        return []

    root = claim
    seen_backward = {root.claim_id}
    while root.supersedes is not None and root.supersedes not in seen_backward:
        earlier = graph.get_claim(root.supersedes)
        if earlier is None:
            break
        seen_backward.add(earlier.claim_id)
        root = earlier

    chain = [root]
    seen_forward = {root.claim_id}
    current = root
    while current.superseded_by is not None and current.superseded_by not in seen_forward:
        later = graph.get_claim(current.superseded_by)
        if later is None:
            break
        seen_forward.add(later.claim_id)
        chain.append(later)
        current = later
    return chain
