"""The invalidation engine - `invalidate()`/`invalidate_many()`.

Most tests here drive the engine directly against a hand-built
``DependencyGraph`` and hand-built ``ChangeSet``s, the same way
``test_graph.py`` tests ``DependencyGraph`` in isolation - this lets a test
say precisely "F2 changed" without going through a full FactNotebook
version-history dance for every scenario. One dedicated test
(``test_end_to_end_through_backspace_core_assert_fact_then_invalidate``) goes
through the real ``BackspaceCore.assert_fact`` -> ``invalidate`` pipeline, to
lock in the one non-obvious correctness point this phase turned up: a
CHANGED walk has to start from the *previous* fact_id, not the new one - see
``invalidation.py``'s comment for why.
"""

from __future__ import annotations

import pytest

from app.backspace import (
    BackspaceCore,
    ChangeKind,
    ChangeSet,
    Claim,
    ClaimStatus,
    DependencyGraph,
    DependencyKind,
    Fact,
    FactStatus,
    Invalidation,
    WorkItem,
    WorkStatus,
    invalidate,
    invalidate_many,
)

FTW = DependencyKind.FACT_TO_WORK
WTW = DependencyKind.WORK_TO_WORK
WTC = DependencyKind.WORK_TO_CLAIM


def _work(work_id: str, kind: str = "generic") -> WorkItem:
    return WorkItem(kind=kind, work_id=work_id)


def _claim(claim_id: str, text: str = "a claim") -> Claim:
    return Claim(text=text, claim_id=claim_id)


def _changed(key: str, old_value, new_value, *, fact_id: str) -> ChangeSet:
    """A CHANGED ChangeSet whose `previous_fact.fact_id` is exactly the id a
    graph edge would have been registered against - the realistic shape
    FactNotebook actually produces (see the `Fact` supersession discipline
    in facts.py: fact_id survives a version transition)."""
    previous = Fact(key=key, value=old_value, fact_id=fact_id, status=FactStatus.SUPERSEDED)
    new_fact = Fact(key=key, value=new_value, fact_id=f"{fact_id}-v2", supersedes=fact_id)
    return ChangeSet(key=key, kind=ChangeKind.CHANGED, new_fact=new_fact, previous_fact=previous)


def _new(key: str, value, *, fact_id: str) -> ChangeSet:
    fact = Fact(key=key, value=value, fact_id=fact_id)
    return ChangeSet(key=key, kind=ChangeKind.NEW, new_fact=fact)


def _unchanged(key: str, value, *, fact_id: str) -> ChangeSet:
    fact = Fact(key=key, value=value, fact_id=fact_id)
    return ChangeSet(key=key, kind=ChangeKind.UNCHANGED, new_fact=fact, previous_fact=fact)


@pytest.fixture
def canonical_graph() -> DependencyGraph:
    """F1=destination, F2=party_size, F3=budget; W1..W4; C1..C4 - one claim
    per work item, exactly as in the phase brief."""
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


# -- 1. irrelevant fact change --------------------------------------------------

def test_irrelevant_fact_change_invalidates_nothing():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    graph.register_dependency(FTW, "F1", "W1")
    changeset = _changed("something_else", "a", "b", fact_id="F-unrelated")

    result = invalidate(graph, changeset)

    assert result.invalidated_work_ids == []
    assert result.invalidated_claim_ids == []
    assert result.kept_work_ids == ["W1"]


# -- 2. direct invalidation -----------------------------------------------------

def test_direct_invalidation():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_dependency(FTW, "F2", "W3")
    changeset = _changed("party_size", 2, 5, fact_id="F2")

    result = invalidate(graph, changeset)

    assert result.invalidated_work_ids == ["W3"]
    assert graph.get_work("W3").status is WorkStatus.STALE


# -- 3. transitive invalidation --------------------------------------------------

def test_transitive_invalidation_along_a_chain():
    graph = DependencyGraph()
    for wid in ("W3", "W4", "W5"):
        graph.register_work(_work(wid))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(WTW, "W3", "W4")
    graph.register_dependency(WTW, "W4", "W5")
    changeset = _changed("party_size", 2, 5, fact_id="F2")

    result = invalidate(graph, changeset)

    assert result.invalidated_work_ids == ["W3", "W4", "W5"]
    assert all(graph.get_work(w).status is WorkStatus.STALE for w in ("W3", "W4", "W5"))


# -- 4. canonical Goa graph -------------------------------------------------------

def test_canonical_graph_f2_change_matches_the_brief_exactly(canonical_graph):
    changeset = _changed("party_size", 2, 5, fact_id="F2")
    result = invalidate(canonical_graph, changeset)

    assert result.invalidated_work_ids == ["W3", "W4"]
    assert result.invalidated_claim_ids == ["C3", "C4"]
    assert sorted(result.kept_work_ids) == ["W1", "W2"]

    assert canonical_graph.get_work("W3").status is WorkStatus.STALE
    assert canonical_graph.get_work("W4").status is WorkStatus.STALE
    assert canonical_graph.get_claim("C3").status is ClaimStatus.INVALIDATED
    assert canonical_graph.get_claim("C4").status is ClaimStatus.INVALIDATED

    # Untouched.
    assert canonical_graph.get_work("W1").status is WorkStatus.PENDING
    assert canonical_graph.get_work("W2").status is WorkStatus.PENDING
    assert canonical_graph.get_claim("C1").status is ClaimStatus.GENERATED
    assert canonical_graph.get_claim("C2").status is ClaimStatus.GENERATED


# -- 5. unaffected branch ---------------------------------------------------------

def test_unaffected_branch_is_disjoint_from_affected_set(canonical_graph):
    changeset = _changed("party_size", 2, 5, fact_id="F2")
    result = invalidate(canonical_graph, changeset)

    affected = set(result.invalidated_work_ids) | set(result.invalidated_claim_ids)
    unaffected = {"W1", "W2", "C1", "C2"}
    assert affected == {"W3", "W4", "C3", "C4"}
    assert affected.isdisjoint(unaffected)


# -- 6. branching graph ------------------------------------------------------------

def test_branching_graph_f1_reaches_its_branch_not_the_other_facts_branch():
    graph = DependencyGraph()
    for wid in ("W1", "W3", "W4"):
        graph.register_work(_work(wid))
    graph.register_dependency(FTW, "F1", "W1")
    graph.register_dependency(WTW, "W1", "W4")
    graph.register_dependency(FTW, "F2", "W3")

    changeset = _changed("destination", "Goa", "Kerala", fact_id="F1")
    result = invalidate(graph, changeset)

    assert sorted(result.invalidated_work_ids) == ["W1", "W4"]
    assert "W3" not in result.invalidated_work_ids
    assert "W3" in result.kept_work_ids


# -- 7/8. multiple changed facts / duplicate affected work ------------------------

def test_multiple_changed_facts_merge_without_duplicating_shared_work():
    graph = DependencyGraph()
    for wid in ("W3", "W5"):
        graph.register_work(_work(wid))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F3", "W3")   # W3 depends on both F2 and F3
    graph.register_dependency(FTW, "F3", "W5")   # W5 depends only on F3

    f2_change = _changed("party_size", 2, 5, fact_id="F2")
    f3_change = _changed("budget", 20000, 30000, fact_id="F3")

    result = invalidate_many(graph, [f2_change, f3_change])

    assert result.invalidated_work_ids.count("W3") == 1
    assert sorted(result.invalidated_work_ids) == ["W3", "W5"]
    assert len(result.changed_facts) == 2
    assert set(result.changed_fact_ids) == {"F2-v2", "F3-v2"}


def test_work_depending_on_neither_changed_fact_stays_valid():
    graph = DependencyGraph()
    for wid in ("W3", "W1"):
        graph.register_work(_work(wid))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F1", "W1")  # unrelated to F2/F3

    f2_change = _changed("party_size", 2, 5, fact_id="F2")
    f3_change = _changed("budget", 20000, 30000, fact_id="F3")  # F3 has no dependents at all

    result = invalidate_many(graph, [f2_change, f3_change])

    assert result.invalidated_work_ids == ["W3"]
    assert "W1" in result.kept_work_ids


# -- 9. duplicate affected claims --------------------------------------------------

def test_shared_claim_appears_once_across_two_changesets():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_work(_work("W4"))
    graph.register_claim(_claim("C4"))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F3", "W3")
    graph.register_dependency(WTW, "W3", "W4")
    graph.register_dependency(WTC, "W4", "C4")

    f2_change = _changed("party_size", 2, 5, fact_id="F2")
    f3_change = _changed("budget", 20000, 30000, fact_id="F3")

    result = invalidate_many(graph, [f2_change, f3_change])

    assert result.invalidated_claim_ids.count("C4") == 1
    assert result.invalidated_claim_ids == ["C4"]


# -- 10/11. generated (unspoken) vs already-spoken claims --------------------------

def test_generated_unspoken_claim_becomes_invalidated():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    claim = graph.register_claim(_claim("C3"))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(WTC, "W3", "C3")
    assert claim.status is ClaimStatus.GENERATED
    assert claim.spoken_at is None

    invalidate(graph, _changed("party_size", 2, 5, fact_id="F2"))

    assert claim.status is ClaimStatus.INVALIDATED
    assert claim.spoken_at is None  # never spoken, stays None


def test_already_spoken_claim_becomes_invalidated_without_losing_spoken_at():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    claim = _claim("C3")
    claim.status = ClaimStatus.SPOKEN
    claim.spoken_at = 555.0
    graph.register_claim(claim)
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(WTC, "W3", "C3")

    invalidate(graph, _changed("party_size", 2, 5, fact_id="F2"))

    assert claim.status is ClaimStatus.INVALIDATED
    # The one bit later phases need is preserved: this claim was spoken.
    assert claim.spoken_at == 555.0


# -- 12/13/19. idempotency and repeated calls ---------------------------------------

def test_repeated_invalidation_is_idempotent(canonical_graph):
    changeset = _changed("party_size", 2, 5, fact_id="F2")
    first = invalidate(canonical_graph, changeset)
    baseline = (
        list(first.invalidated_work_ids),
        list(first.invalidated_claim_ids),
        sorted(first.kept_work_ids),
    )
    for _ in range(10):
        again = invalidate(canonical_graph, changeset)
        assert (
            list(again.invalidated_work_ids),
            list(again.invalidated_claim_ids),
            sorted(again.kept_work_ids),
        ) == baseline
    # Statuses did not get corrupted by ten repeated transitions.
    assert canonical_graph.get_work("W3").status is WorkStatus.STALE
    assert canonical_graph.get_claim("C3").status is ClaimStatus.INVALIDATED


def test_already_invalid_work_is_not_corrupted_by_a_second_relevant_change():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(FTW, "F3", "W3")

    invalidate(graph, _changed("party_size", 2, 5, fact_id="F2"))
    assert graph.get_work("W3").status is WorkStatus.STALE

    # A second, different change also reaching W3 must not corrupt it.
    result = invalidate(graph, _changed("budget", 20000, 30000, fact_id="F3"))
    assert result.invalidated_work_ids == ["W3"]
    assert graph.get_work("W3").status is WorkStatus.STALE


def test_repeated_identical_calls_on_separately_built_equal_graphs_agree():
    def build() -> DependencyGraph:
        graph = DependencyGraph()
        graph.register_work(_work("W3"))
        graph.register_work(_work("W4"))
        graph.register_dependency(FTW, "F2", "W3")
        graph.register_dependency(WTW, "W3", "W4")
        return graph

    a, b = build(), build()
    result_a = invalidate(a, _changed("party_size", 2, 5, fact_id="F2"))
    result_b = invalidate(b, _changed("party_size", 2, 5, fact_id="F2"))
    assert result_a.invalidated_work_ids == result_b.invalidated_work_ids
    assert sorted(result_a.kept_work_ids) == sorted(result_b.kept_work_ids)


# -- 14/15/16/17. ChangeSet kinds ----------------------------------------------------

def test_no_op_unchanged_value_through_the_real_notebook_pipeline():
    """The literal example from the brief: party_size=5, then =5 again."""
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    first = core.assert_fact("party_size", 5, source="user", turn_id="t1")
    core.register_dependency(FTW, first.fact.fact_id, "W3")

    repeat = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    assert repeat.status is ChangeKind.UNCHANGED
    # UNCHANGED never produces a changeset - nothing to even invalidate with.
    assert repeat.changeset is None
    assert core.graph.get_work("W3").status is WorkStatus.PENDING


def test_unchanged_changeset_passed_directly_invalidates_nothing():
    """Safety net for the note in the brief: FactNotebook should never emit
    an UNCHANGED ChangeSet (it emits `changeset=None` for UNCHANGED), but
    invalidate() must still be safe if handed one manually."""
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_dependency(FTW, "F2", "W3")

    changeset = _unchanged("party_size", 5, fact_id="F2")
    result = invalidate(graph, changeset)

    assert result.invalidated_work_ids == []
    assert result.kept_work_ids == ["W3"]
    assert graph.get_work("W3").status is WorkStatus.PENDING


def test_new_changeset_invalidates_nothing():
    graph = DependencyGraph()
    graph.register_work(_work("W1"))
    graph.register_dependency(FTW, "F1", "W1")

    # A NEW fact for a *different* key - nothing could depend on its
    # brand-new fact_id yet.
    changeset = _new("destination", "Goa", fact_id="F-new")
    result = invalidate(graph, changeset)

    assert result.invalidated_work_ids == []
    assert result.kept_work_ids == ["W1"]
    assert result.changed_facts == []  # NEW does not count as "changed"


def test_changed_changeset_performs_dependency_invalidation():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_dependency(FTW, "F2", "W3")
    changeset = _changed("party_size", 2, 5, fact_id="F2")

    result = invalidate(graph, changeset)

    assert result.invalidated_work_ids == ["W3"]
    assert len(result.changed_facts) == 1
    assert result.reason  # a human-readable reason was produced


# -- 18. deterministic ordering ------------------------------------------------------

def test_invalidation_result_ordering_is_deterministic(canonical_graph):
    changeset = _changed("party_size", 2, 5, fact_id="F2")
    results = [invalidate(canonical_graph, changeset) for _ in range(5)]
    for result in results[1:]:
        assert result.invalidated_work_ids == results[0].invalidated_work_ids
        assert result.invalidated_claim_ids == results[0].invalidated_claim_ids
        assert result.kept_work_ids == results[0].kept_work_ids


# -- 20. session isolation ------------------------------------------------------------

def test_session_isolation_between_two_backspace_core_instances():
    core_a = BackspaceCore()
    core_b = BackspaceCore()

    core_a.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    core_b.register_work(WorkItem(kind="price_calculation", work_id="W8"))

    fa = core_a.assert_fact("party_size", 2, source="user", turn_id="t1")
    fb = core_b.assert_fact("party_size", 2, source="user", turn_id="t1")
    core_a.register_dependency(FTW, fa.fact.fact_id, "W3")
    core_b.register_dependency(FTW, fb.fact.fact_id, "W8")

    change_a = core_a.assert_fact("party_size", 5, source="user", turn_id="t2")
    result_a = core_a.invalidate(change_a.changeset)

    assert result_a.invalidated_work_ids == ["W3"]
    # Core B's W8 must never appear - and B's own graph must be untouched.
    assert "W8" not in result_a.invalidated_work_ids
    assert core_b.graph.get_work("W8").status is WorkStatus.PENDING
    assert core_b.get_fact("party_size").value == 2


# -- 21. reset ---------------------------------------------------------------------------

def test_reset_clears_graph_state_invalidation_depends_on():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W3")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)
    assert core.graph.get_work("W3").status is WorkStatus.STALE

    core.reset()

    assert core.graph.get_work("W3") is None
    assert core.get_fact("party_size") is None


# -- 22. facade delegation -----------------------------------------------------------

def test_facade_invalidate_delegates_to_the_engine_against_its_own_graph():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W3")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")

    result = core.invalidate(change.changeset)

    assert isinstance(result, Invalidation)
    assert result.invalidated_work_ids == ["W3"]
    assert core.graph.get_work("W3").status is WorkStatus.STALE


def test_facade_invalidate_many_delegates_and_merges():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    f2 = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    f3 = core.assert_fact("budget", 20000, source="user", turn_id="t1")
    core.register_dependency(FTW, f2.fact.fact_id, "W3")
    core.register_dependency(FTW, f3.fact.fact_id, "W3")

    change_f2 = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    change_f3 = core.assert_fact("budget", 30000, source="user", turn_id="t2")

    result = core.invalidate_many([change_f2.changeset, change_f3.changeset])

    assert result.invalidated_work_ids == ["W3"]  # not duplicated
    assert len(result.changed_facts) == 2


def test_assert_fact_is_still_not_automatically_wired_to_invalidate():
    """Phase 5 explicitly does not connect the two - CHANGED must still
    return invalidation=None/plan=None from assert_fact itself."""
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W3")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    assert change.status is ChangeKind.CHANGED
    assert change.changeset is not None
    assert change.invalidation is None
    assert change.plan is None
    # W3's status is untouched until invalidate() is called explicitly.
    assert core.graph.get_work("W3").status is WorkStatus.PENDING


# -- 23. no graph corruption --------------------------------------------------------

def test_invalidation_never_mutates_graph_structure(canonical_graph):
    before_edges_f1 = canonical_graph.get_direct_dependents("F1")
    before_edges_w1 = canonical_graph.get_direct_dependents("W1")
    before_work_ids = canonical_graph.all_work_ids()
    before_claim_ids = canonical_graph.all_claim_ids()

    invalidate(canonical_graph, _changed("party_size", 2, 5, fact_id="F2"))

    assert canonical_graph.get_direct_dependents("F1") == before_edges_f1
    assert canonical_graph.get_direct_dependents("W1") == before_edges_w1
    assert canonical_graph.all_work_ids() == before_work_ids
    assert canonical_graph.all_claim_ids() == before_claim_ids
    # No new nodes/edges appeared as a side effect.
    assert canonical_graph.get_direct_dependents("W3") == ["W4", "C3"]


# -- 24. provenance / reason preservation --------------------------------------------

def test_reason_describes_the_change():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_dependency(FTW, "F2", "W3")
    result = invalidate(graph, _changed("party_size", 2, 5, fact_id="F2"))
    assert "party_size" in result.reason
    assert "2" in result.reason
    assert "5" in result.reason


def test_reason_for_unchanged_or_empty_batch_says_so():
    graph = DependencyGraph()
    result = invalidate(graph, _unchanged("party_size", 5, fact_id="F2"))
    assert result.reason == "no semantic change"


def test_changed_facts_carry_full_provenance_through_to_the_result():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_dependency(FTW, "F2", "W3")
    previous = Fact(key="party_size", value=2, fact_id="F2", source="user", turn_id="t1", goal_id="g1")
    new_fact = Fact(
        key="party_size", value=5, fact_id="F2-v2", supersedes="F2",
        source="voice_transcript", turn_id="t2", goal_id="g1", confidence=0.87,
    )
    changeset = ChangeSet(key="party_size", kind=ChangeKind.CHANGED, new_fact=new_fact, previous_fact=previous)

    result = invalidate(graph, changeset)

    assert result.changed_facts[0].source == "voice_transcript"
    assert result.changed_facts[0].confidence == 0.87
    assert result.changed_facts[0].goal_id == "g1"


# -- extra: the exact bug this phase caught, locked in as a regression test ---------

def test_end_to_end_through_backspace_core_assert_fact_then_invalidate():
    """assert_fact mints a brand-new fact_id for the changed value; the
    dependency graph is wired against the *previous* fact_id. invalidate()
    has to walk from `changeset.previous_fact.fact_id`, not
    `changeset.new_fact.fact_id` - get this backwards and every real
    (FactNotebook-driven) invalidation silently finds nothing."""
    core = BackspaceCore()
    for wid, kind in (("W1", "flight_search"), ("W2", "hotel_search"),
                      ("W3", "price_calculation"), ("W4", "recommendation")):
        core.register_work(WorkItem(kind=kind, work_id=wid))
    for cid in ("C1", "C2", "C3", "C4"):
        core.register_claim(Claim(text=cid, claim_id=cid))

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
    core.register_dependency(WTC, "W1", "C1")
    core.register_dependency(WTC, "W2", "C2")
    core.register_dependency(WTC, "W3", "C3")
    core.register_dependency(WTC, "W4", "C4")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    result = core.invalidate(change.changeset)

    assert result.invalidated_work_ids == ["W3", "W4"]
    assert result.invalidated_claim_ids == ["C3", "C4"]
    assert sorted(result.kept_work_ids) == ["W1", "W2"]


# ===========================================================================
# Phase 7 - spoken vs unspoken claim classification on the Invalidation result
# ===========================================================================


def test_invalidation_classifies_unspoken_invalidated_claims():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    graph.register_claim(_claim("C3"))  # never marked spoken
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(WTC, "W3", "C3")

    result = invalidate(graph, _changed("party_size", 2, 5, fact_id="F2"))

    assert result.invalidated_claim_ids == ["C3"]
    assert result.unspoken_invalidated_claim_ids == ["C3"]
    assert result.spoken_invalidated_claim_ids == []


def test_invalidation_classifies_spoken_invalidated_claims():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    claim = _claim("C3")
    claim.status = ClaimStatus.SPOKEN
    claim.spoken_at = 42.0
    graph.register_claim(claim)
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(WTC, "W3", "C3")

    result = invalidate(graph, _changed("party_size", 2, 5, fact_id="F2"))

    assert result.invalidated_claim_ids == ["C3"]
    assert result.spoken_invalidated_claim_ids == ["C3"]
    assert result.unspoken_invalidated_claim_ids == []
    assert claim.spoken_at == 42.0  # preserved through invalidation


def test_invalidation_partitions_a_mix_of_spoken_and_unspoken_claims():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    spoken = _claim("C-spoken")
    spoken.spoken_at = 1.0
    graph.register_claim(spoken)
    graph.register_claim(_claim("C-unspoken"))
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(WTC, "W3", "C-spoken")
    graph.register_dependency(WTC, "W3", "C-unspoken")

    result = invalidate(graph, _changed("party_size", 2, 5, fact_id="F2"))

    assert set(result.invalidated_claim_ids) == {"C-spoken", "C-unspoken"}
    assert result.spoken_invalidated_claim_ids == ["C-spoken"]
    assert result.unspoken_invalidated_claim_ids == ["C-unspoken"]


def test_invalidation_to_dict_includes_the_spoken_unspoken_split():
    graph = DependencyGraph()
    graph.register_work(_work("W3"))
    claim = _claim("C3")
    claim.spoken_at = 1.0
    graph.register_claim(claim)
    graph.register_dependency(FTW, "F2", "W3")
    graph.register_dependency(WTC, "W3", "C3")

    result = invalidate(graph, _changed("party_size", 2, 5, fact_id="F2"))
    data = result.to_dict()
    assert data["spoken_invalidated_claim_ids"] == ["C3"]
    assert data["unspoken_invalidated_claim_ids"] == []
