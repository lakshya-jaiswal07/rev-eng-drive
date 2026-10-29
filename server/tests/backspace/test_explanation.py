"""Phase 8 - provenance / explainable ChangeSet.

Model-level tests drive ``build_explanation()`` directly against a hand-built
graph + Invalidation + RecomputationPlan (the same style ``test_graph.py``/
``test_invalidation_engine.py``/``test_planner.py`` already use). The
BackspaceCore-level tests drive the real ``assert_fact`` -> ``invalidate`` ->
``build_explanation``/``explain_change`` pipeline, including the canonical
scenario from the Phase 8 brief and the read-only guarantee.
"""

from __future__ import annotations

import pytest

from app.backspace import (
    BackspaceCore,
    ChangeExplanation,
    ChangeKind,
    ChangeSet,
    ChangeSetNotFoundError,
    Claim,
    ClaimExplanation,
    DependencyGraph,
    DependencyKind,
    ExplanationSummary,
    Fact,
    FactStatus,
    Invalidation,
    PlanStatus,
    RecomputationPlan,
    RecomputeStep,
    WorkExplanation,
    WorkItem,
    WorkStatus,
    build_explanation,
    invalidate,
    plan_recompute,
    render_explanation_text,
)

FTW = DependencyKind.FACT_TO_WORK
WTW = DependencyKind.WORK_TO_WORK
WTC = DependencyKind.WORK_TO_CLAIM


def _work(work_id: str, kind: str = "generic") -> WorkItem:
    return WorkItem(kind=kind, work_id=work_id)


def _claim(claim_id: str, text: str = "a claim") -> Claim:
    return Claim(text=text, claim_id=claim_id)


def _changed(key: str, old_value, new_value, *, fact_id: str) -> ChangeSet:
    previous = Fact(key=key, value=old_value, fact_id=fact_id, status=FactStatus.SUPERSEDED)
    new_fact = Fact(key=key, value=new_value, fact_id=f"{fact_id}-v2", supersedes=fact_id)
    return ChangeSet(key=key, kind=ChangeKind.CHANGED, new_fact=new_fact, previous_fact=previous)


# ===========================================================================
# Model-level: dataclasses serialize cleanly
# ===========================================================================


def test_change_explanation_to_dict_is_json_safe():
    change = ChangeExplanation(
        changeset_id="cs1", key="party_size", change_kind="changed", fact_id="f2",
        new_value=5, new_version=2, previous_fact_id="f1", previous_value=2, previous_version=1,
        source="user", turn_id="t2", goal_id="g1", confidence=0.9,
    )
    data = change.to_dict()
    assert data["key"] == "party_size"
    assert data["previous_value"] == 2
    assert data["new_value"] == 5
    assert all(isinstance(k, str) for k in data)


def test_work_explanation_defaults():
    work = WorkExplanation(work_id="W1", kind="hotel_search", turn_id="t1", goal_id=None, status="pending")
    assert work.reason == ""
    assert work.caused_by_fact_keys == []
    assert work.to_dict()["caused_by_fact_keys"] == []


def test_claim_explanation_round_trip():
    claim = ClaimExplanation(
        claim_id="C1", text="x", work_id="W1", turn_id="t1", status="invalidated",
        spoken=True, evidence_doc_id="doc#1", invalidation_reason="party_size changed",
        retraction_required=True, retracted=False,
    )
    data = claim.to_dict()
    assert data["spoken"] is True
    assert data["retraction_required"] is True
    assert data["retracted"] is False


def test_recompute_step_and_summary_to_dict():
    step = RecomputeStep(work_id="W2", kind="price_calculation", order=1)
    assert step.to_dict() == {"work_id": "W2", "kind": "price_calculation", "order": 1}
    summary = ExplanationSummary(
        changed_fact_count=1, new_fact_count=0, kept_work_count=1, invalidated_work_count=2,
        invalidated_claim_count=2, retraction_required_count=2, retracted_count=0,
        recompute_count=2, plan_status="ready",
    )
    assert summary.to_dict()["plan_status"] == "ready"


# ===========================================================================
# build_explanation() - pure, hand-built graph/invalidation/plan
# ===========================================================================


# -- 1/2/3/4. changed fact appears with correct values/ids/provenance -------------

def test_1_2_3_4_changed_fact_appears_with_correct_values_ids_and_provenance():
    graph = DependencyGraph()
    graph.register_work(_work("W3", "price_calculation"))
    graph.register_dependency(FTW, "F2", "W3")

    previous = Fact(key="party_size", value=2, fact_id="F2", source="user", turn_id="t1", goal_id="g1", confidence=0.8)
    new_fact = Fact(key="party_size", value=5, fact_id="F2-v2", supersedes="F2", source="voice", turn_id="t2", goal_id="g1", confidence=0.6)
    changeset = ChangeSet(key="party_size", kind=ChangeKind.CHANGED, new_fact=new_fact, previous_fact=previous)

    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    explanation = build_explanation(graph, [changeset], invalidation, plan, {})

    assert len(explanation.changes) == 1
    change = explanation.changes[0]
    assert change.key == "party_size"
    assert change.change_kind == "changed"
    assert change.previous_value == 2
    assert change.new_value == 5
    assert change.previous_fact_id == "F2"
    assert change.fact_id == "F2-v2"
    assert change.previous_version == 1
    assert change.new_version == 1  # new_fact constructed directly above, version defaults to 1
    assert change.source == "voice"
    assert change.turn_id == "t2"
    assert change.goal_id == "g1"
    assert change.confidence == 0.6


def test_new_fact_change_has_no_previous_and_still_reports_identity():
    fact = Fact(key="destination", value="Goa", fact_id="F1", version=1, source="user", turn_id="t1")
    changeset = ChangeSet(key="destination", kind=ChangeKind.NEW, new_fact=fact)
    explanation = build_explanation(DependencyGraph(), [changeset], None, None, {})
    change = explanation.changes[0]
    assert change.change_kind == "new"
    assert change.fact_id == "F1"
    assert change.new_value == "Goa"
    assert change.new_version == 1
    assert change.previous_fact_id is None
    assert change.previous_value is None
    assert change.previous_version is None


# -- 5/6/7. kept work, invalidated work, dependency reason -------------------------

def test_5_6_7_kept_and_invalidated_work_with_dependency_reason():
    graph = DependencyGraph()
    graph.register_work(_work("W1", "hotel_search"))
    graph.register_work(_work("W2", "price_calculation"))
    graph.register_dependency(FTW, "F1", "W1")
    graph.register_dependency(FTW, "F2", "W2")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    explanation = build_explanation(graph, [changeset], invalidation, plan, {})

    assert [w.work_id for w in explanation.kept_work] == ["W1"]
    assert explanation.kept_work[0].reason == ""

    assert [w.work_id for w in explanation.invalidated_work] == ["W2"]
    assert explanation.invalidated_work[0].reason == "depends on changed fact 'party_size'"
    assert explanation.invalidated_work[0].caused_by_fact_keys == ["party_size"]


def test_invalidated_work_reason_never_says_something_vague():
    graph = DependencyGraph()
    graph.register_work(_work("W2"))
    graph.register_work(_work("W3"))
    graph.register_dependency(FTW, "F2", "W2")
    graph.register_dependency(WTW, "W2", "W3")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    explanation = build_explanation(graph, [changeset], invalidation, plan, {})

    reasons = {w.work_id: w.reason for w in explanation.invalidated_work}
    assert reasons["W2"] == "depends on changed fact 'party_size'"
    assert reasons["W3"] == "depends on stale work 'W2'"
    for reason in reasons.values():
        assert "something" not in reason.lower()


# -- no false causality -------------------------------------------------------------

def test_no_false_causality_work_with_no_dependency_edge_is_kept():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))  # never wired to any fact
    graph.register_work(_work("W2"))
    graph.register_dependency(FTW, "F2", "W2")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    explanation = build_explanation(graph, [changeset], invalidation, plan, {})

    assert "W1" in [w.work_id for w in explanation.kept_work]
    assert "W1" not in [w.work_id for w in explanation.invalidated_work]


# -- 8/9. multiple changed facts, shared work appears once -------------------------

def test_8_9_multiple_changed_facts_one_explanation_shared_work_once():
    graph = DependencyGraph()
    graph.register_work(_work("W3", "price_calculation"))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F3", "W3")

    from app.backspace import invalidate_many
    cs_party = _changed("party_size", 2, 5, fact_id="F2")
    cs_budget = _changed("budget", 20000, 30000, fact_id="F3")
    invalidation = invalidate_many(graph, [cs_party, cs_budget])
    plan = plan_recompute(graph, invalidation)
    explanation = build_explanation(graph, [cs_party, cs_budget], invalidation, plan, {})

    assert len(explanation.changes) == 2
    assert {c.key for c in explanation.changes} == {"party_size", "budget"}
    assert [w.work_id for w in explanation.invalidated_work] == ["W3"]  # not duplicated
    assert set(explanation.invalidated_work[0].caused_by_fact_keys) == {"party_size", "budget"}


# -- 10/11/12/13/14. invalidated claims, spoken/unspoken, retraction states --------

def test_10_11_unspoken_and_spoken_claims_are_distinguished():
    graph = DependencyGraph()
    graph.register_work(_work("W2"))
    unspoken = graph.register_claim(_claim("C-unspoken"))
    spoken = _claim("C-spoken")
    spoken.spoken_at = 1.0
    graph.register_claim(spoken)
    graph.register_dependency(FTW, "F2", "W2")
    graph.register_dependency(WTC, "W2", "C-unspoken")
    graph.register_dependency(WTC, "W2", "C-spoken")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    explanation = build_explanation(graph, [changeset], invalidation, None, {})

    by_id = {c.claim_id: c for c in explanation.invalidated_claims}
    assert by_id["C-unspoken"].spoken is False
    assert by_id["C-spoken"].spoken is True


def test_12_spoken_claim_with_no_retraction_reports_retraction_required():
    graph = DependencyGraph()
    graph.register_work(_work("W2"))
    claim = _claim("C1")
    claim.spoken_at = 1.0
    graph.register_claim(claim)
    graph.register_dependency(FTW, "F2", "W2")
    graph.register_dependency(WTC, "W2", "C1")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    explanation = build_explanation(graph, [changeset], invalidation, None, {})

    claim_explanation = explanation.invalidated_claims[0]
    assert claim_explanation.spoken is True
    assert claim_explanation.retraction_required is True
    assert claim_explanation.retracted is False
    assert claim_explanation.retraction is None


def test_13_spoken_claim_with_a_retraction_reports_retracted_state():
    from app.backspace import Retraction

    graph = DependencyGraph()
    graph.register_work(_work("W2"))
    claim = _claim("C1")
    claim.spoken_at = 1.0
    graph.register_claim(claim)
    graph.register_dependency(FTW, "F2", "W2")
    graph.register_dependency(WTC, "W2", "C1")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    retraction = Retraction(claim_id="C1", reason="party_size changed")
    explanation = build_explanation(graph, [changeset], invalidation, None, {"C1": retraction})

    claim_explanation = explanation.invalidated_claims[0]
    assert claim_explanation.retraction_required is False  # already handled
    assert claim_explanation.retracted is True
    assert claim_explanation.retraction is not None
    assert claim_explanation.retraction["reason"] == "party_size changed"


def test_14_unspoken_invalidated_claim_does_not_require_retraction():
    graph = DependencyGraph()
    graph.register_work(_work("W2"))
    graph.register_claim(_claim("C1"))  # never spoken
    graph.register_dependency(FTW, "F2", "W2")
    graph.register_dependency(WTC, "W2", "C1")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    explanation = build_explanation(graph, [changeset], invalidation, None, {})

    claim_explanation = explanation.invalidated_claims[0]
    assert claim_explanation.spoken is False
    assert claim_explanation.retraction_required is False
    assert claim_explanation.retracted is False


def test_spoken_is_read_from_spoken_at_never_from_status():
    """A claim manually left in a SPOKEN-ish status but with spoken_at unset
    (shouldn't normally happen, but the rule must hold regardless) reports
    spoken=False; conversely an odd status with spoken_at set reports True."""
    from app.backspace import ClaimStatus

    graph = DependencyGraph()
    graph.register_work(_work("W2"))
    odd = Claim(text="x", claim_id="C1", status=ClaimStatus.GENERATED, spoken_at=1.0)
    graph.register_claim(odd)
    graph.register_dependency(FTW, "F2", "W2")
    graph.register_dependency(WTC, "W2", "C1")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    explanation = build_explanation(graph, [changeset], invalidation, None, {})
    assert explanation.invalidated_claims[0].spoken is True  # from spoken_at, not status


# -- 15/16. recomputation order from the Phase 6 planner, preserved work excluded --

def test_15_16_recompute_order_comes_from_the_planner_preserved_work_excluded():
    graph = DependencyGraph()
    graph.register_work(_work("W1", "hotel_search"))
    graph.register_work(_work("W2", "price_calculation"))
    graph.register_work(_work("W3", "recommendation"))
    graph.register_dependency(FTW, "F1", "W1")
    graph.register_dependency(FTW, "F2", "W2")
    graph.register_dependency(WTW, "W1", "W3")
    graph.register_dependency(WTW, "W2", "W3")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    explanation = build_explanation(graph, [changeset], invalidation, plan, {})

    assert [(r.work_id, r.order) for r in explanation.recompute] == [("W2", 1), ("W3", 2)]
    assert "W1" not in [r.work_id for r in explanation.recompute]  # preserved, never scheduled


# -- 17/18. BLOCKED / EMPTY plan representation --------------------------------------

def test_17_blocked_plan_is_represented_with_missing_dependencies_and_no_order():
    graph = DependencyGraph()
    work = graph.register_work(_work("W4", "recommendation"))
    work.depends_on_work = ["W-missing"]
    work.status = WorkStatus.STALE
    graph.register_dependency(FTW, "F2", "W4")

    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    assert plan.status is PlanStatus.BLOCKED

    explanation = build_explanation(graph, [changeset], invalidation, plan, {})
    assert explanation.summary.plan_status == "blocked"
    assert "W-missing" in explanation.missing_dependencies
    assert explanation.recompute == []  # never a fabricated order for a BLOCKED plan


def test_18_empty_plan_is_represented_as_no_recomputation_required():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    changeset = _changed("party_size", 2, 5, fact_id="F-unrelated")  # touches nothing
    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    assert plan.status is PlanStatus.EMPTY

    explanation = build_explanation(graph, [changeset], invalidation, plan, {})
    assert explanation.recompute == []
    assert explanation.summary.recompute_count == 0
    assert explanation.summary.plan_status == "empty"


def test_explanation_without_an_invalidation_yet_reports_not_computed():
    graph = DependencyGraph()
    fact = Fact(key="party_size", value=5, fact_id="F2")
    changeset = ChangeSet(key="party_size", kind=ChangeKind.CHANGED, new_fact=fact)
    explanation = build_explanation(graph, [changeset], None, None, {})
    assert explanation.kept_work == []
    assert explanation.invalidated_work == []
    assert explanation.recompute == []
    assert explanation.summary.plan_status == "not_computed"


# ===========================================================================
# BackspaceCore-level: explain_change / build_explanation via the facade
# ===========================================================================


def test_20_repeated_explanation_is_deterministic():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W2")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)

    changeset_id = change.changeset.changeset_id
    first = core.build_explanation(changeset_id).to_dict()
    for _ in range(10):
        again = core.build_explanation(changeset_id).to_dict()
        # explanation_id/created_at are per-call identifiers, not part of
        # the deterministic content - compare everything else.
        again.pop("explanation_id")
        again.pop("created_at")
        expected = dict(first)
        expected.pop("explanation_id")
        expected.pop("created_at")
        assert again == expected
    assert core.explain_change(changeset_id) == core.explain_change(changeset_id)


def test_21_multiple_changesets_can_be_explained_independently():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    core.register_work(WorkItem(kind="hotel_search", work_id="W1"))
    f_size = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    f_dest = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    core.register_dependency(FTW, f_size.fact.fact_id, "W2")
    core.register_dependency(FTW, f_dest.fact.fact_id, "W1")

    change_size = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change_size.changeset)
    change_dest = core.assert_fact("destination", "Kerala", source="user", turn_id="t3")
    core.invalidate(change_dest.changeset)

    explanation_size = core.build_explanation(change_size.changeset.changeset_id)
    explanation_dest = core.build_explanation(change_dest.changeset.changeset_id)

    assert [w.work_id for w in explanation_size.invalidated_work] == ["W2"]
    assert [w.work_id for w in explanation_dest.invalidated_work] == ["W1"]


def test_22_unknown_changeset_raises_changesetnotfounderror():
    core = BackspaceCore()
    with pytest.raises(ChangeSetNotFoundError) as excinfo:
        core.build_explanation("does-not-exist")
    assert excinfo.value.changeset_id == "does-not-exist"
    with pytest.raises(ChangeSetNotFoundError):
        core.explain_change("does-not-exist")


def test_23_reset_clears_changeset_and_invalidation_history():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W2")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)
    changeset_id = change.changeset.changeset_id
    assert core.get_changeset(changeset_id) is not None

    core.reset()

    assert core.get_changeset(changeset_id) is None
    assert core.get_invalidation_for_changeset(changeset_id) is None
    with pytest.raises(ChangeSetNotFoundError):
        core.build_explanation(changeset_id)


def test_24_snapshot_remains_consistent_after_building_an_explanation():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W2")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)

    before = core.snapshot()
    core.build_explanation(change.changeset.changeset_id)
    core.explain_change(change.changeset.changeset_id)
    after = core.snapshot()
    assert before == after


# ===========================================================================
# 19 / read-only guarantee
# ===========================================================================


def test_19_explanation_causes_no_state_mutation():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="hotel_search", work_id="W1"))
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    claim = core.register_claim(Claim(text="Hotel price is ₹18,000", claim_id="C1", work_id="W2"))
    f_dest = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    f_size = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f_dest.fact.fact_id, "W1")
    core.register_dependency(FTW, f_size.fact.fact_id, "W2")
    core.register_dependency(WTC, "W2", "C1")
    core.mark_claim_spoken("C1")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)
    changeset_id = change.changeset.changeset_id

    before_snapshot = core.snapshot()
    before_fact_version = core.get_fact("party_size").version
    before_claim_status = core.get_claim("C1").status
    before_retraction = core.get_retraction("C1")

    for _ in range(5):
        core.build_explanation(changeset_id)
        core.explain_change(changeset_id)

    after_snapshot = core.snapshot()
    assert before_snapshot == after_snapshot
    assert core.get_fact("party_size").version == before_fact_version
    assert core.get_claim("C1").status == before_claim_status
    assert core.get_claim("C1") is claim  # same object, never replaced
    assert core.get_retraction("C1") == before_retraction  # still None - never auto-retracted


def test_explanation_never_calls_plan_recompute_side_effects_beyond_reading():
    """plan_recompute is already read-only (Phase 6); this just confirms
    building an explanation doesn't touch work status either."""
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W2")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)

    before_status = core.graph.get_work("W2").status
    core.build_explanation(change.changeset.changeset_id)
    assert core.graph.get_work("W2").status == before_status


# ===========================================================================
# CANONICAL END-TO-END TEST (Phase 8 brief, verbatim)
# ===========================================================================


def test_canonical_scenario_full_explanation():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="hotel_search", work_id="W1"))
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    core.register_work(WorkItem(kind="recommendation", work_id="W3"))
    core.register_claim(Claim(text="Hotels found in Goa", claim_id="C1", work_id="W1"))
    core.register_claim(Claim(text="Hotel price is ₹18,000", claim_id="C2", work_id="W2"))
    core.register_claim(Claim(text="This is a good option", claim_id="C3", work_id="W3"))

    destination = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    party_size = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    budget = core.assert_fact("budget", 20000, source="user", turn_id="t1")

    core.register_dependency(FTW, destination.fact.fact_id, "W1")
    core.register_dependency(FTW, party_size.fact.fact_id, "W2")
    core.register_dependency(FTW, budget.fact.fact_id, "W2")
    core.register_dependency(WTW, "W1", "W3")
    core.register_dependency(WTW, "W2", "W3")
    core.register_dependency(WTC, "W1", "C1")
    core.register_dependency(WTC, "W2", "C2")
    core.register_dependency(WTC, "W3", "C3")

    core.mark_claim_spoken("C2")
    core.mark_claim_spoken("C3")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)
    explanation = core.build_explanation(change.changeset.changeset_id)

    # CHANGED
    assert len(explanation.changes) == 1
    assert explanation.changes[0].key == "party_size"
    assert explanation.changes[0].previous_value == 2
    assert explanation.changes[0].new_value == 5

    # KEPT
    kept_ids = {w.work_id for w in explanation.kept_work}
    assert kept_ids == {"W1"}

    # INVALIDATED
    invalidated_work_ids = {w.work_id for w in explanation.invalidated_work}
    assert invalidated_work_ids == {"W2", "W3"}
    invalidated_claim_ids = {c.claim_id for c in explanation.invalidated_claims}
    assert invalidated_claim_ids == {"C2", "C3"}

    # RETRACTION - both spoken, neither yet explicitly retracted
    for claim_explanation in explanation.invalidated_claims:
        assert claim_explanation.spoken is True
        assert claim_explanation.retraction_required is True
        assert claim_explanation.retracted is False

    # RECOMPUTE - W2 then W3, never W1
    assert [r.work_id for r in explanation.recompute] == ["W2", "W3"]

    # Text rendering agrees with the structured counts
    text = core.explain_change(change.changeset.changeset_id)
    assert "party_size changed from 2 to 5" in text
    assert "2 work items were invalidated" in text
    assert "1 work item was preserved" in text
    assert "2 spoken claims require retraction" in text
    assert "2 work items require recomputation" in text


def test_canonical_scenario_c1_remains_valid_because_its_dependency_did_not_change():
    """"KEPT: C1 if its dependency remains valid" - W1/C1's dependency
    (destination) never changed, so C1 must never appear invalidated."""
    core = BackspaceCore()
    core.register_work(WorkItem(kind="hotel_search", work_id="W1"))
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    core.register_claim(Claim(text="Hotels found in Goa", claim_id="C1", work_id="W1"))
    destination = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    party_size = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, destination.fact.fact_id, "W1")
    core.register_dependency(FTW, party_size.fact.fact_id, "W2")
    core.register_dependency(WTC, "W1", "C1")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)
    explanation = core.build_explanation(change.changeset.changeset_id)

    assert "C1" not in [c.claim_id for c in explanation.invalidated_claims]
    assert core.get_claim("C1").status.value == "generated"


# ===========================================================================
# render_explanation_text - determinism, no hard-coded numbers
# ===========================================================================


def test_render_explanation_text_uses_summary_counts_not_hardcoded_numbers():
    graph = DependencyGraph()
    graph.register_work(_work("W2"))
    graph.register_work(_work("W3"))
    graph.register_dependency(FTW, "F2", "W2")
    graph.register_dependency(WTW, "W2", "W3")
    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    explanation = build_explanation(graph, [changeset], invalidation, plan, {})

    text = render_explanation_text(explanation)
    assert f"{explanation.summary.invalidated_work_count} work item" in text
    assert f"{explanation.summary.recompute_count} work item" in text


def test_render_explanation_text_for_a_blocked_plan_says_blocked_not_a_fake_count():
    graph = DependencyGraph()
    work = graph.register_work(_work("W4"))
    work.depends_on_work = ["ghost"]
    work.status = WorkStatus.STALE
    graph.register_dependency(FTW, "F2", "W4")
    changeset = _changed("party_size", 2, 5, fact_id="F2")
    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    explanation = build_explanation(graph, [changeset], invalidation, plan, {})

    text = render_explanation_text(explanation)
    assert "blocked" in text.lower()


def test_render_explanation_text_no_semantic_change_case():
    fact = Fact(key="party_size", value=5, fact_id="F2")
    changeset = ChangeSet(key="party_size", kind=ChangeKind.UNCHANGED, new_fact=fact, previous_fact=fact)
    graph = DependencyGraph()
    invalidation = invalidate(graph, changeset)
    plan = plan_recompute(graph, invalidation)
    explanation = build_explanation(graph, [changeset], invalidation, plan, {})
    text = render_explanation_text(explanation)
    assert "No semantic change." in text