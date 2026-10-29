"""M1-D3 — synthetic stale-result / duplicate-commit regression.

This is a BACKSPACE Core safety/extension test, not an FDB-v3 benchmark
behavior. FDB-v3's own reference implementation never exercises this race
(see the M1-D audit: its mock tool calls execute synchronously, back-to-back,
inside one blocking call - there is no window in which staleness could
actually occur there today). This file proves the race BACKSPACE's own
M1-B (work_lifecycle.py) / M1-C (actions.py) machinery is *built* to guard
against, using nothing but existing, unmodified BackspaceCore/WorkItem/
ActionLedger APIs - no BACKSPACE Core file changes with this phase.

Everything here is deterministic and sequential: "before attempt 1
completes" means exactly that in program order (invalidate is called before
attempt 1's completion call), never a sleep or a thread race.
"""

from __future__ import annotations

import pytest

from app.backspace import (
    BackspaceCore,
    DependencyKind,
    StaleExecutionError,
    WorkItem,
    WorkStatus,
    action_id_for,
    cancel_work,
    complete_work,
    fail_work,
    is_result_current,
    start_work,
)

FTW = DependencyKind.FACT_TO_WORK


# ===========================================================================
# The required deterministic end-to-end scenario (steps 1-8 of the brief)
# ===========================================================================


def test_stale_attempt_rejected_current_attempt_accepted_once():
    core = BackspaceCore()

    # 1. A WorkItem representing a tool execution, wired to the fact that
    #    will change (so BACKSPACE's own invalidation engine - not this test -
    #    is what decides the WorkItem goes stale).
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    work = core.register_work(WorkItem(kind="booking_action", work_id="BOOK1"))
    core.register_dependency(FTW, f.fact.fact_id, "BOOK1")

    # 2. start_work(work) -> attempt 1.
    attempt_1 = start_work(work)
    assert attempt_1 == 1
    assert work.status is WorkStatus.RUNNING

    # 3. Before attempt 1 completes, a fact change invalidates the WorkItem -
    #    via the existing assert_fact -> invalidate pipeline, no sleeps.
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)
    assert "BOOK1" in invalidation.invalidated_work_ids
    assert work.status is WorkStatus.STALE

    # 4. Start a replacement execution -> attempt 2.
    attempt_2 = start_work(work)
    assert attempt_2 == 2
    assert work.status is WorkStatus.RUNNING

    # 5. A synthetic late result from attempt 1 arrives. It must be rejected
    #    at every layer it could reach, and must not corrupt current state.
    assert is_result_current(work, attempt_1) is False

    with pytest.raises(StaleExecutionError) as excinfo:
        complete_work(work, attempt_1, output={"confirmation": "OLD-BOOKING"})
    assert excinfo.value.attempt == attempt_1
    assert excinfo.value.current_attempt == attempt_2
    # complete_work raised before touching status/output - attempt 2 is still
    # cleanly RUNNING, not silently reverted or corrupted.
    assert work.status is WorkStatus.RUNNING
    assert work.output is None

    with pytest.raises(StaleExecutionError):
        core.commit_action("BOOK1", attempt_1, result={"confirmation": "OLD-BOOKING"})
    # The rejected attempt never entered the ActionLedger.
    stale_action_id = action_id_for(work, attempt_1)
    assert core.get_action_commit(stale_action_id) is None
    assert core.is_action_committed(stale_action_id) is False

    # 6. Attempt 2's result is delivered and must be accepted.
    new_result = {"confirmation": "NEW-BOOKING", "party_size": 5}
    complete_work(work, attempt_2, output=new_result)
    assert work.status is WorkStatus.VALID
    assert work.output == new_result

    commit_result = core.commit_action("BOOK1", attempt_2, result=new_result)
    assert commit_result.already_committed is False
    assert commit_result.commit.result == new_result

    # 7. Committing attempt 2 again must be recognised as a duplicate, not a
    #    second logical commit - and must not adopt a different payload.
    duplicate = core.commit_action("BOOK1", attempt_2, result={"confirmation": "SHOULD-BE-IGNORED"})
    assert duplicate.already_committed is True
    assert duplicate.commit is commit_result.commit
    assert duplicate.commit.result == new_result  # first commit's payload, unchanged

    # 8. Final state.
    assert is_result_current(work, attempt_1) is False           # attempt 1 can never be accepted
    assert is_result_current(work, attempt_2) is True             # attempt 2 is the current execution
    action_ids = core._actions.all_action_ids()
    assert len(action_ids) == 1                                   # exactly one ActionLedger entry
    assert action_ids[0] == "BOOK1:2"                              # committed identity is work_id:2
    assert action_ids[0] == action_id_for(work, attempt_2)
    assert work.output == new_result                               # stale result never overwrote it
    assert core.get_action_commit(stale_action_id) is None        # attempt 1's identity still absent


# ===========================================================================
# Direct primitive-level cases (requested in addition to the end-to-end scenario)
# ===========================================================================


def test_primitive_stale_attempt_rejected_by_is_result_current_complete_work_and_commit_action():
    core = BackspaceCore()
    work = core.register_work(WorkItem(kind="generic", work_id="W1"))

    old_attempt = start_work(work)
    # Directly invalidate (no fact/graph wiring needed for this narrower check)
    from app.backspace import invalidate_work
    invalidate_work(work)
    new_attempt = start_work(work)

    assert is_result_current(work, old_attempt) is False
    with pytest.raises(StaleExecutionError):
        complete_work(work, old_attempt, output="stale")
    with pytest.raises(StaleExecutionError):
        core.commit_action("W1", old_attempt, result="stale")
    assert core.get_action_commit(action_id_for(work, old_attempt)) is None
    assert new_attempt != old_attempt


def test_primitive_current_attempt_is_accepted():
    core = BackspaceCore()
    work = core.register_work(WorkItem(kind="generic", work_id="W1"))
    attempt = start_work(work)

    assert is_result_current(work, attempt) is True
    complete_work(work, attempt, output="ok")
    assert is_result_current(work, attempt) is True  # still current once VALID

    result = core.commit_action("W1", attempt, result="ok")
    assert result.already_committed is False
    assert result.commit.result == "ok"


def test_primitive_duplicate_current_action_identity_is_idempotently_recognized():
    core = BackspaceCore()
    work = core.register_work(WorkItem(kind="generic", work_id="W1"))
    attempt = start_work(work)
    complete_work(work, attempt, output="ok")

    first = core.commit_action("W1", attempt, result="ok")
    second = core.commit_action("W1", attempt, result="ok")
    third = core.commit_action("W1", attempt, result="a different payload entirely")

    assert first.already_committed is False
    assert second.already_committed is True
    assert third.already_committed is True
    assert second.commit is first.commit is third.commit
    assert third.commit.result == "ok"  # never replaced by the later call's payload
    assert len(core._actions.all_action_ids()) == 1  # no second logical commit was ever created


def test_primitive_failed_execution_cannot_be_committed():
    core = BackspaceCore()
    work = core.register_work(WorkItem(kind="generic", work_id="W1"))
    attempt = start_work(work)
    fail_work(work, attempt)

    assert is_result_current(work, attempt) is False
    with pytest.raises(StaleExecutionError):
        core.commit_action("W1", attempt, result="should not commit")
    assert core._actions.all_action_ids() == []


def test_primitive_cancelled_execution_cannot_be_committed():
    core = BackspaceCore()
    work = core.register_work(WorkItem(kind="generic", work_id="W1"))
    attempt = start_work(work)
    cancel_work(work)

    assert is_result_current(work, attempt) is False
    with pytest.raises(StaleExecutionError):
        core.commit_action("W1", attempt, result="should not commit")
    assert core._actions.all_action_ids() == []


def test_primitive_cancelled_before_ever_starting_cannot_be_committed():
    """A WorkItem cancelled straight from PENDING (attempt 0, never started)
    must still be un-committable - is_result_current's status check, not
    just its attempt-number check, is what has to catch this."""
    core = BackspaceCore()
    work = core.register_work(WorkItem(kind="generic", work_id="W1"))
    cancel_work(work)

    assert work.execution_attempt == 0
    assert is_result_current(work, 0) is False
    with pytest.raises(StaleExecutionError):
        core.commit_action("W1", 0, result="should not commit")
    assert core._actions.all_action_ids() == []
