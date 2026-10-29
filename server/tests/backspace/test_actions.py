"""Phase M1-C - stale-result guard + exactly-once state-changing action safety.

Pure ledger tests drive ``actions.py`` directly against hand-built
``WorkItem``s, the same style ``test_work_lifecycle.py`` uses for the
lifecycle it builds on. The fact-correction scenario tests exercise the
whole path end to end through the real ``BackspaceCore``/invalidation
engine, matching the canonical scenario the phase brief specifies.
"""

from __future__ import annotations

import inspect

import pytest

from app.backspace import (
    ActionCommitResult,
    BackspaceCore,
    DependencyKind,
    StaleExecutionError,
    WorkItem,
    WorkStatus,
    action_id_for,
    complete_work,
    invalidate_work,
    start_work,
)
from app.backspace import actions as actions_module

FTW = DependencyKind.FACT_TO_WORK
WTW = DependencyKind.WORK_TO_WORK


def _work(work_id: str = "W1", kind: str = "booking_action") -> WorkItem:
    return WorkItem(kind=kind, work_id=work_id)


# ===========================================================================
# 1/2. current accepted, stale rejected
# ===========================================================================


def test_1_current_execution_is_accepted():
    core = BackspaceCore()
    work = core.register_work(_work())
    attempt = start_work(work)

    result = core.commit_action(work.work_id, attempt, result={"confirmation": "BK1"})

    assert isinstance(result, ActionCommitResult)
    assert result.already_committed is False
    assert result.commit.result == {"confirmation": "BK1"}
    assert core.is_action_committed(action_id_for(work, attempt))


def test_2_stale_execution_is_rejected():
    core = BackspaceCore()
    work = core.register_work(_work())
    attempt = start_work(work)
    invalidate_work(work)  # goes stale before the tool ever reports back

    with pytest.raises(StaleExecutionError):
        core.commit_action(work.work_id, attempt, result="too late")
    assert core.get_action_commit(action_id_for(work, attempt)) is None


# ===========================================================================
# 3. the canonical fact-correction scenario
# ===========================================================================


def test_3_stale_result_arriving_after_a_fact_correction_is_discarded():
    core = BackspaceCore()
    f = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    work = core.register_work(WorkItem(kind="booking_action", work_id="BOOK1"))
    core.register_dependency(FTW, f.fact.fact_id, "BOOK1")

    old_attempt = start_work(work)
    assert work.status is WorkStatus.RUNNING

    # The user changes their mind mid-flight.
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)
    assert "BOOK1" in invalidation.invalidated_work_ids
    assert work.status is WorkStatus.STALE

    # The old tool call's result finally arrives - it must not be committed.
    with pytest.raises(StaleExecutionError):
        core.commit_action("BOOK1", old_attempt, result={"confirmation": "OLD-BOOKING"})
    assert core.get_action_commit(action_id_for(work, old_attempt)) is None

    # New work may now be planned/started with the corrected argument.
    new_attempt = start_work(work)
    assert new_attempt != old_attempt
    result = core.commit_action("BOOK1", new_attempt, result={"confirmation": "NEW-BOOKING", "party_size": 5})
    assert result.already_committed is False
    assert result.commit.result["party_size"] == 5
    # The old action was never committed as current.
    assert core.get_action_commit(action_id_for(work, old_attempt)) is None


# ===========================================================================
# 4/5. duplicate completion, current vs stale
# ===========================================================================


def test_4_duplicate_current_completion_is_idempotent():
    core = BackspaceCore()
    work = core.register_work(_work())
    attempt = start_work(work)

    first = core.commit_action(work.work_id, attempt, result="A")
    second = core.commit_action(work.work_id, attempt, result="B")  # retry, different payload

    assert first.already_committed is False
    assert second.already_committed is True
    assert second.commit is first.commit
    assert second.commit.result == "A"  # first commit's result is never overwritten
    assert len(core._actions.all_action_ids()) == 1


def test_5_duplicate_stale_completion_is_rejected_both_times():
    core = BackspaceCore()
    work = core.register_work(_work())
    attempt = start_work(work)
    invalidate_work(work)

    with pytest.raises(StaleExecutionError):
        core.commit_action(work.work_id, attempt, result="X")
    with pytest.raises(StaleExecutionError):
        core.commit_action(work.work_id, attempt, result="X")
    assert core._actions.all_action_ids() == []  # never recorded, either time


# ===========================================================================
# 6/7. independence across identities
# ===========================================================================


def test_6_different_action_ids_remain_independent():
    core = BackspaceCore()
    w1 = core.register_work(_work("W1"))
    w2 = core.register_work(_work("W2"))
    a1 = start_work(w1)
    a2 = start_work(w2)

    core.commit_action("W1", a1, result="one")
    invalidate_work(w2)
    with pytest.raises(StaleExecutionError):
        core.commit_action("W2", a2, result="two")

    assert core.get_action_commit(action_id_for(w1, a1)).result == "one"
    assert core.get_action_commit(action_id_for(w2, a2)) is None


def test_7_two_valid_sequential_actions_on_the_same_work_remain_independent():
    core = BackspaceCore()
    work = core.register_work(_work("W1"))

    a1 = start_work(work)
    complete_work(work, a1)
    r1 = core.commit_action("W1", a1, result="first booking")

    invalidate_work(work)
    a2 = start_work(work)
    complete_work(work, a2)
    r2 = core.commit_action("W1", a2, result="second booking")

    assert r1.commit.action_id != r2.commit.action_id
    assert core.get_action_commit(r1.commit.action_id).result == "first booking"
    assert core.get_action_commit(r2.commit.action_id).result == "second booking"


# ===========================================================================
# 8/9. safe retry, changed arguments produce a distinct action
# ===========================================================================


def test_8_same_action_retry_is_handled_safely():
    """A network-level retry redelivering the same attempt's completion must
    not produce a second commit."""
    core = BackspaceCore()
    work = core.register_work(_work())
    attempt = start_work(work)

    results = [core.commit_action(work.work_id, attempt, result="ok") for _ in range(5)]

    assert [r.already_committed for r in results] == [False, True, True, True, True]
    assert len(core._actions.all_action_ids()) == 1


def test_9_changed_arguments_create_a_distinct_current_action():
    """In this architecture, arguments only ever change via a fact change
    that invalidates the work item and forces a new execution attempt - so
    the new attempt's action_id is, by construction, distinct from the old
    one's."""
    work = _work()
    a1 = start_work(work)
    id_before = action_id_for(work, a1)
    invalidate_work(work)
    a2 = start_work(work)
    id_after = action_id_for(work, a2)

    assert id_before != id_after
    assert a2 != a1


# ===========================================================================
# 10. chained actions preserve identity
# ===========================================================================


def test_10_chained_actions_preserve_identity_through_invalidation():
    core = BackspaceCore()
    core.register_work(WorkItem(kind="hotel_search", work_id="W1"))
    core.register_work(WorkItem(kind="booking_action", work_id="W3"))
    core.register_dependency(WTW, "W1", "W3")
    f = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    core.register_dependency(FTW, f.fact.fact_id, "W1")

    w1 = core.graph.get_work("W1")
    w3 = core.graph.get_work("W3")
    a1 = start_work(w1)
    complete_work(w1, a1)
    core.commit_action("W1", a1, result="hotel booked")
    a3 = start_work(w3)  # chained off W1

    change = core.assert_fact("destination", "Kerala", source="user", turn_id="t2")
    core.invalidate(change.changeset)

    assert w1.status is WorkStatus.STALE
    assert w3.status is WorkStatus.STALE
    # W1's earlier legitimate commit is untouched by the later invalidation.
    assert core.get_action_commit(action_id_for(w1, a1)).result == "hotel booked"
    # W3's in-flight (now stale) attempt must not be committed.
    with pytest.raises(StaleExecutionError):
        core.commit_action("W3", a3, result="booking made on old destination")


# ===========================================================================
# 11/12. session isolation, reset
# ===========================================================================


def test_11_session_isolation_of_action_state():
    core_a = BackspaceCore()
    core_b = BackspaceCore()
    core_a.register_work(_work("W1"))
    core_b.register_work(_work("W1"))

    a1 = start_work(core_a.graph.get_work("W1"))
    core_a.commit_action("W1", a1, result="only in A")

    assert core_a.get_action_commit(action_id_for(core_a.graph.get_work("W1"), a1)) is not None
    assert core_b._actions.all_action_ids() == []


def test_12_reset_clears_action_state():
    core = BackspaceCore()
    work = core.register_work(_work("W1"))
    attempt = start_work(work)
    action_id = action_id_for(work, attempt)
    core.commit_action("W1", attempt, result="booked")
    assert core.is_action_committed(action_id)

    core.reset()

    assert core.is_action_committed(action_id) is False
    assert core.get_action_commit(action_id) is None
    assert core._actions.all_action_ids() == []


# ===========================================================================
# 13/14. no natural-language matching, no benchmark-specific logic
# ===========================================================================


def test_13_no_natural_language_matching():
    """The guard's outcome must be identical regardless of what work.kind
    (or the committed result) actually says - it never branches on text."""
    core = BackspaceCore()
    for kind in ("booking_action", "a completely different phrase", "🎉", ""):
        work = core.register_work(WorkItem(kind=kind, work_id=f"W-{kind!r}"))
        attempt = start_work(work)
        result = core.commit_action(work.work_id, attempt, result="anything, including free text")
        assert result.already_committed is False

    # Structural guard: commit()/action_id_for() take only WorkItem/int/Any,
    # never a string parsed for meaning.
    for name in ("action_id_for",):
        func = getattr(actions_module, name)
        params = list(inspect.signature(func).parameters)
        assert params == ["work", "attempt"]
    commit_params = list(inspect.signature(actions_module.ActionLedger.commit).parameters)
    assert commit_params == ["self", "work", "attempt", "result"]


def test_14_no_benchmark_specific_logic():
    """No scenario id, FDB identifier, or hardcoded allowlist gates
    acceptance - only structured execution state (attempt/status)."""
    core = BackspaceCore()
    work = core.register_work(WorkItem(kind="totally_unseen_scenario_xyz", work_id="W1"))
    attempt = start_work(work)
    # An id that looks nothing like anything this module was "trained" on
    # is accepted purely because the execution state says it is current.
    result = core.commit_action("W1", attempt, result={"anything": "goes"})
    assert result.already_committed is False

    import ast
    from pathlib import Path

    source = Path(actions_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Collect every docstring node (module/class/function) so it can be
    # excluded below - this checks the module's actual *logic* for a
    # benchmark/scenario literal, not its own prose explaining why there
    # isn't one (which necessarily has to say those words).
    docstrings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)

    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    } - docstrings
    # No non-docstring literal in the module looks like a benchmark/scenario
    # identifier - i.e. nothing in the module's actual logic gates on one.
    assert not any("fdb" in s.lower() or "scenario" in s.lower() for s in literals)


# ===========================================================================
# 15. everything else still passes (smoke check within this file)
# ===========================================================================


def test_15_action_commit_result_and_commit_shape():
    core = BackspaceCore()
    work = core.register_work(_work())
    attempt = start_work(work)
    result = core.commit_action(work.work_id, attempt, result={"k": "v"})

    d = result.commit.to_dict()
    assert d["action_id"] == action_id_for(work, attempt)
    assert d["work_id"] == work.work_id
    assert d["attempt"] == attempt
    assert d["result"] == {"k": "v"}
    assert "committed_at" in d


def test_commit_action_raises_for_unregistered_work_id():
    from app.backspace import NodeNotRegisteredError

    core = BackspaceCore()
    with pytest.raises(NodeNotRegisteredError):
        core.commit_action("nonexistent", 1)


def test_snapshot_includes_committed_actions():
    core = BackspaceCore()
    work = core.register_work(_work("W1"))
    attempt = start_work(work)
    core.commit_action("W1", attempt, result="booked")

    snap = core.snapshot()
    assert action_id_for(work, attempt) in snap["actions"]
    assert snap["actions"][action_id_for(work, attempt)]["result"] == "booked"
