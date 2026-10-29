from __future__ import annotations

from app.backspace import (
    ChangeKind,
    ChangeSet,
    Dependency,
    DependencyKind,
    Fact,
    Invalidation,
)


def _changed_party_size_changeset() -> ChangeSet:
    previous = Fact(key="party_size", value=2, fact_id="f1")
    new_fact = Fact(key="party_size", value=5, fact_id="f2", supersedes="f1")
    return ChangeSet(key="party_size", kind=ChangeKind.CHANGED, new_fact=new_fact, previous_fact=previous)


def test_invalidation_holds_the_triggering_changeset():
    changeset = _changed_party_size_changeset()
    invalidation = Invalidation(changeset=changeset)
    assert invalidation.changeset is changeset
    assert invalidation.changed_facts == []
    assert invalidation.kept_work_ids == []
    assert invalidation.invalidated_work_ids == []
    assert invalidation.invalidated_claim_ids == []
    assert invalidation.affected_dependencies == []


def test_invalidation_can_represent_a_full_result():
    changeset = _changed_party_size_changeset()
    dep = Dependency(kind=DependencyKind.FACT_TO_WORK, from_id="f2", to_id="w1")
    invalidation = Invalidation(
        changeset=changeset,
        changed_facts=[changeset.new_fact],
        kept_work_ids=["w2"],
        invalidated_work_ids=["w1"],
        invalidated_claim_ids=["c1"],
        affected_dependencies=[dep],
        reason="party_size changed from 2 to 5",
    )
    assert invalidation.kept_work_ids == ["w2"]
    assert invalidation.invalidated_work_ids == ["w1"]
    assert invalidation.invalidated_claim_ids == ["c1"]
    assert invalidation.affected_dependencies == [dep]
    assert "party_size" in invalidation.reason


def test_invalidation_serialization_nests_changeset_and_dependencies():
    changeset = _changed_party_size_changeset()
    dep = Dependency(kind=DependencyKind.WORK_TO_CLAIM, from_id="w1", to_id="c1")
    invalidation = Invalidation(
        changeset=changeset,
        invalidated_work_ids=["w1"],
        affected_dependencies=[dep],
        reason="downstream of party_size",
        created_at=42.0,
    )
    data = invalidation.to_dict()
    assert data["changeset"]["key"] == "party_size"
    assert data["affected_dependencies"][0]["kind"] == "work_to_claim"
    assert data["invalidated_work_ids"] == ["w1"]
    assert data["created_at"] == 42.0


# ===========================================================================
# Phase 5 additions to the model: changesets (batch), and derived *_ids views
# ===========================================================================


def test_invalidation_changeset_id_property_derives_from_changeset():
    changeset = _changed_party_size_changeset()
    invalidation = Invalidation(changeset=changeset)
    assert invalidation.changeset_id == changeset.changeset_id


def test_invalidation_changed_fact_ids_property_derives_from_changed_facts():
    changeset = _changed_party_size_changeset()
    invalidation = Invalidation(changeset=changeset, changed_facts=[changeset.new_fact])
    assert invalidation.changed_fact_ids == [changeset.new_fact.fact_id]


def test_invalidation_affected_dependency_ids_property_derives_from_dependencies():
    changeset = _changed_party_size_changeset()
    dep = Dependency(kind=DependencyKind.FACT_TO_WORK, from_id="f2", to_id="w1")
    invalidation = Invalidation(changeset=changeset, affected_dependencies=[dep])
    assert invalidation.affected_dependency_ids == [dep.dependency_id]


def test_invalidation_defaults_to_a_single_element_changesets_list_when_constructed_manually():
    """`invalidate()`/`invalidate_many()` always populate `changesets`
    explicitly; a hand-built `Invalidation()` (as in the tests above) is not
    required to, so it defaults to empty rather than guessing."""
    changeset = _changed_party_size_changeset()
    invalidation = Invalidation(changeset=changeset)
    assert invalidation.changesets == []
    assert invalidation.changeset_ids == []


def test_invalidation_changesets_batch_and_changeset_ids():
    first = _changed_party_size_changeset()
    second_prev = Fact(key="budget", value=20000, fact_id="f3")
    second_new = Fact(key="budget", value=30000, fact_id="f4", supersedes="f3")
    second = ChangeSet(key="budget", kind=ChangeKind.CHANGED, new_fact=second_new, previous_fact=second_prev)

    invalidation = Invalidation(changeset=first, changesets=[first, second])
    assert invalidation.changeset is first  # backward-compatible: first changeset
    assert invalidation.changesets == [first, second]
    assert invalidation.changeset_ids == [first.changeset_id, second.changeset_id]


def test_invalidation_to_dict_includes_the_phase_5_derived_fields():
    changeset = _changed_party_size_changeset()
    dep = Dependency(kind=DependencyKind.FACT_TO_WORK, from_id="f2", to_id="w1")
    invalidation = Invalidation(
        changeset=changeset,
        changesets=[changeset],
        changed_facts=[changeset.new_fact],
        affected_dependencies=[dep],
    )
    data = invalidation.to_dict()
    assert data["changeset_id"] == changeset.changeset_id
    assert data["changeset_ids"] == [changeset.changeset_id]
    assert data["changed_fact_ids"] == [changeset.new_fact.fact_id]
    assert data["affected_dependency_ids"] == [dep.dependency_id]
    assert data["changesets"] == [changeset.to_dict()]
