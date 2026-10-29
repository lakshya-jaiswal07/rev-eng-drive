"""The in-flight work execution lifecycle (Phase M1-B).

A generic, benchmark-agnostic state machine for what a ``WorkItem`` is
*doing* right now - pending, running, completed, stale, cancelled, failed -
layered on top of the fact-change validity states Phase 2-6 already gave it
(``PENDING``/``STALE``/``INVALIDATED``/``RETRACTED``/``RECOMPUTED``). Nothing
here knows what kind of work a ``WorkItem`` represents, what tool produced
it, or what benchmark (if any) is driving it - every function takes a plain
``WorkItem`` and an attempt number, nothing shaped like a scenario id, a
transcript, or a tool name.

Synchronous and side-effect-free beyond mutating the ``WorkItem`` object it
is handed: no ``asyncio``, no task cancellation, no I/O. Per the brief,
BACKSPACE only *records* that work is no longer current - actually tearing
down whatever coroutine is executing it is the runtime/tool layer's job,
informed by reading ``WorkItem.status``/``execution_attempt`` (or calling
``is_result_current``), never the other way around.

The critical invariant every function here exists to protect: **a result
produced by an old, superseded execution attempt must never be accepted as
current.** ``execution_attempt`` (on ``WorkItem``, Phase M1-B) is what makes
that checkable - a work_id alone survives a restart, so status alone cannot
tell "attempt 1's late result" apart from "attempt 2's real result" once a
stale item has been restarted. Comparing the attempt number is what can.
"""

from __future__ import annotations

from typing import Any

from .graph import WorkItem, WorkStatus


class IllegalWorkTransitionError(Exception):
    """Raised when a requested status change is not a legal transition for
    the work item's current status - e.g. a completed (``VALID``) item
    cannot silently start running again without first going through
    ``STALE``, and a terminal ``CANCELLED``/``FAILED`` item has no legal way
    out in this phase."""

    def __init__(self, work_id: str, current: WorkStatus, target: WorkStatus) -> None:
        super().__init__(
            f"Work {work_id!r} cannot move from {current.value!r} to {target.value!r}."
        )
        self.work_id = work_id
        self.current = current
        self.target = target


class StaleExecutionError(Exception):
    """Raised when ``complete_work``/``fail_work`` is called with an attempt
    number that is not the work item's current one - i.e. an old, superseded
    execution trying to report a result. This is the enforcement side of the
    module's central invariant; ``is_result_current`` is the advisory side a
    caller can check first."""

    def __init__(self, work_id: str, attempt: int, current_attempt: int) -> None:
        super().__init__(
            f"Work {work_id!r} attempt {attempt} is not current "
            f"(current attempt is {current_attempt}); its result cannot be accepted."
        )
        self.work_id = work_id
        self.attempt = attempt
        self.current_attempt = current_attempt


# The legal-transition table. Deliberately narrow: only transitions the
# brief explicitly calls for, or that existing semantics require (STALE ->
# RUNNING, without which a stale item could never actually be recomputed -
# the entire point of the Phase 6 planner). CANCELLED/FAILED have no legal
# outgoing transition in this phase - retry semantics, if wanted, are a
# later phase's decision, not invented here. INVALIDATED/RETRACTED/
# RECOMPUTED are untouched, pre-existing values this phase does not assign
# or transition through; they are absent from this table on purpose.
_LEGAL_TRANSITIONS: dict[WorkStatus, frozenset[WorkStatus]] = {
    WorkStatus.PENDING: frozenset({WorkStatus.RUNNING, WorkStatus.CANCELLED, WorkStatus.STALE}),
    WorkStatus.RUNNING: frozenset(
        {WorkStatus.VALID, WorkStatus.FAILED, WorkStatus.CANCELLED, WorkStatus.STALE}
    ),
    WorkStatus.VALID: frozenset({WorkStatus.STALE}),
    WorkStatus.STALE: frozenset({WorkStatus.RUNNING}),
    WorkStatus.CANCELLED: frozenset(),
    WorkStatus.FAILED: frozenset(),
}

# Statuses under which a result can ever be considered current - anything
# else (STALE, CANCELLED, FAILED, and the untouched INVALIDATED/RETRACTED)
# means "no, this attempt's output is not to be trusted."
_ACCEPTABLE_RESULT_STATUSES = frozenset({WorkStatus.RUNNING, WorkStatus.VALID})

# Statuses invalidation may move a work item out of, into STALE. Deliberately
# excludes CANCELLED/FAILED (already terminal - nothing to make stale) and
# leaves STALE itself in, so re-invalidating an already-stale item is a safe,
# idempotent no-op rather than an error.
_INVALIDATABLE_STATUSES = frozenset(
    {WorkStatus.PENDING, WorkStatus.RUNNING, WorkStatus.VALID, WorkStatus.STALE}
)


def _transition(work: WorkItem, target: WorkStatus) -> None:
    allowed = _LEGAL_TRANSITIONS.get(work.status, frozenset())
    if target not in allowed:
        raise IllegalWorkTransitionError(work.work_id, work.status, target)
    work.status = target


def start_work(work: WorkItem) -> int:
    """Begin a new execution attempt for ``work``.

    Legal from ``PENDING`` or ``STALE`` only (``STALE`` is what makes
    recomputation of previously-invalidated work possible at all). Increments
    and returns ``execution_attempt`` - the caller must keep this number and
    pass it back to ``complete_work``/``fail_work`` for this attempt.
    """
    _transition(work, WorkStatus.RUNNING)
    work.execution_attempt += 1
    return work.execution_attempt


def complete_work(work: WorkItem, attempt: int, output: Any = None) -> None:
    """Record that execution ``attempt`` of ``work`` finished successfully.

    Raises ``StaleExecutionError`` if ``attempt`` is not the work item's
    current attempt (an old execution reporting in after being superseded)
    and ``IllegalWorkTransitionError`` if the work item is not ``RUNNING``.
    Calling this twice with the same, still-current ``attempt`` is safe and
    idempotent (``VALID`` -> ``VALID`` is a no-op reassignment, not a
    transition ``_LEGAL_TRANSITIONS`` needs to separately allow).
    """
    if work.status is WorkStatus.VALID and attempt == work.execution_attempt:
        if output is not None:
            work.output = output
        return  # duplicate completion of the same attempt - safe, no-op
    if attempt != work.execution_attempt:
        raise StaleExecutionError(work.work_id, attempt, work.execution_attempt)
    _transition(work, WorkStatus.VALID)
    if output is not None:
        work.output = output


def fail_work(work: WorkItem, attempt: int) -> None:
    """Record that execution ``attempt`` of ``work`` failed.

    Same attempt-currency guard as ``complete_work``: a stale attempt cannot
    fail its way into changing a work item's current state either.
    """
    if attempt != work.execution_attempt:
        raise StaleExecutionError(work.work_id, attempt, work.execution_attempt)
    _transition(work, WorkStatus.FAILED)


def cancel_work(work: WorkItem) -> None:
    """Mark ``work`` cancelled/discarded - legal from ``PENDING`` or
    ``RUNNING``. Does not take an attempt number: cancelling means "stop
    whatever is currently pending or in flight for this work_id," which is
    unambiguous regardless of which attempt is running."""
    _transition(work, WorkStatus.CANCELLED)


def invalidate_work(work: WorkItem) -> None:
    """Mark ``work`` stale because a fact it depends on changed.

    Idempotent and safe from any state that means "this was, or might have
    been, current" (``PENDING``/``RUNNING``/``VALID``/already-``STALE``) -
    a no-op for ``CANCELLED``/``FAILED``, which are already not current and
    have nothing left to invalidate. This is what
    ``invalidation.invalidate_many`` calls instead of assigning ``status``
    directly - the *only* thing this function does is flip a status field;
    it never touches ``asyncio``, never cancels a task. Actually tearing down
    a ``RUNNING`` attempt (if the runtime chooses to) is a decision made
    outside ``backspace/``, informed by reading the status this sets.
    """
    if work.status in _INVALIDATABLE_STATUSES:
        work.status = WorkStatus.STALE


def is_result_current(work: WorkItem, attempt: int) -> bool:
    """Whether a result produced by execution ``attempt`` of ``work`` should
    still be accepted.

    Purely structural: compares ``attempt`` against ``work.execution_attempt``
    and checks ``work.status`` is one that means "this attempt's output can
    be trusted." Never inspects text, a tool name, or anything scenario- or
    benchmark-shaped - this is the advisory counterpart to the exceptions
    ``complete_work``/``fail_work`` raise, for a caller that wants to check
    before acting rather than handle a raised error after the fact.
    """
    if attempt != work.execution_attempt:
        return False
    return work.status in _ACCEPTABLE_RESULT_STATUSES
