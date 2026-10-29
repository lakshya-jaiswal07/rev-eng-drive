from __future__ import annotations

import pytest

from app.backspace import (
    Claim,
    Dependency,
    DependencyCycleError,
    DependencyGraph,
    DependencyKind,
    NodeKind,
    NodeKindMismatchError,
    NodeNotRegisteredError,
    UnsupportedDependencyKindError,
    WorkItem,
    WorkStatus,
)

FTW = DependencyKind.FACT_TO_WORK
WTW = DependencyKind.WORK_TO_WORK
WTC = DependencyKind.WORK_TO_CLAIM


# ===========================================================================
# Phase 2 model tests (WorkItem, WorkStatus, Dependency, DependencyKind) -
# unchanged from Phase 2/3, kept exactly as they were.
# ===========================================================================


@pytest.mark.parametrize(
    "kind", ["flight_search", "hotel_search", "price_calculation", "recommendation"]
)
def test_work_item_kind_is_free_form_and_covers_required_kinds(kind):
    work = WorkItem(kind=kind)
    assert work.kind == kind
    assert work.status is WorkStatus.PENDING
    assert work.work_id


def test_work_item_can_declare_dependencies_without_resolving_them():
    work = WorkItem(
        kind="price_calculation",
        turn_id="t1",
        goal_id="g1",
        depends_on_facts=["f1", "f2"],
        depends_on_work=["w0"],
    )
    assert work.depends_on_facts == ["f1", "f2"]
    assert work.depends_on_work == ["w0"]


def test_work_item_output_and_provenance_are_free_form():
    work = WorkItem(kind="flight_search", output={"quote_inr": 5400}, provenance={"tool": "price_quote"})
    assert work.output == {"quote_inr": 5400}
    assert work.provenance == {"tool": "price_quote"}


def test_work_status_enum_values():
    # "running"/"cancelled"/"failed" (Phase M1-B) added for the in-flight
    # execution lifecycle; "valid" is reused as that lifecycle's "completed
    # successfully" state rather than adding a same-meaning "completed".
    assert {s.value for s in WorkStatus} == {
        "pending", "running", "valid", "stale", "cancelled", "failed",
        "invalidated", "retracted", "recomputed",
    }


def test_work_status_rejects_unknown_value():
    with pytest.raises(ValueError):
        WorkStatus("bogus")


@pytest.mark.parametrize("kind", [FTW, WTW, WTC])
def test_dependency_represents_every_required_edge_shape(kind):
    dep = Dependency(kind=kind, from_id="a1", to_id="b1")
    assert dep.kind is kind
    assert dep.from_id == "a1"
    assert dep.to_id == "b1"
    assert dep.dependency_id


def test_dependency_is_immutable():
    dep = Dependency(kind=FTW, from_id="f1", to_id="w1")
    with pytest.raises(Exception):
        dep.from_id = "other"  # type: ignore[misc]


def test_dependency_kind_rejects_unknown_value():
    with pytest.raises(ValueError):
        DependencyKind("bogus")


def test_work_item_serialization_is_deterministic_for_identical_input():
    kwargs = dict(
        kind="hotel_search", work_id="w1", turn_id="t1", goal_id="g1",
        output={"ok": True}, depends_on_facts=["f1"], depends_on_work=[],
        created_at=5.0, provenance={},
    )
    first = WorkItem(**kwargs).to_dict()
    second = WorkItem(**kwargs).to_dict()
    assert first == second


# ===========================================================================
# DependencyGraph - Phase 4
# ===========================================================================


def _work(work_id: str, kind: str = "generic") -> WorkItem:
    return WorkItem(kind=kind, work_id=work_id)


def _claim(claim_id: str, text: str = "a claim") -> Claim:
    return Claim(text=text, claim_id=claim_id)


# -- 1/2. registration -------------------------------------------------------

def test_register_work_returns_the_item_and_is_queryable():
    graph = DependencyGraph()
    work = graph.register_work(_work("W1", "flight_search"))
    assert work.work_id == "W1"
    assert graph.get_direct_dependents("W1") == []


def test_register_claim_returns_the_item():
    graph = DependencyGraph()
    claim = graph.register_claim(_claim("C1"))
    assert claim.claim_id == "C1"


# -- 3/4/5. edge types --------------------------------------------------------

def test_fact_to_work_dependency():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    dep = graph.register_dependency(FTW, "F1", "W1")
    assert dep.kind is FTW
    assert graph.has_dependency("F1", "W1")


def test_work_to_work_dependency():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    graph.register_work(_work("W4"))
    graph.register_dependency(WTW, "W1", "W4")
    assert graph.has_dependency("W1", "W4")


def test_work_to_claim_dependency():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    graph.register_claim(_claim("C1"))
    graph.register_dependency(WTC, "W1", "C1")
    assert graph.has_dependency("W1", "C1")


# -- 6/7. direct vs transitive ------------------------------------------------

def test_direct_dependents_vs_transitive_are_not_conflated():
    graph = DependencyGraph()
    for wid in ("W3", "W4"):
        graph.register_work(_work(wid))
    graph.register_claim(_claim("C3"))
    graph.register_claim(_claim("C4"))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(WTW, "W3", "W4")
    graph.register_dependency(WTC, "W3", "C3")
    graph.register_dependency(WTC, "W4", "C4")

    assert graph.get_direct_dependents("F2") == ["W3"]
    assert graph.get_transitive_dependents("F2") == ["W3", "W4", "C3", "C4"]


# -- 8. reverse lookup ---------------------------------------------------------

def test_get_dependencies_is_the_reverse_lookup():
    graph = DependencyGraph()
    for wid in ("W1", "W2", "W3", "W4"):
        graph.register_work(_work(wid))
    graph.register_dependency(WTW, "W1", "W4")
    graph.register_dependency(WTW, "W2", "W4")
    graph.register_dependency(WTW, "W3", "W4")
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F3", "W3")

    assert graph.get_dependencies("W4") == ["W1", "W2", "W3"]
    assert graph.get_dependencies("W3") == ["F2", "F3"]


# -- 9. branching graph --------------------------------------------------------

def test_branching_graph_two_facts_feed_one_work_item():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F3", "W3")
    assert graph.get_transitive_dependents("F2") == ["W3"]
    assert graph.get_transitive_dependents("F3") == ["W3"]
    assert graph.get_dependencies("W3") == ["F2", "F3"]


# -- 10. the canonical Goa graph, built once and reused -----------------------

@pytest.fixture
def canonical_graph() -> DependencyGraph:
    """Facts: F1=destination, F2=party_size, F3=budget.
    Work:   W1=flight_search, W2=hotel_search, W3=price_calculation, W4=recommendation.
    Claims: C1..C4, one per work item.
    """
    graph = DependencyGraph()
    for wid, kind in (("W1", "flight_search"), ("W2", "hotel_search"),
                      ("W3", "price_calculation"), ("W4", "recommendation")):
        graph.register_work(_work(wid, kind))
    for cid in ("C1", "C2", "C3", "C4"):
        graph.register_claim(_claim(cid))

    graph.register_dependency(FTW, "F1", "W1")
    graph.register_dependency(FTW, "F1", "W2")
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F3", "W3")

    graph.register_dependency(WTW, "W1", "W4")
    graph.register_dependency(WTW, "W2", "W4")
    graph.register_dependency(WTW, "W3", "W4")

    graph.register_dependency(WTC, "W1", "C1")
    graph.register_dependency(WTC, "W2", "C2")
    graph.register_dependency(WTC, "W3", "C3")
    graph.register_dependency(WTC, "W4", "C4")
    return graph


def test_canonical_graph_dependents_of_f1(canonical_graph):
    assert canonical_graph.get_transitive_dependents("F1") == ["W1", "W2", "W4", "C1", "C2", "C4"]


def test_canonical_graph_dependents_of_f2(canonical_graph):
    assert canonical_graph.get_transitive_dependents("F2") == ["W3", "W4", "C3", "C4"]


def test_canonical_graph_dependents_of_f3(canonical_graph):
    assert canonical_graph.get_transitive_dependents("F3") == ["W3", "W4", "C3", "C4"]


def test_canonical_graph_dependents_of_w3(canonical_graph):
    assert canonical_graph.get_transitive_dependents("W3") == ["W4", "C3", "C4"]


def test_canonical_graph_dependents_of_w1(canonical_graph):
    assert canonical_graph.get_transitive_dependents("W1") == ["W4", "C1", "C4"]


def test_canonical_graph_get_dependents_alias_matches_transitive(canonical_graph):
    assert canonical_graph.get_dependents("F1") == canonical_graph.get_transitive_dependents("F1")


# -- 11. unaffected branch (this is what a later invalidation phase will lean on) --

def test_canonical_graph_f2_change_does_not_reach_the_flight_or_hotel_branch(canonical_graph):
    affected = set(canonical_graph.get_transitive_dependents("F2"))
    assert affected == {"W3", "W4", "C3", "C4"}
    unaffected = {"W1", "W2", "C1", "C2"}
    assert affected.isdisjoint(unaffected)


# -- 12. duplicate edge ---------------------------------------------------------

def test_duplicate_dependency_registration_is_idempotent():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    first = graph.register_dependency(FTW, "F2", "W3")
    second = graph.register_dependency(FTW, "F2", "W3")
    assert first is second
    assert graph.get_direct_dependents("F2") == ["W3"]  # not duplicated


# -- 13. duplicate work / claim ---------------------------------------------------

def test_duplicate_work_registration_keeps_the_first():
    graph = DependencyGraph()
    first = graph.register_work(_work("W1", "flight_search"))
    second = graph.register_work(_work("W1", "hotel_search"))
    assert first is second
    assert second.kind == "flight_search"


def test_duplicate_claim_registration_keeps_the_first():
    graph = DependencyGraph()
    first = graph.register_claim(_claim("C1", "first text"))
    second = graph.register_claim(_claim("C1", "second text"))
    assert first is second
    assert second.text == "first text"


# -- 15. missing node -------------------------------------------------------------

def test_work_to_work_with_unregistered_source_raises():
    graph = DependencyGraph()
    graph.register_work(_work("W2"))
    with pytest.raises(NodeNotRegisteredError) as excinfo:
        graph.register_dependency(WTW, "W1", "W2")
    assert excinfo.value.node_id == "W1"
    assert excinfo.value.expected_kind is NodeKind.WORK


def test_fact_to_work_with_unregistered_target_raises():
    graph = DependencyGraph()
    with pytest.raises(NodeNotRegisteredError):
        graph.register_dependency(FTW, "F1", "W-does-not-exist")


def test_work_to_claim_with_unregistered_claim_raises():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    with pytest.raises(NodeNotRegisteredError):
        graph.register_dependency(WTC, "W1", "C-does-not-exist")


def test_fact_node_does_not_require_explicit_registration():
    """Deliberate asymmetry, documented in graph.py: FACT ids are trusted
    as-is since FactNotebook already owns them; only WORK/CLAIM require a
    prior register_* call."""
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    # No graph.register_fact(...) call exists or is needed.
    graph.register_dependency(FTW, "any-fact-id-at-all", "W1")
    assert graph.has_dependency("any-fact-id-at-all", "W1")


def test_wrong_node_kind_raises_kind_mismatch_not_missing_node():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    graph.register_claim(_claim("C1"))
    # C1 is registered, but as a CLAIM, not a WORK - WORK_TO_WORK must reject it.
    with pytest.raises(NodeKindMismatchError) as excinfo:
        graph.register_dependency(WTW, "W1", "C1")
    assert excinfo.value.node_id == "C1"
    assert excinfo.value.expected_kind is NodeKind.WORK
    assert excinfo.value.actual_kind is NodeKind.CLAIM


# -- 16. unsupported dependency kind -----------------------------------------------

def test_unsupported_dependency_kind_is_rejected():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    graph.register_work(_work("W2"))
    with pytest.raises(UnsupportedDependencyKindError):
        graph.register_dependency("bogus", "W1", "W2")  # type: ignore[arg-type]


# -- 17/18. cycle detection ---------------------------------------------------------

def test_direct_two_node_cycle_is_rejected():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_work(_work("W4"))
    graph.register_dependency(WTW, "W3", "W4")
    with pytest.raises(DependencyCycleError) as excinfo:
        graph.register_dependency(WTW, "W4", "W3")
    assert excinfo.value.from_id == "W4"
    assert excinfo.value.to_id == "W3"
    # The graph must not have moved into a corrupted state: W3 -> W4 is
    # still the only edge.
    assert graph.get_direct_dependents("W3") == ["W4"]
    assert graph.get_direct_dependents("W4") == []


def test_longer_cycle_is_rejected():
    graph = DependencyGraph()
    for wid in ("W1", "W2", "W3"):
        graph.register_work(_work(wid))
    graph.register_dependency(WTW, "W1", "W2")
    graph.register_dependency(WTW, "W2", "W3")
    with pytest.raises(DependencyCycleError):
        graph.register_dependency(WTW, "W3", "W1")
    assert graph.get_transitive_dependents("W1") == ["W2", "W3"]


def test_self_loop_is_rejected():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    with pytest.raises(DependencyCycleError):
        graph.register_dependency(WTW, "W1", "W1")


# -- 19. deterministic ordering -----------------------------------------------------

def test_ordering_is_stable_across_repeated_calls(canonical_graph):
    first = canonical_graph.get_transitive_dependents("F1")
    for _ in range(10):
        assert canonical_graph.get_transitive_dependents("F1") == first


def test_ordering_is_identical_across_separately_built_identical_graphs():
    def build() -> DependencyGraph:
        graph = DependencyGraph()
        for wid in ("W1", "W2", "W3", "W4"):
            graph.register_work(_work(wid))
        graph.register_dependency(FTW, "F1", "W1")
        graph.register_dependency(FTW, "F1", "W2")
        graph.register_dependency(WTW, "W1", "W4")
        graph.register_dependency(WTW, "W2", "W4")
        return graph

    a, b = build(), build()
    assert a.get_transitive_dependents("F1") == b.get_transitive_dependents("F1")
    assert a.get_dependencies("W4") == b.get_dependencies("W4")


# -- 20. repeated read-only queries never mutate state -------------------------------

def test_repeated_queries_never_change_graph_state(canonical_graph):
    before_direct = canonical_graph.get_direct_dependents("F1")
    before_deps = canonical_graph.get_dependencies("W4")
    for _ in range(100):
        canonical_graph.get_transitive_dependents("F2")
        canonical_graph.get_direct_dependents("F1")
        canonical_graph.get_dependencies("W4")
        canonical_graph.has_dependency("F1", "W1")
    assert canonical_graph.get_direct_dependents("F1") == before_direct
    assert canonical_graph.get_dependencies("W4") == before_deps


def test_query_results_are_copies_not_live_references():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    graph.register_work(_work("W4"))
    graph.register_dependency(WTW, "W1", "W4")
    result = graph.get_direct_dependents("W1")
    result.append("tampered")
    assert graph.get_direct_dependents("W1") == ["W4"]


# -- 21. reset ------------------------------------------------------------------------

def test_reset_clears_nodes_and_edges():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    graph.register_claim(_claim("C1"))
    graph.register_dependency(FTW, "F1", "W1")
    graph.register_dependency(WTC, "W1", "C1")

    graph.reset()

    assert graph.get_direct_dependents("F1") == []
    assert graph.get_dependencies("W1") == []
    assert graph.has_dependency("F1", "W1") is False
    # Registration state is gone too: W1 is no longer a known WORK node, so
    # a fresh edge referencing it is treated as missing, not pre-existing.
    with pytest.raises(NodeNotRegisteredError):
        graph.register_dependency(WTW, "W1", "W2")


def test_reset_on_one_graph_does_not_affect_another():
    a = DependencyGraph()
    b = DependencyGraph()
    a.register_work(_work("W1"))
    b.register_work(_work("W1"))
    a.register_dependency(FTW, "F1", "W1")
    b.register_dependency(FTW, "F1", "W1")

    a.reset()

    assert a.get_direct_dependents("F1") == []
    assert b.get_direct_dependents("F1") == ["W1"]


# -- 22. independent graph instances --------------------------------------------------

def test_two_independent_graphs_never_share_nodes_or_edges():
    a = DependencyGraph()
    b = DependencyGraph()
    a.register_work(_work("W1", "flight_search"))
    # b never registered W1 at all.
    with pytest.raises(NodeNotRegisteredError):
        b.register_dependency(FTW, "F1", "W1")
    # a is unaffected by b's failed attempt.
    a.register_dependency(FTW, "F1", "W1")
    assert a.has_dependency("F1", "W1")