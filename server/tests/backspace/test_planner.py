from __future__ import annotations

import pytest

from app.backspace import (
    BackspaceCore,
    ChangeKind,
    ChangeSet,
    Claim,
    ClaimStatus,
    Dependency,
    DependencyGraph,
    DependencyKind,
    Fact,
    FactStatus,
    Invalidation,
    PlanStatus,
    RecomputationPlan,
    WorkItem,
    WorkStatus,
    invalidate,
    plan_recompute,
)

FTW = DependencyKind.FACT_TO_WORK
WTW = DependencyKind.WORK_TO_WORK
WTC = DependencyKind.WORK_TO_CLAIM


def _invalidation() -> Invalidation:
    new_fact = Fact(key="party_size", value=5, fact_id="f2")
    changeset = ChangeSet(key="party_size", kind=ChangeKind.CHANGED, new_fact=new_fact)
    return Invalidation(changeset=changeset, invalidated_work_ids=["w1"], kept_work_ids=["w2"])


def test_recomputation_plan_defaults():
    plan = RecomputationPlan(invalidation=_invalidation())
    assert plan.status is PlanStatus.PENDING
    assert plan.work_items_to_recompute == []
    assert plan.preserved_work_ids == []
    assert plan.missing_dependencies == []
    assert plan.plan_id


def test_recomputation_plan_can_represent_a_full_plan():
    invalidation = _invalidation()
    plan = RecomputationPlan(
        invalidation=invalidation,
        work_items_to_recompute=["w1"],
        preserved_work_ids=["w2"],
        missing_dependencies=["f3"],
        rationale="only w1 depended on party_size",
        status=PlanStatus.READY,
    )
    assert plan.work_items_to_recompute == ["w1"]
    assert plan.preserved_work_ids == ["w2"]
    assert plan.missing_dependencies == ["f3"]
    assert plan.status is PlanStatus.READY


def test_plan_status_enum_values():
    # "blocked" (Phase 6) added for a plan that found work to recompute but
    # cannot trust an order for it (missing dependency, or a cycle).
    assert {s.value for s in PlanStatus} == {"pending", "ready", "empty", "executed", "blocked"}


def test_plan_status_rejects_unknown_value():
    with pytest.raises(ValueError):
        PlanStatus("bogus")


def test_recomputation_plan_serialization_nests_invalidation():
    plan = RecomputationPlan(
        invalidation=_invalidation(),
        work_items_to_recompute=["w1"],
        rationale="r",
        created_at=7.0,
    )
    data = plan.to_dict()
    assert data["work_items_to_recompute"] == ["w1"]
    assert data["invalidation"]["invalidated_work_ids"] == ["w1"]
    assert data["created_at"] == 7.0


def test_recomputation_plan_invalidated_work_ids_property_derives_from_invalidation():
    plan = RecomputationPlan(invalidation=_invalidation())
    assert plan.invalidated_work_ids == ["w1"]


def test_recomputation_plan_reasons_field_is_a_dict_defaulting_empty():
    plan = RecomputationPlan(invalidation=_invalidation())
    assert plan.reasons == {}
    plan2 = RecomputationPlan(invalidation=_invalidation(), reasons={"w1": "depends on changed fact 'x'"})
    assert plan2.to_dict()["reasons"] == {"w1": "depends on changed fact 'x'"}


# ===========================================================================
# Phase 6 - the recomputation planner (plan_recompute)
# ===========================================================================


def _work(work_id: str, kind: str = "generic", depends_on_work: list[str] | None = None) -> WorkItem:
    return WorkItem(kind=kind, work_id=work_id, depends_on_work=depends_on_work or [])


def _changed(key: str, old_value, new_value, *, fact_id: str) -> ChangeSet:
    previous = Fact(key=key, value=old_value, fact_id=fact_id, status=FactStatus.SUPERSEDED)
    new_fact = Fact(key=key, value=new_value, fact_id=f"{fact_id}-v2", supersedes=fact_id)
    return ChangeSet(key=key, kind=ChangeKind.CHANGED, new_fact=new_fact, previous_fact=previous)


def _invalidation_for(work_ids: list[str], changeset: ChangeSet | None = None) -> Invalidation:
    """A hand-built Invalidation naming exactly `work_ids` as invalidated -
    lets a test drive the planner without needing Phase 5's engine to agree
    first, so planner tests are isolated from invalidation-engine tests."""
    cs = changeset or _changed("k", "old", "new", fact_id="F1")
    return Invalidation(changeset=cs, changesets=[cs], invalidated_work_ids=list(work_ids))


@pytest.fixture
def canonical_graph() -> DependencyGraph:
    graph = DependencyGraph()
    for wid, kind in (("W1", "flight_search"), ("W2", "hotel_search"),
                      ("W3", "price_calculation"), ("W4", "recommendation")):
        graph.register_work(_work(wid, kind))
    graph.register_dependency(FTW, "F1", "W1")
    graph.register_dependency(FTW, "F1", "W2")
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F3", "W3")
    graph.register_dependency(WTW, "W1", "W4")
    graph.register_dependency(WTW, "W2", "W4")
    graph.register_dependency(WTW, "W3", "W4")
    return graph


# -- 1. simple stale work -----------------------------------------------------

def test_simple_stale_work_is_scheduled():
    graph = DependencyGraph()
    work = graph.register_work(_work("W3"))
    work.status = WorkStatus.STALE
    plan = plan_recompute(graph, _invalidation_for(["W3"]))
    assert plan.work_items_to_recompute == ["W3"]
    assert plan.status is PlanStatus.READY


# -- 2. preserved valid work ---------------------------------------------------

def test_preserved_valid_work_is_listed_but_not_scheduled():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    stale = graph.register_work(_work("W3"))
    stale.status = WorkStatus.STALE
    plan = plan_recompute(graph, _invalidation_for(["W3"]))
    assert plan.work_items_to_recompute == ["W3"]
    assert "W1" in plan.preserved_work_ids


# -- 3. dependency ordering -----------------------------------------------------

def test_dependency_ordering_w3_before_w4():
    graph = DependencyGraph()
    w3 = graph.register_work(_work("W3"))
    w4 = graph.register_work(_work("W4"))
    graph.register_dependency(WTW, "W3", "W4")
    w3.status = WorkStatus.STALE
    w4.status = WorkStatus.STALE

    plan = plan_recompute(graph, _invalidation_for(["W3", "W4"]))
    assert plan.work_items_to_recompute == ["W3", "W4"]


# -- 4. long chain --------------------------------------------------------------

def test_long_chain_all_four_stale():
    graph = DependencyGraph()
    for wid in ("W1", "W2", "W3", "W4"):
        graph.register_work(_work(wid)).status = WorkStatus.STALE
    graph.register_dependency(WTW, "W1", "W2")
    graph.register_dependency(WTW, "W2", "W3")
    graph.register_dependency(WTW, "W3", "W4")

    plan = plan_recompute(graph, _invalidation_for(["W1", "W2", "W3", "W4"]))
    assert plan.work_items_to_recompute == ["W1", "W2", "W3", "W4"]


def test_long_chain_only_tail_stale_head_preserved():
    graph = DependencyGraph()
    for wid in ("W1", "W2", "W3", "W4"):
        graph.register_work(_work(wid))
    graph.register_dependency(WTW, "W1", "W2")
    graph.register_dependency(WTW, "W2", "W3")
    graph.register_dependency(WTW, "W3", "W4")
    for wid in ("W2", "W3", "W4"):
        graph.get_work(wid).status = WorkStatus.STALE
    # W1 stays PENDING/preserved.

    plan = plan_recompute(graph, _invalidation_for(["W2", "W3", "W4"]))
    assert plan.work_items_to_recompute == ["W2", "W3", "W4"]
    assert "W1" not in plan.work_items_to_recompute
    assert "W1" in plan.preserved_work_ids


# -- 5. branching graph -----------------------------------------------------------

def test_branching_graph_produces_a_valid_deterministic_order():
    graph = DependencyGraph()
    for wid in ("W1", "W2", "W3", "W4", "W5"):
        graph.register_work(_work(wid)).status = WorkStatus.STALE
    graph.register_dependency(FTW, "F1", "W1")
    graph.register_dependency(FTW, "F1", "W2")
    graph.register_dependency(FTW, "F1", "W3")
    graph.register_dependency(WTW, "W1", "W4")
    graph.register_dependency(WTW, "W2", "W4")
    graph.register_dependency(WTW, "W3", "W5")

    plan = plan_recompute(graph, _invalidation_for(["W1", "W2", "W3", "W4", "W5"]))
    order = plan.work_items_to_recompute
    assert order.index("W1") < order.index("W4")
    assert order.index("W2") < order.index("W4")
    assert order.index("W3") < order.index("W5")
    assert set(order) == {"W1", "W2", "W3", "W4", "W5"}


# -- 6. canonical Goa scenario (integration, via BackspaceCore) -------------------

def test_canonical_goa_scenario_end_to_end():
    core = BackspaceCore()
    for wid, kind in (("W1", "flight_search"), ("W2", "hotel_search"),
                      ("W3", "price_calculation"), ("W4", "recommendation")):
        core.register_work(WorkItem(kind=kind, work_id=wid))

    f1 = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    f2 = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    f3 = core.assert_fact("budget", 20000, source="user", turn_id="t1")
    core.register_dependency(FTW, f1.fact.fact_id, "W1")
    core.register_dependency(FTW, f1.fact.fact_id, "W2")
    core.register_dependency(FTW, f2.fact.fact_id, "W3")
    core.register_dependency(FTW, f3.fact.fact_id, "W3")
    core.register_dependency(WTW, "W1", "W4")
    core.register_dependency(WTW, "W2", "W4")
    core.register_dependency(WTW, "W3", "W4")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)
    plan = core.plan_recompute(invalidation)

    assert plan.status is PlanStatus.READY
    assert plan.work_items_to_recompute == ["W3", "W4"]
    assert sorted(plan.preserved_work_ids) == ["W1", "W2"]


# -- 7/8. multiple changed facts / duplicate affected work -------------------------

def _invalidation_from(changesets: list[ChangeSet], invalidated_work_ids: list[str]) -> Invalidation:
    """A hand-built Invalidation simulating what a (possibly naive) caller
    could hand the planner - including an accidentally-duplicated id list -
    to prove the planner itself also deduplicates rather than trusting its
    input blindly."""
    return Invalidation(changeset=changesets[0], changesets=changesets, invalidated_work_ids=invalidated_work_ids)


def test_multiple_changed_facts_shared_work_appears_once_and_in_order():
    graph = DependencyGraph()
    w3 = graph.register_work(_work("W3"))
    w3.status = WorkStatus.STALE
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F3", "W3")

    f2_change = _changed("party_size", 2, 5, fact_id="F2")
    f3_change = _changed("budget", 20000, 30000, fact_id="F3")
    # Simulates an upstream Invalidation that (incorrectly) duplicated W3.
    invalidation = _invalidation_from([f2_change, f3_change], ["W3", "W3"])

    plan = plan_recompute(graph, invalidation)
    assert plan.work_items_to_recompute == ["W3"]
    assert plan.work_items_to_recompute.count("W3") == 1


# -- 9/10. valid vs stale upstream dependency (the critical rule) ------------------

def test_valid_upstream_dependency_is_not_scheduled():
    """Section 16's example: W3 -> W4, W4 stale, W3 VALID. Plan: [W4] only."""
    graph = DependencyGraph()
    w3 = graph.register_work(_work("W3"))
    w4 = graph.register_work(_work("W4"))
    graph.register_dependency(WTW, "W3", "W4")
    w3.status = WorkStatus.VALID
    w4.status = WorkStatus.STALE

    plan = plan_recompute(graph, _invalidation_for(["W3", "W4"]))
    assert plan.work_items_to_recompute == ["W4"]
    assert "W3" in plan.preserved_work_ids


def test_stale_upstream_dependency_is_scheduled_before_its_dependent():
    graph = DependencyGraph()
    w3 = graph.register_work(_work("W3"))
    w4 = graph.register_work(_work("W4"))
    graph.register_dependency(WTW, "W3", "W4")
    w3.status = WorkStatus.STALE
    w4.status = WorkStatus.STALE

    plan = plan_recompute(graph, _invalidation_for(["W3", "W4"]))
    assert plan.work_items_to_recompute == ["W3", "W4"]


# -- 11. missing dependency ---------------------------------------------------------

def test_missing_declared_dependency_blocks_the_plan():
    graph = DependencyGraph()
    work = graph.register_work(_work("W4", depends_on_work=["W3"]))  # W3 never registered
    work.status = WorkStatus.STALE

    plan = plan_recompute(graph, _invalidation_for(["W4"]))
    assert plan.status is PlanStatus.BLOCKED
    assert "W3" in plan.missing_dependencies


def test_missing_work_item_referenced_by_invalidation_itself_is_reported():
    graph = DependencyGraph()
    plan = plan_recompute(graph, _invalidation_for(["W-ghost"]))
    assert plan.status is PlanStatus.BLOCKED
    assert "W-ghost" in plan.missing_dependencies


# -- 12. cycle safety -----------------------------------------------------------------

def test_cycle_in_a_corrupted_graph_blocks_rather_than_hangs_or_fabricates():
    """The public DependencyGraph API cannot create a cycle (Phase 4). This
    test pokes the private adjacency structure directly to simulate a
    corrupted graph - a white-box test of an otherwise-unreachable defensive
    path, not a weakening of the graph's public cycle-prevention invariant."""
    graph = DependencyGraph()
    w1 = graph.register_work(_work("W1"))
    w2 = graph.register_work(_work("W2"))
    graph.register_dependency(WTW, "W1", "W2")
    w1.status = WorkStatus.STALE
    w2.status = WorkStatus.STALE

    corrupt_edge = Dependency(kind=WTW, from_id="W2", to_id="W1")
    graph._forward_edges.setdefault("W2", []).append(corrupt_edge)  # noqa: SLF001

    plan = plan_recompute(graph, _invalidation_for(["W1", "W2"]))
    assert plan.status is PlanStatus.BLOCKED
    assert set(plan.work_items_to_recompute) == {"W1", "W2"}  # visible, but order not trusted


# -- 13/14/15/19. determinism, repeated planning, idempotency ----------------------

def test_deterministic_ordering_across_repeated_calls(canonical_graph):
    canonical_graph.get_work("W3").status = WorkStatus.STALE
    canonical_graph.get_work("W4").status = WorkStatus.STALE
    invalidation = _invalidation_for(["W3", "W4"])

    first = plan_recompute(canonical_graph, invalidation)
    for _ in range(10):
        again = plan_recompute(canonical_graph, invalidation)
        assert again.work_items_to_recompute == first.work_items_to_recompute
        assert sorted(again.preserved_work_ids) == sorted(first.preserved_work_ids)
        assert again.status == first.status


def test_plan_idempotency_no_duplicate_work_across_repeated_calls():
    graph = DependencyGraph()
    w3 = graph.register_work(_work("W3"))
    w3.status = WorkStatus.STALE
    invalidation = _invalidation_for(["W3", "W3", "W3"])  # already-duplicated input

    for _ in range(10):
        plan = plan_recompute(graph, invalidation)
        assert plan.work_items_to_recompute == ["W3"]


# -- 16/17. already-stale / already-valid work --------------------------------------

def test_already_stale_work_is_eligible():
    graph = DependencyGraph()
    w3 = graph.register_work(_work("W3"))
    w3.status = WorkStatus.STALE
    plan = plan_recompute(graph, _invalidation_for(["W3"]))
    assert plan.work_items_to_recompute == ["W3"]


def test_valid_work_is_never_recomputed_even_if_invalidation_names_it():
    """Defensive: the planner trusts live WorkStatus over a possibly-stale
    Invalidation snapshot."""
    graph = DependencyGraph()
    w1 = graph.register_work(_work("W1"))
    w1.status = WorkStatus.VALID
    plan = plan_recompute(graph, _invalidation_for(["W1"]))
    assert plan.work_items_to_recompute == []
    assert plan.status is PlanStatus.EMPTY
    assert "W1" in plan.preserved_work_ids


# -- 18. preserved work in output ----------------------------------------------------

def test_preserved_work_is_explicit_not_left_for_the_caller_to_compute(canonical_graph):
    canonical_graph.get_work("W3").status = WorkStatus.STALE
    canonical_graph.get_work("W4").status = WorkStatus.STALE
    plan = plan_recompute(canonical_graph, _invalidation_for(["W3", "W4"]))
    assert sorted(plan.preserved_work_ids) == ["W1", "W2"]


# -- 19. rationale ---------------------------------------------------------------------

def test_rationale_reasons_are_structured_not_generated(canonical_graph):
    canonical_graph.get_work("W3").status = WorkStatus.STALE
    canonical_graph.get_work("W4").status = WorkStatus.STALE
    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = Invalidation(changeset=changeset, changesets=[changeset], invalidated_work_ids=["W3", "W4"])

    plan = plan_recompute(canonical_graph, invalidation)
    assert plan.reasons["W3"] == "depends on changed fact 'party_size'"
    assert plan.reasons["W4"] == "depends on stale work 'W3'"
    assert plan.rationale  # a non-empty overall summary exists too


# -- 20. session isolation --------------------------------------------------------------

def test_session_isolation_between_two_backspace_core_instances_for_planning():
    core_a = BackspaceCore()
    core_b = BackspaceCore()
    core_a.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    core_b.register_work(WorkItem(kind="price_calculation", work_id="W8"))

    fa = core_a.assert_fact("party_size", 2, source="user", turn_id="t1")
    fb = core_b.assert_fact("party_size", 2, source="user", turn_id="t1")
    core_a.register_dependency(FTW, fa.fact.fact_id, "W3")
    core_b.register_dependency(FTW, fb.fact.fact_id, "W8")

    change_a = core_a.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation_a = core_a.invalidate(change_a.changeset)
    plan_a = core_a.plan_recompute(invalidation_a)

    assert plan_a.work_items_to_recompute == ["W3"]
    assert "W8" not in plan_a.work_items_to_recompute
    assert core_b.graph.get_work("W3") is None  # B never even has this id


# -- 21. reset --------------------------------------------------------------------------

def test_reset_clears_graph_state_planning_depends_on():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W3")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)
    core.plan_recompute(invalidation)
    assert core.graph.get_work("W3").status is WorkStatus.STALE

    core.reset()

    assert core.graph.get_work("W3") is None
    assert core.get_fact("party_size") is None


# -- 22. facade delegation ---------------------------------------------------------------

def test_facade_plan_recompute_delegates_to_the_planner_against_its_own_graph():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W3")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)

    plan = core.plan_recompute(invalidation)

    assert isinstance(plan, RecomputationPlan)
    assert plan.work_items_to_recompute == ["W3"]


def test_assert_fact_and_invalidate_are_still_not_automatically_wired_to_plan_recompute():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W3")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    assert change.status is ChangeKind.CHANGED
    assert change.invalidation is None
    assert change.plan is None  # still true in Phase 6 - the pipeline is not wired yet


# -- 23/24/25. no mutation of the graph structure, facts, or claims ----------------------

def test_plan_recompute_never_mutates_graph_structure(canonical_graph):
    canonical_graph.get_work("W3").status = WorkStatus.STALE
    canonical_graph.get_work("W4").status = WorkStatus.STALE
    before_work_ids = canonical_graph.all_work_ids()
    before_edges = canonical_graph.get_direct_dependents("W1")

    plan_recompute(canonical_graph, _invalidation_for(["W3", "W4"]))

    assert canonical_graph.all_work_ids() == before_work_ids
    assert canonical_graph.get_direct_dependents("W1") == before_edges


def test_plan_recompute_never_mutates_work_status():
    graph = DependencyGraph()
    w3 = graph.register_work(_work("W3"))
    w3.status = WorkStatus.STALE
    plan_recompute(graph, _invalidation_for(["W3"]))
    # Planning does not itself flip status to RECOMPUTED or anything else -
    # that belongs to a future execution phase.
    assert graph.get_work("W3").status is WorkStatus.STALE


def test_plan_recompute_never_touches_facts():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W3")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)

    before = core.get_fact("party_size")
    before_history_len = len(core.get_fact_history("party_size"))

    core.plan_recompute(invalidation)

    after = core.get_fact("party_size")
    assert after is before  # same object, not replaced
    assert len(core.get_fact_history("party_size")) == before_history_len


def test_plan_recompute_never_touches_claims():
    graph = DependencyGraph()
    w3 = graph.register_work(_work("W3"))
    w3.status = WorkStatus.STALE
    claim = graph.register_claim(Claim(text="a price claim", claim_id="C3"))
    graph.register_dependency(WTC, "W3", "C3")
    assert claim.status is ClaimStatus.GENERATED

    plan_recompute(graph, _invalidation_for(["W3"]))

    assert claim.status is ClaimStatus.GENERATED  # untouched - planning is not invalidation
