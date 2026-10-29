"""BackspaceCore facade tests.

Deliberately exercised through the facade's own public methods
(``assert_fact``/``update_fact``/``get_fact``/``get_fact_history``/
``snapshot``/``reset``) rather than by reaching into ``core.facts`` - the
point of this file is to prove the facade is a faithful, thin wrapper, not to
re-test FactNotebook's internals a second time (that's what
``test_facts.py`` is for).
"""

from __future__ import annotations

import pytest

from app.backspace import (
    BackspaceCore,
    ChangeKind,
    Claim,
    DependencyCycleError,
    DependencyKind,
    FactNotFoundError,
    NodeNotRegisteredError,
    WorkItem,
)


def test_assert_fact_new():
    core = BackspaceCore()
    update = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    assert update.status is ChangeKind.NEW
    assert update.previous is None
    assert update.changeset is None
    assert update.fact.value == "Goa"
    assert update.fact.version == 1


def test_assert_fact_unchanged():
    core = BackspaceCore()
    core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    update = core.assert_fact("destination", "Goa", source="user", turn_id="t2")
    assert update.status is ChangeKind.UNCHANGED
    assert update.changeset is None
    assert update.fact.version == 1


def test_assert_fact_changed_matches_the_goa_five_example_from_the_brief():
    core = BackspaceCore()
    core.assert_fact("party_size", 2, source="user", turn_id="t1", goal_id="g1")
    update = core.assert_fact("party_size", 5, source="user", turn_id="t2", goal_id="g1")

    assert update.status is ChangeKind.CHANGED
    assert update.fact.value == 5
    assert update.fact.version == 2
    assert update.previous.value == 2
    assert update.previous.status.value == "superseded"

    assert update.changeset is not None
    assert update.changeset.key == "party_size"
    assert update.changeset.previous_fact.value == 2
    assert update.changeset.new_fact.value == 5

    # This phase creates the ChangeSet and stops - no invalidation, no plan.
    assert update.invalidation is None
    assert update.plan is None


def test_update_fact_by_id():
    core = BackspaceCore()
    first = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    update = core.update_fact(first.fact.fact_id, 5, source="user", turn_id="t2")
    assert update.status is ChangeKind.CHANGED
    assert core.get_fact("party_size").value == 5


def test_update_fact_missing_id_raises():
    core = BackspaceCore()
    with pytest.raises(FactNotFoundError):
        core.update_fact("nope", 1, source="user", turn_id="t1")


def test_get_fact_and_get_fact_history_delegate_correctly():
    core = BackspaceCore()
    assert core.get_fact("party_size") is None
    assert core.get_fact_history("party_size") == []
    core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.assert_fact("party_size", 5, source="user", turn_id="t2")
    assert core.get_fact("party_size").value == 5
    assert [f.value for f in core.get_fact_history("party_size")] == [2, 5]


def test_snapshot_reflects_current_state():
    core = BackspaceCore()
    core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    snapshot = core.snapshot()
    assert set(snapshot["facts"].keys()) == {"party_size", "destination"}
    assert snapshot["facts"]["party_size"]["current"]["value"] == 5


def test_reset_clears_this_instance_only():
    core = BackspaceCore()
    core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.reset()
    assert core.get_fact("party_size") is None
    assert core.snapshot()["facts"] == {}


def test_two_backspace_core_instances_are_fully_isolated():
    a = BackspaceCore()
    b = BackspaceCore()
    a.assert_fact("party_size", 5, source="user", turn_id="t1")
    b.assert_fact("party_size", 2, source="user", turn_id="t1")
    assert a.get_fact("party_size").value == 5
    assert b.get_fact("party_size").value == 2

    a.reset()
    assert a.get_fact("party_size") is None
    assert b.get_fact("party_size").value == 2  # untouched by a's reset


def test_backspace_core_holds_no_class_level_mutable_state():
    """Every piece of state must live on the instance, not the class -
    otherwise two sessions could end up sharing a notebook by accident."""
    for name, value in vars(BackspaceCore).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, (dict, list, set)), (
            f"BackspaceCore.{name} is a mutable class attribute: {value!r}"
        )
    # Constructing many instances must never cause them to converge.
    instances = [BackspaceCore() for _ in range(5)]
    for i, core in enumerate(instances):
        core.assert_fact("party_size", i, source="user", turn_id="t1")
    assert [c.get_fact("party_size").value for c in instances] == [0, 1, 2, 3, 4]


# ===========================================================================
# Phase 4 - facade delegation to DependencyGraph
# ===========================================================================


def test_facade_register_work_and_claim_delegate_to_the_graph():
    core = BackspaceCore()
    work = core.register_work(WorkItem(kind="flight_search", work_id="W1"))
    claim = core.register_claim(Claim(text="a flight claim", claim_id="C1"))
    assert work.work_id == "W1"
    assert claim.claim_id == "C1"
    # Reachable both through the facade and through the underlying graph -
    # proof this is delegation, not a second, parallel registry.
    assert core.graph.get_direct_dependents("W1") == []
    core.register_dependency(DependencyKind.WORK_TO_CLAIM, "W1", "C1")
    assert core.get_direct_dependents("W1") == ["C1"]
    assert core.graph.get_direct_dependents("W1") == ["C1"]


def test_facade_get_dependencies_and_transitive_dependents_delegate():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W3"))
    core.register_work(WorkItem(kind="recommendation", work_id="W4"))
    core.register_dependency(DependencyKind.FACT_TO_WORK, "F2", "W3")
    core.register_dependency(DependencyKind.WORK_TO_WORK, "W3", "W4")

    assert core.get_dependencies("W4") == ["W3"]
    assert core.get_transitive_dependents("F2") == ["W3", "W4"]


def test_facade_propagates_graph_errors_unchanged():
    core = BackspaceCore()
    with pytest.raises(NodeNotRegisteredError):
        core.register_dependency(DependencyKind.WORK_TO_WORK, "W1", "W2")

    core.register_work(WorkItem(kind="a", work_id="W1"))
    core.register_work(WorkItem(kind="b", work_id="W2"))
    core.register_dependency(DependencyKind.WORK_TO_WORK, "W1", "W2")
    with pytest.raises(DependencyCycleError):
        core.register_dependency(DependencyKind.WORK_TO_WORK, "W2", "W1")


def test_facade_reset_clears_both_facts_and_graph():
    core = BackspaceCore()
    core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_work(WorkItem(kind="flight_search", work_id="W1"))
    core.register_dependency(DependencyKind.FACT_TO_WORK, "F1", "W1")

    core.reset()

    assert core.get_fact("party_size") is None
    assert core.get_direct_dependents("F1") == []
    with pytest.raises(NodeNotRegisteredError):
        core.register_dependency(DependencyKind.WORK_TO_WORK, "W1", "W-anything")


# ===========================================================================
# Phase 4 - regression: Phase 3 fact behavior must be completely unchanged
# ===========================================================================


def test_fact_behavior_regression_new_unchanged_changed_are_identical_to_phase_3():
    core = BackspaceCore()

    new_update = core.assert_fact("party_size", 2, source="user", turn_id="t1", goal_id="g1")
    assert new_update.status is ChangeKind.NEW
    assert new_update.previous is None
    assert new_update.changeset is None
    assert new_update.invalidation is None
    assert new_update.plan is None

    unchanged_update = core.assert_fact("party_size", 2, source="user", turn_id="t2")
    assert unchanged_update.status is ChangeKind.UNCHANGED
    assert unchanged_update.changeset is None
    assert unchanged_update.fact.version == 1

    changed_update = core.assert_fact("party_size", 5, source="user", turn_id="t3")
    assert changed_update.status is ChangeKind.CHANGED
    assert changed_update.fact.version == 2
    assert changed_update.previous.value == 2
    assert changed_update.changeset is not None
    assert changed_update.changeset.key == "party_size"
    # Still true in this phase: a ChangeSet exists, but nothing computes an
    # invalidation or a recomputation plan from it yet - registering the
    # dependency graph in parallel changes nothing about this.
    assert changed_update.invalidation is None
    assert changed_update.plan is None


def test_fact_assertions_never_touch_the_graph():
    """assert_fact must have zero side effects on the dependency graph -
    the two subsystems are not wired together in this phase."""
    core = BackspaceCore()
    core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.assert_fact("party_size", 5, source="user", turn_id="t2")
    # No fact ever needed registration, and none was performed implicitly:
    # the graph has no WORK/CLAIM nodes and querying an arbitrary fact id
    # for dependents is simply empty, not populated by assert_fact.
    assert core.get_transitive_dependents("party_size") == []
    assert core.get_direct_dependents("party_size") == []
