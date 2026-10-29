"""WorkItem/Dependency data models, and the DependencyGraph built from them.

``WorkItem`` is anything the agent layer did that a fact change could make
stale - a retrieval, a plan step, a tool call, a priced quote, an assembled
recommendation. ``kind`` is deliberately a free-form string rather than a
closed enum: ``"flight_search"``, ``"hotel_search"``, ``"price_calculation"``
and ``"recommendation"`` all have to be representable, and new kinds will
keep showing up as the agent layer grows, so a fixed enum would just mean
editing this file every time someone adds one.

``Dependency`` is a single generic edge, deliberately not three separate edge
types, because the graph has to represent all three relationships the
architecture calls for with one shape: Fact -> WorkItem, WorkItem ->
WorkItem, WorkItem -> Claim.

``DependencyGraph`` (Phase 4) is the traversal structure built out of those
two shapes: a session-local, deterministic answer to "if this node changes,
what does it transitively reach?". It still does not decide what "reach"
*means* for invalidation - that is a later phase's job (walking this graph is
how it will get its answer, not something this file does itself).

One deliberate asymmetry, documented here once rather than at every call
site: WORK and CLAIM nodes must be registered explicitly (``register_work``/
``register_claim``) before any edge can reference them, and referencing an
unregistered one is a typed error. FACT nodes are not separately registered
at all - a fact's ``fact_id`` is only ever produced by ``FactNotebook``
(Phase 3), which is already the authoritative store for it; requiring a
second, parallel ``register_fact()`` call here would just be bookkeeping this
file has no way to keep honestly in sync with that store. So a ``fact_id``
used as the source of a ``FACT_TO_WORK`` edge is accepted as-is, unchecked.
This is the one place this module creates a graph reference without
requiring prior registration, and it is intentional, not an oversight.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .claims import Claim


class WorkStatus(str, Enum):
    """Where a work item stands - both relative to the facts it depended on,
    and (Phase M1-B) what its current execution attempt is actually doing.

    ``PENDING``/``STALE``/``INVALIDATED``/``RETRACTED``/``RECOMPUTED`` are
    unchanged from Phase 2. ``RUNNING``/``CANCELLED``/``FAILED`` are new,
    additive execution states. ``VALID`` - already defined since Phase 2 but
    never assigned by any code path before this phase - is what this phase
    uses as "completed successfully, current, trust it": the brief's own
    vocabulary calls this state "completed", but the existing enum member is
    reused rather than adding a same-meaning ``COMPLETED`` alongside it,
    since ``VALID`` already means exactly that and two names for one state
    would be the duplication the brief asks this phase to avoid.

    See ``work_lifecycle.py`` for the legal-transition table between these -
    this enum only declares the vocabulary, not the state machine.
    """

    PENDING = "pending"
    RUNNING = "running"
    VALID = "valid"
    STALE = "stale"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INVALIDATED = "invalidated"
    RETRACTED = "retracted"
    RECOMPUTED = "recomputed"


class DependencyKind(str, Enum):
    """The three edge shapes the architecture requires."""

    FACT_TO_WORK = "fact_to_work"
    WORK_TO_WORK = "work_to_work"
    WORK_TO_CLAIM = "work_to_claim"


@dataclass
class WorkItem:
    """Something the agent layer produced that may depend on facts/evidence.

    ``execution_attempt`` (Phase M1-B) is a monotonic counter - the same
    pattern as ``Fact.version`` - incremented each time this work_id starts a
    new execution attempt (see ``work_lifecycle.start_work``). It exists
    for exactly one reason: a ``work_id`` alone cannot tell a late-arriving
    result from an old, superseded attempt apart from a legitimate result of
    the *current* attempt, once a work item has been restarted after going
    stale. Comparing the attempt number a result carries against this field
    (``work_lifecycle.is_result_current``) is what makes that distinction
    possible without inventing a second identity system alongside ``work_id``.
    """

    kind: str
    work_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: WorkStatus = WorkStatus.PENDING
    turn_id: str = ""
    goal_id: str | None = None
    output: Any = None
    depends_on_facts: list[str] = field(default_factory=list)
    depends_on_work: list[str] = field(default_factory=list)
    execution_attempt: int = 0
    created_at: float = field(default_factory=time.time)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "kind": self.kind,
            "status": self.status.value,
            "turn_id": self.turn_id,
            "goal_id": self.goal_id,
            "output": self.output,
            "depends_on_facts": list(self.depends_on_facts),
            "depends_on_work": list(self.depends_on_work),
            "execution_attempt": self.execution_attempt,
            "created_at": self.created_at,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class Dependency:
    """One generic edge: ``from_id`` -> ``to_id``, typed by ``kind``.

    ``from_id``/``to_id`` are plain ids (a ``fact_id`` or a ``work_id``,
    depending on ``kind``) rather than typed references, so this file never
    has to import ``Fact`` or ``Claim`` to describe the relationship between
    them.
    """

    kind: DependencyKind
    from_id: str
    to_id: str
    dependency_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependency_id": self.dependency_id,
            "kind": self.kind.value,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "created_at": self.created_at,
        }


class NodeKind(str, Enum):
    """What a registered graph node is. Distinct from ``DependencyKind``,
    which names the *edges* between nodes, not the nodes themselves."""

    FACT = "fact"
    WORK = "work"
    CLAIM = "claim"


# The node kinds each DependencyKind's endpoints must be. Also the single
# place that answers "which relationships are supported" - a DependencyKind
# not present here (impossible today, since the enum only has three members,
# but kept explicit rather than assumed) is unsupported.
_EDGE_ENDPOINTS: dict[DependencyKind, tuple[NodeKind, NodeKind]] = {
    DependencyKind.FACT_TO_WORK: (NodeKind.FACT, NodeKind.WORK),
    DependencyKind.WORK_TO_WORK: (NodeKind.WORK, NodeKind.WORK),
    DependencyKind.WORK_TO_CLAIM: (NodeKind.WORK, NodeKind.CLAIM),
}


class DependencyGraphError(Exception):
    """Base class for every error this module raises."""


class UnsupportedDependencyKindError(DependencyGraphError):
    """Raised for anything that is not one of the three known DependencyKind values."""

    def __init__(self, kind: Any) -> None:
        super().__init__(f"{kind!r} is not a supported dependency kind.")
        self.kind = kind


class NodeNotRegisteredError(DependencyGraphError):
    """Raised when an edge references a WORK or CLAIM node that was never registered."""

    def __init__(self, node_id: str, expected_kind: NodeKind) -> None:
        super().__init__(f"No {expected_kind.value} node registered with id {node_id!r}.")
        self.node_id = node_id
        self.expected_kind = expected_kind


class NodeKindMismatchError(DependencyGraphError):
    """Raised when an edge references a real node of the wrong kind

    (e.g. a WORK id used where a CLAIM id was expected) - this is the check
    that rejects a structurally wrong relationship even when every id
    involved happens to be registered as *something*.
    """

    def __init__(self, node_id: str, expected_kind: NodeKind, actual_kind: NodeKind) -> None:
        super().__init__(
            f"Node {node_id!r} is registered as {actual_kind.value}, not {expected_kind.value}."
        )
        self.node_id = node_id
        self.expected_kind = expected_kind
        self.actual_kind = actual_kind


class DependencyCycleError(DependencyGraphError):
    """Raised when adding an edge would close a cycle."""

    def __init__(self, from_id: str, to_id: str) -> None:
        super().__init__(f"Adding {from_id!r} -> {to_id!r} would create a cycle.")
        self.from_id = from_id
        self.to_id = to_id


class DependencyGraph:
    """Session-local, deterministic dependency graph over Fact/WorkItem/Claim ids.

    Every mapping below is created fresh in ``__init__`` - no module-level
    state, so two ``DependencyGraph()``s never share a node or an edge, and
    ``reset()`` on one never touches another.

    Ordering: every query method returns ids in a single, documented,
    deterministic order - never raw ``dict``/``set`` iteration order left to
    chance. ``get_direct_dependents``/``get_dependencies`` return edges in
    the order they were registered (Python dicts and lists already preserve
    insertion order; this class relies on that deliberately rather than
    sorting, since registration order is itself meaningful information - it
    reflects the order the caller actually discovered these relationships).
    ``get_transitive_dependents`` performs a breadth-first walk over that
    same insertion-ordered adjacency, appending each node to the result
    exactly once, at the moment of its first discovery. That gives a stable
    "closest first, then registration order among ties" ordering with no
    reliance on hashing.
    """

    def __init__(self) -> None:
        self._work: dict[str, WorkItem] = {}
        self._claims: dict[str, Claim] = {}
        # Kind tag for every registered WORK/CLAIM node. FACT nodes are
        # deliberately absent from this - see the module docstring.
        self._node_kind: dict[str, NodeKind] = {}
        self._edges: dict[tuple[DependencyKind, str, str], Dependency] = {}
        self._forward: dict[str, list[str]] = {}  # id -> ids it points to
        self._reverse: dict[str, list[str]] = {}  # id -> ids that point to it
        # Parallel to `_forward`, but holding the actual Dependency objects
        # rather than just target ids. Added in Phase 5 for the invalidation
        # engine, which needs real dependency_ids for Invalidation's
        # affected_dependencies - get_direct_dependents/get_dependencies
        # deliberately stay id-only and untouched.
        self._forward_edges: dict[str, list[Dependency]] = {}

    # -- registration --------------------------------------------------------
    def register_work(self, work: WorkItem) -> WorkItem:
        """Register a WORK node. Idempotent: a second call with the same
        ``work_id`` is a no-op that returns the *originally* registered
        item - the first registration wins, the second is not inspected for
        conflicting fields. Updating a work item's fields is out of scope
        for this phase."""
        existing = self._work.get(work.work_id)
        if existing is not None:
            return existing
        self._work[work.work_id] = work
        self._node_kind[work.work_id] = NodeKind.WORK
        self._forward.setdefault(work.work_id, [])
        self._reverse.setdefault(work.work_id, [])
        return work

    def register_claim(self, claim: Claim) -> Claim:
        """Register a CLAIM node. Idempotent, same rule as ``register_work``."""
        existing = self._claims.get(claim.claim_id)
        if existing is not None:
            return existing
        self._claims[claim.claim_id] = claim
        self._node_kind[claim.claim_id] = NodeKind.CLAIM
        self._forward.setdefault(claim.claim_id, [])
        self._reverse.setdefault(claim.claim_id, [])
        return claim

    def register_dependency(self, kind: DependencyKind, from_id: str, to_id: str) -> Dependency:
        """Register one edge. Validates node kinds, rejects a would-be
        cycle, and is idempotent for an exact repeat of an edge already
        registered (same kind, same endpoints)."""
        if not isinstance(kind, DependencyKind) or kind not in _EDGE_ENDPOINTS:
            raise UnsupportedDependencyKindError(kind)

        expected_from, expected_to = _EDGE_ENDPOINTS[kind]
        if expected_from is not NodeKind.FACT:
            self._require_node(from_id, expected_from)
        self._require_node(to_id, expected_to)

        edge_key = (kind, from_id, to_id)
        existing = self._edges.get(edge_key)
        if existing is not None:
            return existing  # exact duplicate: no second logical edge

        if self._can_reach(to_id, from_id):
            raise DependencyCycleError(from_id, to_id)

        dependency = Dependency(kind=kind, from_id=from_id, to_id=to_id)
        self._edges[edge_key] = dependency
        self._forward.setdefault(from_id, []).append(to_id)
        self._forward.setdefault(to_id, [])
        self._reverse.setdefault(to_id, []).append(from_id)
        self._reverse.setdefault(from_id, [])
        self._forward_edges.setdefault(from_id, []).append(dependency)
        self._forward_edges.setdefault(to_id, [])
        return dependency

    # -- queries (read-only: none of these mutate the graph) -----------------
    def get_dependencies(self, node_id: str) -> list[str]:
        """Direct predecessors of ``node_id`` - what it directly depends on."""
        return list(self._reverse.get(node_id, []))

    def get_direct_dependents(self, node_id: str) -> list[str]:
        """Nodes with a direct edge *from* ``node_id``."""
        return list(self._forward.get(node_id, []))

    def get_transitive_dependents(self, node_id: str) -> list[str]:
        """Every node reachable from ``node_id`` by following edges forward,
        each listed exactly once, ordered by first discovery (breadth-first).
        Does not include ``node_id`` itself."""
        visited = {node_id}
        order: list[str] = []
        queue: list[str] = list(self._forward.get(node_id, []))
        for candidate in queue:
            visited.add(candidate)
            order.append(candidate)
        head = 0
        while head < len(queue):
            current = queue[head]
            head += 1
            for neighbour in self._forward.get(current, []):
                if neighbour in visited:
                    continue
                visited.add(neighbour)
                order.append(neighbour)
                queue.append(neighbour)
        return order

    def get_dependents(self, node_id: str) -> list[str]:
        """Alias for ``get_transitive_dependents`` - "what depends on this"
        almost always means the full closure, not just the direct edges.
        Provided under this name so callers do not have to choose between
        ``get_dependents``/``get_transitive_dependents`` at every call site;
        it is not a third, different semantic."""
        return self.get_transitive_dependents(node_id)

    def has_dependency(self, source_id: str, target_id: str) -> bool:
        """Whether a *direct* edge ``source_id -> target_id`` exists (of any
        DependencyKind). Not transitive - use ``get_transitive_dependents``
        for reachability."""
        return target_id in self._forward.get(source_id, ())

    def get_outgoing_edges(self, node_id: str) -> list[Dependency]:
        """The actual ``Dependency`` objects for every direct edge from
        ``node_id``, in registration order. Where ``get_direct_dependents``
        gives you just the target ids, this gives you the edges themselves -
        their ``kind`` and stable ``dependency_id`` - which Phase 5's
        invalidation engine needs to populate ``Invalidation.
        affected_dependencies``."""
        return list(self._forward_edges.get(node_id, []))

    def get_transitive_dependents_with_edges(self, node_id: str) -> tuple[list[str], list[Dependency]]:
        """Everything ``get_transitive_dependents`` returns, plus every
        ``Dependency`` edge crossed to reach it.

        A separate breadth-first implementation from ``get_transitive_
        dependents`` rather than a refactor of it, deliberately: Phase 4's
        method and its tests are load-bearing and untouched, and this method
        is cross-checked against it (see ``test_graph.py``) rather than
        trusted to stay in sync by construction. The traversal order is
        identical: each node is discovered once, at the earliest point a
        breadth-first walk over insertion-ordered edges would reach it.
        """
        visited = {node_id}
        order: list[str] = []
        edges: list[Dependency] = []
        frontier = [node_id]
        while frontier:
            next_frontier: list[str] = []
            for current in frontier:
                for edge in self._forward_edges.get(current, []):
                    edges.append(edge)
                    if edge.to_id not in visited:
                        visited.add(edge.to_id)
                        order.append(edge.to_id)
                        next_frontier.append(edge.to_id)
            frontier = next_frontier
        return order, edges

    def node_kind(self, node_id: str) -> NodeKind | None:
        """The registered kind of ``node_id`` (WORK or CLAIM), or ``None`` if
        it is not a registered node - which is also what a bare FACT id
        (never separately registered) looks like to this method."""
        return self._node_kind.get(node_id)

    def get_work(self, work_id: str) -> WorkItem | None:
        return self._work.get(work_id)

    def get_claim(self, claim_id: str) -> Claim | None:
        return self._claims.get(claim_id)

    def all_work_ids(self) -> list[str]:
        """Every registered WORK id, in registration order."""
        return list(self._work.keys())

    def all_claim_ids(self) -> list[str]:
        """Every registered CLAIM id, in registration order."""
        return list(self._claims.keys())

    def reset(self) -> None:
        """Clear this graph's nodes and edges. Other instances are untouched."""
        self._work.clear()
        self._claims.clear()
        self._node_kind.clear()
        self._edges.clear()
        self._forward.clear()
        self._reverse.clear()
        self._forward_edges.clear()

    # -- internals -------------------------------------------------------------
    def _require_node(self, node_id: str, expected_kind: NodeKind) -> None:
        actual = self._node_kind.get(node_id)
        if actual is None:
            raise NodeNotRegisteredError(node_id, expected_kind)
        if actual is not expected_kind:
            raise NodeKindMismatchError(node_id, expected_kind, actual)

    def _can_reach(self, start: str, target: str) -> bool:
        """Whether ``target`` is reachable from ``start`` via existing
        forward edges - used to detect a would-be cycle before it is created."""
        if start == target:
            return True
        seen = {start}
        stack = list(self._forward.get(start, []))
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(self._forward.get(node, []))
        return False
