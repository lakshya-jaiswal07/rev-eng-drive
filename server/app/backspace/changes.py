"""ChangeSet data model.

A ``ChangeSet`` is what a single ``assert_fact``/``update_fact`` call is
eventually going to produce: a record of whether the observation was brand
new, matched what the notebook already had, or changed it. Determining
*which* of those three it is - comparing against the current Fact Notebook -
is notebook behaviour and is not implemented here; this file only defines the
shape that comparison will return.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .facts import Fact


class ChangeKind(str, Enum):
    """What kind of transition a fact observation represents."""

    NEW = "new"
    UNCHANGED = "unchanged"
    CHANGED = "changed"


@dataclass
class ChangeSet:
    """One semantic fact transition.

    Holds the full ``Fact`` objects (not just ids/values) on both sides so a
    later ``explain_change`` has everything it needs - source, confidence,
    provenance - without a second lookup.
    """

    key: str
    kind: ChangeKind
    new_fact: Fact | None
    changeset_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    previous_fact: Fact | None = None
    source: str = "unknown"
    turn_id: str = ""
    goal_id: str | None = None
    created_at: float = field(default_factory=time.time)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changeset_id": self.changeset_id,
            "key": self.key,
            "kind": self.kind.value,
            "new_fact": self.new_fact.to_dict() if self.new_fact is not None else None,
            "previous_fact": self.previous_fact.to_dict() if self.previous_fact is not None else None,
            "source": self.source,
            "turn_id": self.turn_id,
            "goal_id": self.goal_id,
            "created_at": self.created_at,
            "provenance": dict(self.provenance),
        }
