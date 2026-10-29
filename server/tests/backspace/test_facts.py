from __future__ import annotations

import pytest

from app.backspace import ChangeKind, Fact, FactNotebook, FactNotFoundError, FactStatus


def test_fact_creation_has_sensible_defaults():
    fact = Fact(key="party_size", value=2)
    assert fact.key == "party_size"
    assert fact.value == 2
    assert fact.version == 1
    assert fact.status is FactStatus.CURRENT
    assert fact.goal_id is None
    assert fact.confidence == 1.0
    assert fact.fact_id  # auto-generated, non-empty


def test_two_facts_get_distinct_ids():
    a = Fact(key="party_size", value=2)
    b = Fact(key="party_size", value=2)
    assert a.fact_id != b.fact_id


def test_fact_optional_goal_id_can_be_set_or_omitted():
    with_goal = Fact(key="destination", value="Goa", goal_id="g1")
    without_goal = Fact(key="destination", value="Goa")
    assert with_goal.goal_id == "g1"
    assert without_goal.goal_id is None


def test_fact_provenance_is_a_free_form_bag():
    fact = Fact(key="party_size", value=5, provenance={"tool": "party_size_extractor"})
    assert fact.provenance == {"tool": "party_size_extractor"}
    # default is an empty, independent dict per instance
    other = Fact(key="party_size", value=5)
    assert other.provenance == {}
    other.provenance["x"] = 1
    assert fact.provenance == {"tool": "party_size_extractor"}


def test_fact_supersession_fields_are_representable():
    old = Fact(key="party_size", value=2, status=FactStatus.SUPERSEDED)
    new = Fact(key="party_size", value=5, supersedes=old.fact_id)
    old.superseded_by = new.fact_id
    assert new.supersedes == old.fact_id
    assert old.superseded_by == new.fact_id
    assert old.status is FactStatus.SUPERSEDED


def test_fact_serialization_round_trips_expected_shape():
    fact = Fact(
        key="party_size",
        value=5,
        fact_id="f1",
        version=2,
        status=FactStatus.CURRENT,
        source="user",
        turn_id="t1",
        goal_id="g1",
        confidence=0.9,
        supersedes="f0",
        superseded_by=None,
        created_at=1000.0,
        provenance={"note": "confirmed twice"},
    )
    assert fact.to_dict() == {
        "fact_id": "f1",
        "key": "party_size",
        "value": 5,
        "version": 2,
        "status": "current",
        "source": "user",
        "turn_id": "t1",
        "goal_id": "g1",
        "confidence": 0.9,
        "supersedes": "f0",
        "superseded_by": None,
        "created_at": 1000.0,
        "provenance": {"note": "confirmed twice"},
    }


def test_fact_serialization_is_deterministic_for_identical_input():
    kwargs = dict(
        key="party_size", value=5, fact_id="f1", version=1, source="user",
        turn_id="t1", goal_id="g1", confidence=1.0, created_at=1234.5,
    )
    first = Fact(**kwargs).to_dict()
    second = Fact(**kwargs).to_dict()
    assert first == second


def test_fact_status_enum_values():
    assert {s.value for s in FactStatus} == {"current", "superseded", "conflicting"}


def test_fact_status_rejects_unknown_value():
    with pytest.raises(ValueError):
        FactStatus("bogus")


# ===========================================================================
# FactNotebook - Phase 3
# ===========================================================================


def test_notebook_new_fact_creates_version_one():
    notebook = FactNotebook()
    update = notebook.observe("destination", "Goa", source="user", turn_id="t1")
    assert update.status is ChangeKind.NEW
    assert update.previous is None
    assert update.changeset is None
    assert update.fact.value == "Goa"
    assert update.fact.version == 1
    assert update.fact.status is FactStatus.CURRENT


def test_notebook_get_fact_returns_current_and_none_when_absent():
    notebook = FactNotebook()
    assert notebook.get_fact("destination") is None
    assert notebook.has_fact("destination") is False
    notebook.observe("destination", "Goa", source="user", turn_id="t1")
    fact = notebook.get_fact("destination")
    assert fact is not None
    assert fact.value == "Goa"
    assert notebook.has_fact("destination") is True


def test_notebook_unchanged_observation_does_not_increment_version_or_create_changeset():
    notebook = FactNotebook()
    notebook.observe("destination", "Goa", source="user", turn_id="t1")
    update = notebook.observe("destination", "Goa", source="user", turn_id="t2")
    assert update.status is ChangeKind.UNCHANGED
    assert update.changeset is None
    assert update.fact.version == 1
    assert notebook.get_fact("destination").version == 1
    assert len(notebook.get_fact_history("destination")) == 1


def test_notebook_changed_observation_increments_version_and_creates_changeset():
    notebook = FactNotebook()
    notebook.observe("party_size", 2, source="user", turn_id="t1")
    update = notebook.observe("party_size", 5, source="user", turn_id="t2")
    assert update.status is ChangeKind.CHANGED
    assert update.fact.value == 5
    assert update.fact.version == 2
    assert update.previous.value == 2
    assert update.previous.status is FactStatus.SUPERSEDED
    assert update.previous.superseded_by == update.fact.fact_id
    assert update.changeset is not None
    assert update.changeset.key == "party_size"
    assert update.changeset.kind is ChangeKind.CHANGED
    assert update.changeset.previous_fact.value == 2
    assert update.changeset.new_fact.value == 5
    # This phase stops at the ChangeSet: invalidation/recomputation are not run yet.
    assert update.invalidation is None
    assert update.plan is None


def test_notebook_multiple_changes_produce_monotonic_versions():
    notebook = FactNotebook()
    notebook.observe("party_size", 2, source="user", turn_id="t1")
    notebook.observe("party_size", 5, source="user", turn_id="t2")
    notebook.observe("party_size", 4, source="user", turn_id="t3")
    history = notebook.get_fact_history("party_size")
    assert [f.version for f in history] == [1, 2, 3]
    assert [f.value for f in history] == [2, 5, 4]
    assert notebook.get_fact("party_size").version == 3
    assert notebook.get_fact("party_size").value == 4


def test_notebook_unchanged_observation_never_produces_a_fourth_version():
    notebook = FactNotebook()
    notebook.observe("party_size", 2, source="user", turn_id="t1")
    notebook.observe("party_size", 5, source="user", turn_id="t2")
    notebook.observe("party_size", 4, source="user", turn_id="t3")
    notebook.observe("party_size", 4, source="user", turn_id="t4")  # repeats the current value
    history = notebook.get_fact_history("party_size")
    assert [f.version for f in history] == [1, 2, 3]
    assert notebook.get_fact("party_size").version == 3


def test_notebook_history_ordering_is_oldest_first():
    notebook = FactNotebook()
    notebook.observe("party_size", 2, source="user", turn_id="t1")
    notebook.observe("party_size", 5, source="user", turn_id="t2")
    history = notebook.get_fact_history("party_size")
    assert history[0].value == 2
    assert history[1].value == 5
    assert history[0].status is FactStatus.SUPERSEDED
    assert history[1].status is FactStatus.CURRENT


def test_notebook_history_does_not_mutate_retroactively():
    """A caller holding the original NEW FactUpdate's fact object must never
    see it change out from under them, even after a later CHANGED call."""
    notebook = FactNotebook()
    first_update = notebook.observe("party_size", 2, source="user", turn_id="t1")
    original_fact = first_update.fact
    assert original_fact.status is FactStatus.CURRENT
    assert original_fact.superseded_by is None

    notebook.observe("party_size", 5, source="user", turn_id="t2")

    # The object this test already holds a reference to is untouched...
    assert original_fact.status is FactStatus.CURRENT
    assert original_fact.superseded_by is None
    # ...even though the notebook's own record has moved on.
    assert notebook.get_fact_history("party_size")[0].status is FactStatus.SUPERSEDED
    assert notebook.get_fact_history("party_size")[0].superseded_by is not None


def test_notebook_history_list_returned_is_a_copy():
    notebook = FactNotebook()
    notebook.observe("party_size", 2, source="user", turn_id="t1")
    history = notebook.get_fact_history("party_size")
    history.append(Fact(key="party_size", value=999))
    assert len(notebook.get_fact_history("party_size")) == 1


def test_notebook_preserves_provenance_per_observation():
    notebook = FactNotebook()
    notebook.observe("party_size", 2, source="user", turn_id="t1", goal_id="g1", confidence=0.8)
    update = notebook.observe("party_size", 5, source="tool:party_size_extractor", turn_id="t2", goal_id="g2", confidence=0.6)
    assert update.fact.source == "tool:party_size_extractor"
    assert update.fact.turn_id == "t2"
    assert update.fact.goal_id == "g2"
    assert update.fact.confidence == 0.6
    assert update.previous.source == "user"
    assert update.previous.turn_id == "t1"
    assert update.previous.goal_id == "g1"
    assert update.previous.confidence == 0.8


def test_notebook_goal_id_is_opaque_and_never_interpreted():
    notebook = FactNotebook()
    # A value that would never survive being parsed as a real goal id, on
    # purpose - the notebook must store it verbatim regardless.
    weird_goal_id = "not a goal id; DROP TABLE goals; <<>>"
    update = notebook.observe("destination", "Goa", source="user", turn_id="t1", goal_id=weird_goal_id)
    assert update.fact.goal_id == weird_goal_id
    assert notebook.get_fact("destination").goal_id == weird_goal_id


def test_notebook_confidence_defaults_and_is_preserved():
    notebook = FactNotebook()
    default_update = notebook.observe("destination", "Goa", source="user", turn_id="t1")
    assert default_update.fact.confidence == 1.0
    explicit_update = notebook.observe("party_size", 5, source="user", turn_id="t1", confidence=0.42)
    assert explicit_update.fact.confidence == 0.42


def test_notebook_update_fact_by_id_changes_value_and_preserves_history():
    notebook = FactNotebook()
    first = notebook.observe("party_size", 2, source="user", turn_id="t1")
    update = notebook.update_fact(first.fact.fact_id, 5, source="user", turn_id="t2")
    assert update.status is ChangeKind.CHANGED
    assert update.fact.value == 5
    assert update.fact.version == 2
    assert notebook.get_fact("party_size").value == 5
    history = notebook.get_fact_history("party_size")
    assert [f.value for f in history] == [2, 5]


def test_notebook_update_fact_by_id_unchanged_value():
    notebook = FactNotebook()
    first = notebook.observe("destination", "Goa", source="user", turn_id="t1")
    update = notebook.update_fact(first.fact.fact_id, "Goa", source="user", turn_id="t2")
    assert update.status is ChangeKind.UNCHANGED
    assert update.changeset is None
    assert notebook.get_fact("destination").version == 1


def test_notebook_update_fact_raises_for_unknown_id():
    notebook = FactNotebook()
    with pytest.raises(FactNotFoundError) as excinfo:
        notebook.update_fact("does-not-exist", 5, source="user", turn_id="t1")
    assert excinfo.value.fact_id == "does-not-exist"


def test_notebook_update_fact_by_a_superseded_id_still_resolves_the_key():
    notebook = FactNotebook()
    first = notebook.observe("party_size", 2, source="user", turn_id="t1")
    notebook.observe("party_size", 5, source="user", turn_id="t2")
    # `first.fact.fact_id` now refers to a superseded version, not the
    # current one - it must still resolve to the right key.
    update = notebook.update_fact(first.fact.fact_id, 4, source="user", turn_id="t3")
    assert update.status is ChangeKind.CHANGED
    assert update.fact.value == 4
    assert notebook.get_fact("party_size").value == 4


def test_notebook_snapshot_contains_current_and_history():
    notebook = FactNotebook()
    notebook.observe("party_size", 2, source="user", turn_id="t1")
    notebook.observe("party_size", 5, source="user", turn_id="t2")
    notebook.observe("destination", "Goa", source="user", turn_id="t1")
    snapshot = notebook.snapshot()
    assert set(snapshot.keys()) == {"party_size", "destination"}
    assert snapshot["party_size"]["current"]["value"] == 5
    assert [v["value"] for v in snapshot["party_size"]["history"]] == [2, 5]
    assert snapshot["destination"]["current"]["value"] == "Goa"


def test_notebook_snapshot_returns_copies_not_live_references():
    notebook = FactNotebook()
    notebook.observe("party_size", 2, source="user", turn_id="t1")
    snapshot = notebook.snapshot()
    snapshot["party_size"]["current"]["value"] = 999
    snapshot["party_size"]["history"].append({"bogus": True})
    assert notebook.get_fact("party_size").value == 2
    assert len(notebook.get_fact_history("party_size")) == 1


def test_notebook_reset_clears_current_and_history():
    notebook = FactNotebook()
    notebook.observe("party_size", 2, source="user", turn_id="t1")
    notebook.observe("destination", "Goa", source="user", turn_id="t1")
    notebook.reset()
    assert notebook.get_fact("party_size") is None
    assert notebook.get_fact("destination") is None
    assert notebook.get_fact_history("party_size") == []
    assert notebook.snapshot() == {}
    # Reset must not resurrect old ids either.
    with pytest.raises(FactNotFoundError):
        notebook.update_fact("whatever", 1, source="user", turn_id="t2")


def test_reset_on_one_notebook_does_not_affect_another():
    a = FactNotebook()
    b = FactNotebook()
    a.observe("party_size", 2, source="user", turn_id="t1")
    b.observe("party_size", 9, source="user", turn_id="t1")
    a.reset()
    assert a.get_fact("party_size") is None
    assert b.get_fact("party_size").value == 9


def test_two_independent_notebooks_never_share_state():
    a = FactNotebook()
    b = FactNotebook()
    a.observe("party_size", 5, source="user", turn_id="t1")
    b.observe("party_size", 2, source="user", turn_id="t1")
    assert a.get_fact("party_size").value == 5
    assert b.get_fact("party_size").value == 2
    assert a.get_fact("party_size").fact_id != b.get_fact("party_size").fact_id


def test_notebook_ordinary_correction_is_changed_not_conflicting():
    """party_size: 5 -> 3 is an ordinary correction, not a conflict - nothing
    in this phase has a reason to mark it CONFLICTING."""
    notebook = FactNotebook()
    notebook.observe("party_size", 5, source="user", turn_id="t1", confidence=0.9)
    update = notebook.observe("party_size", 3, source="user", turn_id="t2", confidence=0.9)
    assert update.status is ChangeKind.CHANGED
    assert update.fact.status is FactStatus.CURRENT
    assert update.previous.status is FactStatus.SUPERSEDED
    history = notebook.get_fact_history("party_size")
    assert [f.value for f in history] == [5, 3]
    # Both observations' provenance survives, nothing was silently dropped.
    assert history[0].source == "user"
    assert history[0].turn_id == "t1"
    assert history[1].turn_id == "t2"
