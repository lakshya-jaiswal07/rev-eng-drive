"""Fact data model and the FactNotebook that tracks it over time.

A ``Fact`` is a single typed observation the agent layer has asserted, keyed
by a stable ``key`` ("party_size", "destination", ...). It carries no opinion
about how it was derived - no utterance, no tokens, nothing NLP-shaped. That
is deliberate: the model has to be producible from a structured tool result
or an LLM's structured output exactly as easily as from a hand-written test.

``FactNotebook`` is the session-local store built out of that shape: the
current value per key, and the full version history behind it. It is a plain
instance-state class - every dict below is created fresh in ``__init__``, so
two ``FactNotebook()``s never share state and ``reset()`` on one never
touches another.

A note on the two local (in-method, not top-of-file) imports inside
``FactNotebook``: ``ChangeSet`` lives in ``changes.py`` and ``FactUpdate``
lives in ``core.py``, and both of those modules import *this* one for
``Fact``. Importing them back at the top of this file would be a circular
import. Importing them inside the method that needs them is not - by the
time ``observe()`` is actually called, the whole package has already
finished loading, so the import resolves immediately. This keeps ``facts.py``
itself free of any sibling import, which is what lets it be the leaf module
everything else in the package builds on.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class FactStatus(str, Enum):
    """Where a fact currently stands relative to the rest of the notebook.

    Assigning and transitioning between these is notebook behaviour and does
    not belong in this file - a Fact can be constructed in any status, the
    same way a ``Goal`` can be constructed in any ``GoalStatus``.
    """

    CURRENT = "current"
    SUPERSEDED = "superseded"
    CONFLICTING = "conflicting"


@dataclass
class Fact:
    """One typed, versioned observation.

    ``goal_id`` is an opaque label for cross-referencing against the existing
    goal stack - it is never parsed or resolved here. ``provenance`` is a
    free-form bag for whatever the producer wants to keep alongside the fact
    (e.g. which tool produced it, a raw confidence breakdown) without forcing
    every producer into the same fixed set of fields.
    """

    key: str
    value: Any
    fact_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    version: int = 1
    status: FactStatus = FactStatus.CURRENT
    source: str = "unknown"
    turn_id: str = ""
    goal_id: str | None = None
    confidence: float = 1.0
    supersedes: str | None = None
    superseded_by: str | None = None
    created_at: float = field(default_factory=time.time)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "key": self.key,
            "value": self.value,
            "version": self.version,
            "status": self.status.value,
            "source": self.source,
            "turn_id": self.turn_id,
            "goal_id": self.goal_id,
            "confidence": self.confidence,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "created_at": self.created_at,
            "provenance": dict(self.provenance),
        }


class FactNotFoundError(Exception):
    """Raised when a ``fact_id`` is referenced but the notebook has never seen it.

    Mirrors ``harness.py``'s ``HarnessRefusal``: a plain ``Exception``
    subclass carrying the one piece of context a caller needs, rather than a
    bespoke exception hierarchy.
    """

    def __init__(self, fact_id: str) -> None:
        super().__init__(f"No fact with id {fact_id!r}.")
        self.fact_id = fact_id


def _values_equal(a: Any, b: Any) -> bool:
    """Whether two observed values count as the same fact.

    Plain structural equality (``==``) - nothing more. ``2`` and ``2.0``
    compare equal because Python's own numeric types already do that;
    ``"Goa"`` and ``"goa"`` do not, and a caller that wants those treated as
    the same observation has to normalise before calling ``observe()``. No
    case-folding, no whitespace trimming, no fuzzy or semantic matching -
    this module never interprets a value, so it has no basis to guess what
    "close enough" would mean for one. This is the smallest rule that
    satisfies the brief's own examples (``2 == 2``, ``"Goa" == "Goa"``)
    without inventing normalisation nothing asked for yet.
    """
    return a == b


class FactNotebook:
    """Session-local store of the current value, and full history, per key.

    Every mapping below is an ordinary instance attribute created in
    ``__init__`` - there is no module-level dictionary anywhere in this file,
    so nothing here is shared between sessions, tests, or ``BackspaceCore``
    instances.
    """

    def __init__(self) -> None:
        self._current: dict[str, Fact] = {}
        self._history: dict[str, list[Fact]] = {}
        self._by_id: dict[str, Fact] = {}

    # -- inspection --------------------------------------------------------
    def has_fact(self, key: str) -> bool:
        return key in self._current

    def get_fact(self, key: str) -> Fact | None:
        """The current fact for ``key``, or ``None`` if it has never been observed."""
        return self._current.get(key)

    def get_fact_history(self, key: str) -> list[Fact]:
        """Every version of ``key``, oldest first. A copy - mutating the
        returned list never touches the notebook."""
        return list(self._history.get(key, []))

    def snapshot(self) -> dict[str, Any]:
        """A read-only, serialisation-friendly dump of every key's current
        fact and full history. Every list/dict returned is a fresh copy."""
        return {
            key: {
                "current": fact.to_dict(),
                "history": [f.to_dict() for f in self._history.get(key, [])],
            }
            for key, fact in self._current.items()
        }

    # -- mutation ------------------------------------------------------------
    def observe(
        self,
        key: str,
        value: Any,
        *,
        source: str,
        turn_id: str,
        goal_id: str | None = None,
        confidence: float = 1.0,
    ) -> FactUpdate:
        """Record one structured fact observation and classify it.

        ``goal_id`` is carried through unread: it is never parsed, compared,
        or resolved against anything. This method - and this whole module -
        has no idea what a ``GoalTracker`` is.

        Returns a ``FactUpdate``:

        * no prior fact for ``key`` -> ``status=NEW``, ``previous=None``,
          ``changeset=None``, a version-1 ``Fact`` is created.
        * the observed value equals the current value (see
          ``_values_equal``) -> ``status=UNCHANGED``, the current fact is
          returned unchanged, no new version, no ``ChangeSet``, history
          untouched.
        * otherwise -> ``status=CHANGED``, a new version is created and
          becomes current, the old version is preserved in history (not
          mutated in place - see below), and a ``ChangeSet`` describing the
          transition is returned alongside it.
        """
        # Local imports: see the module docstring for why these can't live
        # at the top of this file.
        from .changes import ChangeKind, ChangeSet
        from .core import FactUpdate

        current = self._current.get(key)

        if current is None:
            fact = Fact(
                key=key,
                value=value,
                version=1,
                status=FactStatus.CURRENT,
                source=source,
                turn_id=turn_id,
                goal_id=goal_id,
                confidence=confidence,
            )
            self._store(key, fact)
            return FactUpdate(fact=fact, status=ChangeKind.NEW, previous=None)

        if _values_equal(current.value, value):
            return FactUpdate(fact=current, status=ChangeKind.UNCHANGED, previous=current)

        new_fact = Fact(
            key=key,
            value=value,
            version=current.version + 1,
            status=FactStatus.CURRENT,
            source=source,
            turn_id=turn_id,
            goal_id=goal_id,
            confidence=confidence,
            supersedes=current.fact_id,
        )
        # `replace()` builds a *new* object rather than mutating `current` in
        # place, so anyone already holding a reference to `current` (e.g. the
        # FactUpdate this same method returned the last time this key
        # changed) keeps seeing it exactly as it was at that moment. Only the
        # notebook's own bookkeeping - the history list and the id index -
        # moves forward to the closed-out copy.
        closed_previous = replace(current, status=FactStatus.SUPERSEDED, superseded_by=new_fact.fact_id)
        self._replace_history_entry(key, current.fact_id, closed_previous)
        self._store(key, new_fact)

        changeset = ChangeSet(
            key=key,
            kind=ChangeKind.CHANGED,
            new_fact=new_fact,
            previous_fact=closed_previous,
            source=source,
            turn_id=turn_id,
            goal_id=goal_id,
        )
        return FactUpdate(
            fact=new_fact, status=ChangeKind.CHANGED, previous=closed_previous, changeset=changeset
        )

    def update_fact(
        self,
        fact_id: str,
        value: Any,
        *,
        source: str,
        turn_id: str,
        goal_id: str | None = None,
        confidence: float = 1.0,
    ) -> FactUpdate:
        """Correct the fact identified by ``fact_id``.

        Resolves ``fact_id`` to its key (raising ``FactNotFoundError`` if the
        notebook has never seen it, including for an id that belonged to an
        already-superseded version - the id index is kept up to date across
        supersession) and then runs the exact same NEW/UNCHANGED/CHANGED
        classification as ``observe()`` against the *current* value for that
        key. Versioning stays monotonic per key regardless of which
        historical id was used to reach it.
        """
        existing = self._by_id.get(fact_id)
        if existing is None:
            raise FactNotFoundError(fact_id)
        return self.observe(
            existing.key, value, source=source, turn_id=turn_id, goal_id=goal_id, confidence=confidence
        )

    def reset(self) -> None:
        """Clear this notebook's facts and history. Other notebooks are untouched."""
        self._current.clear()
        self._history.clear()
        self._by_id.clear()

    # -- internals -----------------------------------------------------------
    def _store(self, key: str, fact: Fact) -> None:
        self._current[key] = fact
        self._history.setdefault(key, []).append(fact)
        self._by_id[fact.fact_id] = fact

    def _replace_history_entry(self, key: str, fact_id: str, replacement: Fact) -> None:
        history = self._history.setdefault(key, [])
        for index, fact in enumerate(history):
            if fact.fact_id == fact_id:
                history[index] = replacement
                break
        self._by_id[fact_id] = replacement
