from __future__ import annotations

import pytest

from app.backspace import (
    BackspaceEvent,
    BackspaceEventType,
    ChangeKind,
    ChangeSet,
    Claim,
    Fact,
    FactUpdate,
    Invalidation,
    RecomputationPlan,
    Retraction,
)


def test_fact_update_for_a_brand_new_fact_has_no_previous():
    fact = Fact(key="party_size", value=2)
    update = FactUpdate(fact=fact, status=ChangeKind.NEW)
    assert update.fact is fact
    assert update.status is ChangeKind.NEW
    assert update.previous is None
    assert update.changeset is None
    assert update.invalidation is None
    assert update.plan is None


def test_fact_update_for_a_changed_fact_can_carry_the_whole_pipeline():
    previous = Fact(key="party_size", value=2, fact_id="f1")
    new_fact = Fact(key="party_size", value=5, fact_id="f2", supersedes="f1")
    changeset = ChangeSet(key="party_size", kind=ChangeKind.CHANGED, new_fact=new_fact, previous_fact=previous)
    invalidation = Invalidation(changeset=changeset, invalidated_work_ids=["w1"])
    plan = RecomputationPlan(invalidation=invalidation, work_items_to_recompute=["w1"])

    update = FactUpdate(
        fact=new_fact, status=ChangeKind.CHANGED, previous=previous,
        changeset=changeset, invalidation=invalidation, plan=plan,
    )
    assert update.previous is previous
    assert update.changeset is changeset
    assert update.invalidation is invalidation
    assert update.plan is plan


def test_fact_update_serialization_nests_everything_present():
    fact = Fact(key="party_size", value=2, fact_id="f1")
    update = FactUpdate(fact=fact, status=ChangeKind.NEW)
    data = update.to_dict()
    assert data["fact"]["fact_id"] == "f1"
    assert data["status"] == "new"
    assert data["previous"] is None
    assert data["changeset"] is None


def test_retraction_creation_and_defaults():
    retraction = Retraction(claim_id="c1", reason="party_size changed, quote no longer applies")
    assert retraction.claim_id == "c1"
    assert "party_size" in retraction.reason
    assert retraction.previous_claim is None
    assert retraction.changed_facts == []
    assert retraction.retraction_id


def test_retraction_can_carry_the_previous_claim_and_changed_facts():
    claim = Claim(text="A room under 9000 is available.", claim_id="c1")
    fact = Fact(key="party_size", value=5, fact_id="f2")
    retraction = Retraction(
        claim_id="c1", reason="party size changed", previous_claim=claim, changed_facts=[fact],
        source="backspace_core",
    )
    assert retraction.previous_claim is claim
    assert retraction.changed_facts == [fact]


def test_retraction_serialization_is_deterministic_for_identical_input():
    kwargs = dict(
        claim_id="c1", reason="r", retraction_id="r1", source="s", created_at=1.0,
        provenance={"a": 1},
    )
    first = Retraction(**kwargs).to_dict()
    second = Retraction(**kwargs).to_dict()
    assert first == second


@pytest.mark.parametrize(
    "event_type",
    [
        BackspaceEventType.FACT_ADDED,
        BackspaceEventType.FACT_CHANGED,
        BackspaceEventType.WORK_REGISTERED,
        BackspaceEventType.DEPENDENCY_REGISTERED,
        BackspaceEventType.CLAIM_REGISTERED,
        BackspaceEventType.WORK_INVALIDATED,
        BackspaceEventType.CLAIM_INVALIDATED,
        BackspaceEventType.CLAIM_RETRACTED,
        BackspaceEventType.RECOMPUTATION_PLANNED,
        BackspaceEventType.BACKSPACE_COMPLETED,
    ],
)
def test_every_required_event_type_is_representable(event_type):
    event = BackspaceEvent(event_type=event_type, turn_id="t1", payload={"k": "v"})
    assert event.event_type is event_type
    assert event.to_dict()["event_type"] == event_type.value


def test_backspace_event_type_rejects_unknown_value():
    with pytest.raises(ValueError):
        BackspaceEventType("bogus")


def test_backspace_event_serialization_is_deterministic_for_identical_input():
    kwargs = dict(
        event_type=BackspaceEventType.FACT_CHANGED, turn_id="t1", goal_id="g1",
        payload={"key": "party_size"}, event_id="e1", created_at=99.0,
    )
    first = BackspaceEvent(**kwargs).to_dict()
    second = BackspaceEvent(**kwargs).to_dict()
    assert first == second
