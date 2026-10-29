"""Exactly-once (at-most-once) commit guard for state-changing actions (Phase M1-C).

The updated Theme 05 guide's requirement, restated precisely: when the user
changes their mind mid-flight, (1) the stale execution must be discarded,
(2) a corrected execution must be plannable with the new arguments, and (3)
the underlying state-changing effect (a booking, a purchase, any tool call
with a real-world side effect) must never be committed twice for the same
logical action. This module is the domain-level guard the execution layer
consults to get that guarantee - it does not execute anything itself.

Identity design (the "inspect first, use the smallest design" step this
phase asks for): an action's identity is exactly the pair
``(work.work_id, attempt)`` - the same ``work_id``/``execution_attempt``
Phase M1-B (``work_lifecycle.py``) already introduced, formatted as one
string by ``action_id_for``. Nothing new is added to ``WorkItem``. This is
deliberate, not an oversight of the "action type"/"arguments"/"version"
possibilities the brief lists: in this architecture a *new* execution
attempt is only ever started (``work_lifecycle.start_work``) after the
previous one has gone ``STALE``, and the only thing that makes a WORK node
go stale is a fact it depends on changing (``invalidation.invalidate_many``)
- i.e. its effective arguments changing. So ``execution_attempt`` already *is*
a monotonic version number over "the arguments this work item is currently
running with"; hashing the arguments separately would be a second versioning
system tracking the same fact this one already tracks, which is exactly the
duplication the brief asks this phase to avoid. A raw transcript, a
benchmark scenario id, or a tool name never enters the identity - only two
integers/strings already produced by structured execution state.

Guarantee, stated precisely because the brief asks for that: this is
**at-most-once acceptance of a given action identity, for the lifetime of
one ``ActionLedger`` (one session's ``BackspaceCore``)**. It is not
distributed-systems exactly-once - there is no persistence across a process
restart, no cross-session deduplication, and no network-level idempotency
key exchanged with an external system. Within an in-process, session-scoped
agent turn loop, that is the whole problem this phase was asked to solve.

Layering: ``work_lifecycle.is_result_current`` is reused, not reimplemented,
as the eligibility check - the same "attempt matches AND status is
RUNNING/VALID" rule that guards ``complete_work``/``fail_work`` guards
``ActionLedger.commit`` too, via the same ``StaleExecutionError``. That
keeps exactly one place (``work_lifecycle.py``) deciding what "current"
means; this module only adds "and have I already committed this one?" on
top of that existing decision.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .graph import WorkItem
from .work_lifecycle import StaleExecutionError, is_result_current


def action_id_for(work: WorkItem, attempt: int) -> str:
    """The identity of the state-changing action performed by execution
    ``attempt`` of ``work``.

    Purely structural: ``f"{work_id}:{attempt}"``. Two calls for the same
    ``(work_id, attempt)`` always produce the same id; starting a new attempt
    (``work_lifecycle.start_work``, which only happens after this work item
    went ``STALE``) always produces a different one. Never reads
    ``work.kind``, ``work.output``, or anything text-shaped - the id carries
    no information about what the action *is*, only which execution attempt
    of which work item performed it.
    """
    return f"{work.work_id}:{attempt}"


@dataclass
class ActionCommit:
    """The permanent record of one action identity's (first and only) commit."""

    action_id: str
    work_id: str
    attempt: int
    result: Any = None
    committed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "work_id": self.work_id,
            "attempt": self.attempt,
            "result": self.result,
            "committed_at": self.committed_at,
        }


@dataclass
class ActionCommitResult:
    """What ``ActionLedger.commit`` returns.

    ``already_committed`` is ``False`` exactly once per action identity - the
    call that actually performed the commit - and ``True`` for every
    subsequent call with the same identity, which is how a caller tells "I
    should now perform the real-world effect" apart from "this was already
    done, do not repeat it" without needing a separate query first.
    """

    commit: ActionCommit
    already_committed: bool


class ActionLedger:
    """Session-scoped, in-process at-most-once commit guard.

    Owns exactly one mapping: ``action_id -> ActionCommit``. Nothing here
    inspects ``work.kind``, a tool name, an argument's text, or a benchmark
    identifier - every decision is made from ``work.execution_attempt``/
    ``work.status`` (via ``is_result_current``) and the id already computed
    from them. Two ``ActionLedger``s never share a commit - the same
    session-isolation discipline ``DependencyGraph``/``FactNotebook`` already
    follow.
    """

    def __init__(self) -> None:
        self._commits: dict[str, ActionCommit] = {}

    def commit(self, work: WorkItem, attempt: int, result: Any = None) -> ActionCommitResult:
        """Attempt to commit the state-changing action for this ``attempt``
        of ``work``.

        Raises ``StaleExecutionError`` (from ``work_lifecycle.py`` - not a
        second, parallel exception type) if ``attempt`` is not current per
        ``is_result_current`` - i.e. either an old attempt number, or an
        attempt whose work item is no longer ``RUNNING``/``VALID`` (already
        gone ``STALE``/``CANCELLED``/``FAILED``/``INVALIDATED``). This is
        checked, and raises, *before* anything is recorded - a rejected call
        never enters the ledger, so a stale result can never masquerade as a
        commit later.

        Idempotent for a still-current identity: a second (or Nth) call with
        the same ``(work.work_id, attempt)`` does not create a second commit
        and does not overwrite the first call's ``result`` - it returns the
        original ``ActionCommit`` with ``already_committed=True``. This is
        what makes a redelivered/retried completion safe: the real-world
        effect it represents is recorded at most once no matter how many
        times the same attempt reports success.
        """
        if not is_result_current(work, attempt):
            raise StaleExecutionError(work.work_id, attempt, work.execution_attempt)

        action_id = action_id_for(work, attempt)
        existing = self._commits.get(action_id)
        if existing is not None:
            return ActionCommitResult(commit=existing, already_committed=True)

        commit = ActionCommit(action_id=action_id, work_id=work.work_id, attempt=attempt, result=result)
        self._commits[action_id] = commit
        return ActionCommitResult(commit=commit, already_committed=False)

    def get(self, action_id: str) -> ActionCommit | None:
        return self._commits.get(action_id)

    def is_committed(self, action_id: str) -> bool:
        return action_id in self._commits

    def all_action_ids(self) -> list[str]:
        """Every committed action id, in commit order."""
        return list(self._commits.keys())

    def reset(self) -> None:
        """Clear this ledger's commit history. Other instances are untouched."""
        self._commits.clear()
