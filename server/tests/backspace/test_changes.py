from __future__ import annotations

import pytest

from app.backspace import ChangeKind, ChangeSet, Fact


def test_changeset_new_has_no_previous_fact():
    new_fact = Fact(key="party_size", value=2)
    changeset = ChangeSet(key="party_size", kind=ChangeKind.NEW, new_fact=new_fact)
    assert changeset.kind is ChangeKind.NEW
    assert changeset.previous_fact is None
    assert changeset.new_fact is new_fact


def test_changeset_changed_carries_both_facts():
    previous = Fact(key="party_size", value=2, fact_id="f1")
    new_fact = Fact(key="party_size", value=5, fact_id="f2", supersedes="f1")
    changeset = ChangeSet(
        key="party_size", kind=ChangeKind.CHANGED, new_fact=new_fact, previous_fact=previous,
        source="user", turn_id="t2", goal_id="g1",
    )
    assert changeset.previous_fact is previous
    assert changeset.new_fact is new_fact
    assert changeset.new_fact.supersedes == previous.fact_id


def test_changeset_unchanged_can_still_reference_the_existing_fact():
    fact = Fact(key="destination", value="Goa", fact_id="f1")
    changeset = ChangeSet(key="destination", kind=ChangeKind.UNCHANGED, new_fact=fact, previous_fact=fact)
    assert changeset.kind is ChangeKind.UNCHANGED
    assert changeset.new_fact is changeset.previous_fact


def test_change_kind_enum_values():
    assert {k.value for k in ChangeKind} == {"new", "unchanged", "changed"}


def test_change_kind_rejects_unknown_value():
    with pytest.raises(ValueError):
        ChangeKind("bogus")


def test_changeset_serialization_nests_facts_and_is_deterministic():
    previous = Fact(key="party_size", value=2, fact_id="f1", created_at=1.0)
    new_fact = Fact(key="party_size", value=5, fact_id="f2", supersedes="f1", created_at=2.0)
    kwargs = dict(
        key="party_size", kind=ChangeKind.CHANGED, new_fact=new_fact, previous_fact=previous,
        changeset_id="cs1", source="user", turn_id="t2", goal_id="g1", created_at=3.0,
    )
    first = ChangeSet(**kwargs).to_dict()
    second = ChangeSet(**kwargs).to_dict()
    assert first == second
    assert first["new_fact"]["fact_id"] == "f2"
    assert first["previous_fact"]["fact_id"] == "f1"
