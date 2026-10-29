from __future__ import annotations

import pytest

from app.backspace import (
    Claim,
    ClaimNotFoundError,
    ClaimNotInvalidatedError,
    ClaimNotSpokenError,
    ClaimStatus,
    DependencyGraph,
    Evidence,
    get_claim_history,
    invalidate_claim,
    mark_claim_spoken,
    require_claim_retractable,
    supersede_claim,
)


def test_evidence_matches_the_existing_runtime_shape():
    raw = {
        "doc_id": "flights#0",
        "title": "Fares",
        "snippet": "Economy fares start at...",
        "source": "flights",
    }
    evidence = Evidence.from_dict(raw)
    assert evidence.to_dict() == raw


def test_evidence_from_dict_tolerates_missing_optional_fields():
    evidence = Evidence.from_dict({"doc_id": "policy#1"})
    assert evidence.doc_id == "policy#1"
    assert evidence.title == ""
    assert evidence.snippet == ""
    assert evidence.source == ""


def test_claim_creation_defaults():
    claim = Claim(text="Baggage is 15kg in economy.")
    assert claim.text == "Baggage is 15kg in economy."
    assert claim.status is ClaimStatus.GENERATED
    assert claim.evidence is None
    assert claim.depends_on_facts == []
    assert claim.goal_id is None
    assert claim.claim_id


def test_claim_can_carry_evidence_and_fact_dependencies():
    evidence = Evidence(doc_id="hotels#2", title="Properties", snippet="...", source="hotels")
    claim = Claim(
        text="A room under 9000 is available.",
        work_id="w1",
        turn_id="t1",
        goal_id="g1",
        evidence=evidence,
        depends_on_facts=["f1", "f2"],
    )
    assert claim.evidence is evidence
    assert claim.depends_on_facts == ["f1", "f2"]
    assert claim.work_id == "w1"


def test_claim_lifecycle_states_are_all_representable():
    assert {s.value for s in ClaimStatus} == {
        "generated", "spoken", "heard", "valid",
        "invalidated", "retracted", "superseded",
    }


def test_claim_status_rejects_unknown_value():
    with pytest.raises(ValueError):
        ClaimStatus("bogus")


def test_claim_serialization_is_deterministic_for_identical_input():
    evidence = Evidence(doc_id="d1", title="T", snippet="S", source="src")
    kwargs = dict(
        text="x", claim_id="c1", work_id="w1", turn_id="t1", goal_id="g1",
        evidence=evidence, status=ClaimStatus.SPOKEN, depends_on_facts=["f1"],
        created_at=10.0, spoken_at=11.0, provenance={"a": 1},
    )
    first = Claim(**kwargs).to_dict()
    second = Claim(**kwargs).to_dict()
    assert first == second
    assert first["evidence"] == evidence.to_dict()


def test_claim_phase_7_fields_default_to_none():
    claim = Claim(text="x")
    assert claim.supersedes is None
    assert claim.superseded_by is None
    assert claim.invalidation_reason is None
    assert claim.retraction_id is None
    data = claim.to_dict()
    assert data["supersedes"] is None
    assert data["superseded_by"] is None
    assert data["invalidation_reason"] is None
    assert data["retraction_id"] is None


# ===========================================================================
# Phase 7 - claim ledger lifecycle functions
# ===========================================================================


def _graph_with_claim(claim_id: str = "C1", **claim_kwargs) -> tuple[DependencyGraph, Claim]:
    graph = DependencyGraph()
    claim = graph.register_claim(Claim(text="Hotel price is ₹18,000", claim_id=claim_id, **claim_kwargs))
    return graph, claim


# -- register / initial state --------------------------------------------------

def test_1_register_claim_starts_generated():
    graph, claim = _graph_with_claim()
    assert claim.status is ClaimStatus.GENERATED
    assert claim.spoken_at is None


def test_2_registering_twice_is_idempotent_first_wins():
    graph = DependencyGraph()
    first = graph.register_claim(Claim(text="a", claim_id="C1"))
    second = graph.register_claim(Claim(text="b", claim_id="C1"))
    assert first is second
    assert second.text == "a"


# -- mark_claim_spoken -----------------------------------------------------------

def test_3_mark_claim_spoken_transitions_generated_to_spoken():
    graph, claim = _graph_with_claim()
    mark_claim_spoken(graph, "C1")
    assert claim.status is ClaimStatus.SPOKEN


def test_4_spoken_timestamp_is_set_and_preserved():
    graph, claim = _graph_with_claim()
    mark_claim_spoken(graph, "C1")
    first_spoken_at = claim.spoken_at
    assert first_spoken_at is not None


def test_5_marking_spoken_twice_is_safe_and_does_not_retimestamp():
    graph, claim = _graph_with_claim()
    mark_claim_spoken(graph, "C1")
    first_spoken_at = claim.spoken_at
    mark_claim_spoken(graph, "C1")
    assert claim.spoken_at == first_spoken_at
    assert claim.status is ClaimStatus.SPOKEN


def test_mark_nonexistent_claim_as_spoken_raises():
    graph = DependencyGraph()
    with pytest.raises(ClaimNotFoundError) as excinfo:
        mark_claim_spoken(graph, "ghost")
    assert excinfo.value.claim_id == "ghost"


def test_marking_an_already_superseded_claim_spoken_preserves_spoken_at_but_not_status():
    graph, old = _graph_with_claim("C1")
    new = supersede_claim(graph, "C1", Claim(text="Hotel price is ₹31,000", claim_id="C2"))
    assert old.status is ClaimStatus.SUPERSEDED
    mark_claim_spoken(graph, "C1")
    assert old.spoken_at is not None
    assert old.status is ClaimStatus.SUPERSEDED  # not regressed back to SPOKEN


# -- invalidate_claim (direct, single-claim) --------------------------------------

def test_6_invalidate_unspoken_claim():
    graph, claim = _graph_with_claim()
    invalidate_claim(graph, "C1", "party_size changed")
    assert claim.status is ClaimStatus.INVALIDATED
    assert claim.invalidation_reason == "party_size changed"


def test_7_unspoken_invalid_claim_cannot_be_retracted():
    graph, claim = _graph_with_claim()
    invalidate_claim(graph, "C1", "party_size changed")
    with pytest.raises(ClaimNotSpokenError):
        require_claim_retractable(graph, "C1")


def test_8_invalidate_spoken_claim():
    graph, claim = _graph_with_claim()
    mark_claim_spoken(graph, "C1")
    invalidate_claim(graph, "C1", "party_size changed")
    assert claim.status is ClaimStatus.INVALIDATED
    assert claim.spoken_at is not None  # preserved, not erased


def test_9_spoken_invalidated_claim_is_retractable():
    graph, claim = _graph_with_claim()
    mark_claim_spoken(graph, "C1")
    invalidate_claim(graph, "C1", "party_size changed")
    retractable = require_claim_retractable(graph, "C1")
    assert retractable is claim


def test_invalidate_nonexistent_claim_raises():
    graph = DependencyGraph()
    with pytest.raises(ClaimNotFoundError):
        invalidate_claim(graph, "ghost", "x")


def test_12_duplicate_invalidation_is_idempotent():
    graph, claim = _graph_with_claim()
    invalidate_claim(graph, "C1", "reason A")
    invalidate_claim(graph, "C1", "reason B")
    assert claim.status is ClaimStatus.INVALIDATED
    assert claim.invalidation_reason == "reason B"  # latest wins, no history kept


def test_invalidate_claim_does_not_regress_a_retracted_claim():
    graph, claim = _graph_with_claim()
    mark_claim_spoken(graph, "C1")
    invalidate_claim(graph, "C1", "first reason")
    claim.status = ClaimStatus.RETRACTED  # simulate a completed retraction
    invalidate_claim(graph, "C1", "second reason")
    assert claim.status is ClaimStatus.RETRACTED
    assert claim.invalidation_reason == "first reason"  # untouched by the no-op


# -- require_claim_retractable / retraction preconditions ----------------------

def test_14_cannot_retract_an_unspoken_claim():
    graph, claim = _graph_with_claim()
    invalidate_claim(graph, "C1", "x")
    with pytest.raises(ClaimNotSpokenError):
        require_claim_retractable(graph, "C1")


def test_15_cannot_retract_a_valid_claim():
    graph, claim = _graph_with_claim()
    mark_claim_spoken(graph, "C1")  # spoken, but never invalidated
    with pytest.raises(ClaimNotInvalidatedError) as excinfo:
        require_claim_retractable(graph, "C1")
    assert excinfo.value.status is ClaimStatus.SPOKEN


def test_16_retract_missing_claim_produces_correct_error():
    graph = DependencyGraph()
    with pytest.raises(ClaimNotFoundError) as excinfo:
        require_claim_retractable(graph, "ghost")
    assert excinfo.value.claim_id == "ghost"


def test_require_claim_retractable_does_not_mutate_anything():
    graph, claim = _graph_with_claim()
    mark_claim_spoken(graph, "C1")
    invalidate_claim(graph, "C1", "x")
    before_status = claim.status
    require_claim_retractable(graph, "C1")
    assert claim.status == before_status  # still INVALIDATED, not RETRACTED


# -- supersession -----------------------------------------------------------------

def test_23_supersede_claim_links_both_directions_and_moves_old_to_superseded():
    graph, old = _graph_with_claim("C1")
    new = supersede_claim(graph, "C1", Claim(text="Hotel price is ₹31,000", claim_id="C2"))
    assert new.supersedes == "C1"
    assert old.superseded_by == "C2"
    assert old.status is ClaimStatus.SUPERSEDED


def test_supersede_claim_does_not_downgrade_a_retracted_claim():
    graph, old = _graph_with_claim("C1")
    old.status = ClaimStatus.RETRACTED
    supersede_claim(graph, "C1", Claim(text="new", claim_id="C2"))
    assert old.status is ClaimStatus.RETRACTED  # a retraction is not silently overwritten


def test_supersede_unknown_old_claim_raises():
    graph = DependencyGraph()
    with pytest.raises(ClaimNotFoundError):
        supersede_claim(graph, "ghost", Claim(text="new", claim_id="C2"))


# -- claim history ------------------------------------------------------------------

def test_17_claim_history_remains_available_after_invalidation():
    graph, claim = _graph_with_claim()
    mark_claim_spoken(graph, "C1")
    invalidate_claim(graph, "C1", "party_size changed")
    history = get_claim_history(graph, "C1")
    assert history == [claim]
    assert history[0].text == "Hotel price is ₹18,000"
    assert history[0].status is ClaimStatus.INVALIDATED


def test_claim_history_walks_a_full_supersession_chain_oldest_first():
    graph, old = _graph_with_claim("C1")
    new = supersede_claim(graph, "C1", Claim(text="v2", claim_id="C2"))
    newer = supersede_claim(graph, "C2", Claim(text="v3", claim_id="C3"))

    for claim_id in ("C1", "C2", "C3"):
        history = get_claim_history(graph, claim_id)
        assert [c.claim_id for c in history] == ["C1", "C2", "C3"]
    assert get_claim_history(graph, "C3")[-1] is newer
    assert get_claim_history(graph, "C1")[0] is old


def test_claim_history_of_a_claim_with_no_supersession_is_itself_alone():
    graph, claim = _graph_with_claim()
    assert get_claim_history(graph, "C1") == [claim]


def test_claim_history_of_unknown_claim_is_empty():
    graph = DependencyGraph()
    assert get_claim_history(graph, "ghost") == []
