"""Phase M1-B - the in-flight work execution lifecycle.

Pure state-machine tests drive ``work_lifecycle.py``'s functions directly
against a hand-built ``WorkItem``/``DependencyGraph``, the same style every
other engine module in this package is tested. The invalidation-integration
tests confirm the lifecycle actually gets used from ``invalidate()`` (Phase
5) without changing which work is affected - the dependency graph remains
the sole source of truth for *that* question; this phase only changes what
happens to a work item once it is deemed affected.
"""

from __future__ import annotations

import pytest

from app.backspace import (
    BackspaceCore,
    DependencyGraph,
    DependencyKind,
    IllegalWorkTransitionError,
    StaleExecutionError,
    WorkItem,
    WorkStatus,
    cancel_work,
    complete_work,
    fail_work,
    invalidate_work,
    is_result_current,
    start_work,
)

FTW = DependencyKind.FACT_TO_WORK
WTW = DependencyKind.WORK_TO_WORK


def _work(work_id: str = "W1", kind: str = "generic", status: WorkStatus = WorkStatus.PENDING) -> WorkItem:
    return WorkItem(kind=kind, work_id=work_id, status=status)


# ===========================================================================
# 1-5. the basic legal transitions
# ===========================================================================


def test_1_pending_to_running():
    work = _work()
    attempt = start_work(work)
    assert work.status is WorkStatus.RUNNING
    assert attempt == 1
    assert work.execution_attempt == 1


def test_2_running_to_completed():
    work = _work()
    attempt = start_work(work)
    complete_work(work, attempt, output={"quote_inr": 18000})
    assert work.status is WorkStatus.VALID
    assert work.output == {"quote_inr": 18000}


def test_3_running_to_failed():
    work = _work()
    attempt = start_work(work)
    fail_work(work, attempt)
    assert work.status is WorkStatus.FAILED


def test_4_running_to_stale():
    work = _work()
    start_work(work)
    invalidate_work(work)
    assert work.status is WorkStatus.STALE


def test_5_pending_to_stale():
    work = _work()  # never started
    invalidate_work(work)
    assert work.status is WorkStatus.STALE


def test_valid_to_stale_existing_invalidation_semantics_preserved():
    work = _work()
    attempt = start_work(work)
    complete_work(work, attempt)
    invalidate_work(work)
    assert work.status is WorkStatus.STALE


def test_pending_to_cancelled_and_running_to_cancelled():
    a = _work("A")
    cancel_work(a)
    assert a.status is WorkStatus.CANCELLED

    b = _work("B")
    start_work(b)
    cancel_work(b)
    assert b.status is WorkStatus.CANCELLED


def test_stale_to_running_recomputation_is_possible():
    """Without this transition a stale item could never actually be
    recomputed - the entire point of the Phase 6 planner."""
    work = _work()
    invalidate_work(work)
    attempt = start_work(work)
    assert work.status is WorkStatus.RUNNING
    assert attempt == 1


# ===========================================================================
# 6/7. result acceptance guard
# ===========================================================================


def test_6_stale_result_is_rejected():
    work = _work()
    attempt = start_work(work)
    invalidate_work(work)  # goes stale while "in flight"
    assert is_result_current(work, attempt) is False


def test_7_current_result_is_accepted():
    work = _work()
    attempt = start_work(work)
    assert is_result_current(work, attempt) is True
    complete_work(work, attempt)
    assert is_result_current(work, attempt) is True  # still current once VALID


def test_result_acceptance_never_inspects_text_or_names():
    """The guard is purely structural - it takes a WorkItem and an int,
    nothing text- or benchmark-shaped."""
    work = WorkItem(kind="anything at all, even a fake scenario name", work_id="W1")
    attempt = start_work(work)
    assert is_result_current(work, attempt) is True
    assert is_result_current(work, 999) is False


# ===========================================================================
# 8/9. completed work and duplicate completion
# ===========================================================================


def test_8_completed_work_cannot_accept_an_old_executions_result():
    work = _work()
    old_attempt = start_work(work)
    invalidate_work(work)
    new_attempt = start_work(work)
    complete_work(work, new_attempt, output="current answer")

    assert work.status is WorkStatus.VALID
    assert is_result_current(work, old_attempt) is False
    with pytest.raises(StaleExecutionError) as excinfo:
        complete_work(work, old_attempt, output="stale answer")
    assert excinfo.value.attempt == old_attempt
    assert excinfo.value.current_attempt == new_attempt
    # The stale completion attempt must not have corrupted the real result.
    assert work.output == "current answer"


def test_9_duplicate_completion_of_the_current_attempt_is_safe():
    work = _work()
    attempt = start_work(work)
    complete_work(work, attempt, output="first")
    complete_work(work, attempt, output="first")  # identical repeat
    assert work.status is WorkStatus.VALID
    assert work.output == "first"


def test_duplicate_completion_does_not_bump_the_attempt_counter():
    work = _work()
    attempt = start_work(work)
    complete_work(work, attempt)
    complete_work(work, attempt)
    assert work.execution_attempt == attempt  # no phantom extra attempts


# ===========================================================================
# 10. invalid transitions fail cleanly
# ===========================================================================


def test_10_completed_work_item_cannot_silently_start_a_new_execution():
    work = _work()
    attempt = start_work(work)
    complete_work(work, attempt)
    with pytest.raises(IllegalWorkTransitionError) as excinfo:
        start_work(work)
    assert excinfo.value.current is WorkStatus.VALID
    assert excinfo.value.target is WorkStatus.RUNNING
    assert work.status is WorkStatus.VALID  # unchanged by the rejected attempt


def test_10_cancelled_and_failed_are_terminal_in_this_phase():
    cancelled = _work("A")
    cancel_work(cancelled)
    with pytest.raises(IllegalWorkTransitionError):
        start_work(cancelled)

    failed = _work("B")
    start_work(failed)
    fail_work(failed, 1)
    with pytest.raises(IllegalWorkTransitionError):
        start_work(failed)


def test_10_pending_cannot_jump_straight_to_completed():
    work = _work()
    with pytest.raises(IllegalWorkTransitionError):
        complete_work(work, 0)


def test_10_cannot_fail_or_complete_work_that_never_started():
    work = _work()
    with pytest.raises(StaleExecutionError):
        complete_work(work, 1)
    with pytest.raises(StaleExecutionError):
        fail_work(work, 1)


def test_10_illegal_transition_does_not_mutate_state():
    work = _work()
    attempt = start_work(work)
    complete_work(work, attempt)
    before_output = work.output
    try:
        start_work(work)
    except IllegalWorkTransitionError:
        pass
    assert work.status is WorkStatus.VALID
    assert work.output == before_output
    assert work.execution_attempt == attempt  # not bumped by the rejected call


# ===========================================================================
# 11/12/13. invalidation integration - graph remains the source of truth
# ===========================================================================


def test_11_dependency_invalidation_marks_in_flight_running_work_stale():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W2")

    work = core.graph.get_work("W2")
    attempt = start_work(work)  # simulate the tool call actually running
    assert work.status is WorkStatus.RUNNING

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)

    assert "W2" in invalidation.invalidated_work_ids
    assert work.status is WorkStatus.STALE
    # The in-flight attempt's eventual result must now be rejected.
    assert is_result_current(work, attempt) is False


def test_12_unaffected_work_remains_current_through_invalidation():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="hotel_search", work_id="W1"))
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    f_dest = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    f_size = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    core.register_dependency(FTW, f_dest.fact.fact_id, "W1")
    core.register_dependency(FTW, f_size.fact.fact_id, "W2")

    w1 = core.graph.get_work("W1")
    attempt = start_work(w1)
    complete_work(w1, attempt, output="hotels found")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)

    assert w1.status is WorkStatus.VALID  # untouched - no dependency on party_size
    assert is_result_current(w1, attempt) is True
    assert w1.output == "hotels found"


def test_13_chained_work_preserves_dependency_semantics_through_execution():
    """W1 -> W3 (chained). Invalidating F (which only W1 depends on
    directly) must still transitively stale W3, exactly as Phase 5
    established - the execution lifecycle changes *what happens* to affected
    work, never *which* work is affected."""
    core = BackspaceCore()
    core.register_work(WorkItem(kind="hotel_search", work_id="W1"))
    core.register_work(WorkItem(kind="recommendation", work_id="W3"))
    core.register_dependency(WTW, "W1", "W3")
    f = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W1")

    w1 = core.graph.get_work("W1")
    w3 = core.graph.get_work("W3")
    a1 = start_work(w1)
    complete_work(w1, a1)
    a3 = start_work(w3)  # W3 running, chained off W1's completion

    change = core.assert_fact("destination", "Kerala", source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)

    assert set(invalidation.invalidated_work_ids) == {"W1", "W3"}
    assert w1.status is WorkStatus.STALE
    assert w3.status is WorkStatus.STALE
    assert is_result_current(w3, a3) is False


# ===========================================================================
# 14. reset clears execution state
# ===========================================================================


def test_14_reset_clears_execution_state():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    work = core.graph.get_work("W2")
    attempt = start_work(work)
    complete_work(work, attempt)
    assert work.execution_attempt == 1

    core.reset()

    assert core.graph.get_work("W2") is None
    # A freshly re-registered work item under the same id starts clean -
    # no leftover attempt counter or status survives the reset.
    fresh = core.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    assert fresh.status is WorkStatus.PENDING
    assert fresh.execution_attempt == 0


# ===========================================================================
# 15. session isolation
# ===========================================================================


def test_15_session_isolation_of_execution_state():
    core_a = BackspaceCore()
    core_b = BackspaceCore()
    core_a.register_work(WorkItem(kind="price_calculation", work_id="W2"))
    core_b.register_work(WorkItem(kind="price_calculation", work_id="W2"))

    work_a = core_a.graph.get_work("W2")
    work_b = core_b.graph.get_work("W2")

    attempt_a = start_work(work_a)
    complete_work(work_a, attempt_a)
    fail_work(work_b, start_work(work_b))

    assert work_a.status is WorkStatus.VALID
    assert work_b.status is WorkStatus.FAILED
    assert work_a.execution_attempt == 1
    assert work_b.execution_attempt == 1
    assert work_a is not work_b


def test_15_session_isolation_via_graph_reset_only_affects_its_own_core():
    core_a = BackspaceCore()
    core_b = BackspaceCore()
    core_a.register_work(WorkItem(kind="x", work_id="W1"))
    core_b.register_work(WorkItem(kind="x", work_id="W1"))
    start_work(core_a.graph.get_work("W1"))
    start_work(core_b.graph.get_work("W1"))

    core_a.reset()

    assert core_a.graph.get_work("W1") is None
    assert core_b.graph.get_work("W1").status is WorkStatus.RUNNING


# ===========================================================================
# no false generic-ness leaks: WorkItem never references a benchmark concept
# ===========================================================================


def test_work_lifecycle_module_takes_no_benchmark_shaped_arguments():
    """Structural guard: every public function here takes only a WorkItem
    and, where relevant, a plain int attempt/optional output - never a
    scenario id, tool name allowlist, or transcript."""
    import inspect

    from app.backspace import work_lifecycle

    for name in ("start_work", "complete_work", "fail_work", "cancel_work",
                 "invalidate_work", "is_result_current"):
        func = getattr(work_lifecycle, name)
        params = list(inspect.signature(func).parameters)
        assert params[0] == "work"
        assert all(p in ("work", "attempt", "output") for p in params)
