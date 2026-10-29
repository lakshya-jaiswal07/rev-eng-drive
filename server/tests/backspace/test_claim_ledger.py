"""BackspaceCore-level claim ledger tests: retract_claim, snapshot, reset,
session isolation, and the canonical scenario from the Phase 7 brief.

``test_claims.py`` covers the pure lifecycle functions (``mark_claim_spoken``,
``invalidate_claim``, ``require_claim_retractable``, ``supersede_claim``,
``get_claim_history``) directly against a ``DependencyGraph``. This file
drives the same lifecycle through ``BackspaceCore``'s public API instead, the
way a real caller would, and adds the scenarios that only make sense at that
level: retraction idempotency backed by the facade's own storage, snapshot
content, reset, and session isolation.
"""

from __future__ import annotations

import pytest

from app.backspace import (
    BackspaceCore,
    ClaimNotFoundError,
    ClaimNotInvalidatedError,
    ClaimNotSpokenError,
    ClaimStatus,
    DependencyKind,
    Claim,
    Retraction,
    WorkItem,
    WorkStatus,
)

FTW = DependencyKind.FACT_TO_WORK
WTC = DependencyKind.WORK_TO_CLAIM
WTW = DependencyKind.WORK_TO_WORK


def _canonical_core() -> tuple[BackspaceCore, dict[str, str]]:
    """Builds exactly the scenario from the Phase 7 brief:

    destination=Goa, party_size=2, budget=20000
    hotel_search depends on destination
    price_calculation depends on party_size + budget
    "Hotel price is ₹18,000" claim depends on price_calculation
    """
    core = BackspaceCore()
    core.register_work(WorkItem(kind="hotel_search", work_id="hotel_search"))
    core.register_work(WorkItem(kind="price_calculation", work_id="price_calculation"))
    claim = core.register_claim(
        Claim(text="Hotel price is ₹18,000", claim_id="hotel_claim", work_id="price_calculation")
    )

    destination = core.assert_fact("destination", "Goa", source="user", turn_id="t1")
    party_size = core.assert_fact("party_size", 2, source="user", turn_id="t1")
    budget = core.assert_fact("budget", 20000, source="user", turn_id="t1")

    core.register_dependency(FTW, destination.fact.fact_id, "hotel_search")
    core.register_dependency(FTW, party_size.fact.fact_id, "price_calculation")
    core.register_dependency(FTW, budget.fact.fact_id, "price_calculation")
    core.register_dependency(WTC, "price_calculation", "hotel_claim")

    fact_ids = {
        "destination": destination.fact.fact_id,
        "party_size": party_size.fact.fact_id,
        "budget": budget.fact.fact_id,
    }
    return core, fact_ids


# ===========================================================================
# CANONICAL SCENARIO (Phase 7 brief, verbatim)
# ===========================================================================


def test_canonical_scenario_spoken_claim_gets_invalidated_and_retracted():
    core, fact_ids = _canonical_core()
    core.mark_claim_spoken("hotel_claim")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)

    # destination fact: unchanged
    assert core.get_fact("destination").value == "Goa"
    assert core.get_fact("destination").version == 1
    # budget fact: unchanged
    assert core.get_fact("budget").value == 20000
    assert core.get_fact("budget").version == 1
    # party_size: changed
    assert core.get_fact("party_size").value == 5
    assert core.get_fact("party_size").version == 2

    # hotel_search: preserved
    assert "hotel_search" in invalidation.kept_work_ids
    assert core.graph.get_work("hotel_search").status is WorkStatus.PENDING

    # price_calculation: stale
    assert "price_calculation" in invalidation.invalidated_work_ids
    assert core.graph.get_work("price_calculation").status is WorkStatus.STALE

    # hotel claim: INVALIDATED
    assert invalidation.invalidated_claim_ids == ["hotel_claim"]
    assert core.get_claim("hotel_claim").status is ClaimStatus.INVALIDATED

    # because claim was spoken: retraction is available/creatable
    assert invalidation.spoken_invalidated_claim_ids == ["hotel_claim"]
    assert invalidation.unspoken_invalidated_claim_ids == []
    retraction = core.retract_claim("hotel_claim", reason=invalidation.reason)
    assert isinstance(retraction, Retraction)
    assert retraction.claim_id == "hotel_claim"
    assert core.get_claim("hotel_claim").status is ClaimStatus.RETRACTED


def test_canonical_scenario_never_reruns_hotel_search_or_recomputes_anything():
    """The core must not rerun hotel_search, generate a replacement answer,
    or do anything beyond classifying/marking state - no execution."""
    core, _ = _canonical_core()
    core.mark_claim_spoken("hotel_claim")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)

    hotel_work = core.graph.get_work("hotel_search")
    # Untouched: no output was ever produced, no status change, no re-run.
    assert hotel_work.status is WorkStatus.PENDING
    assert hotel_work.output is None


# ===========================================================================
# retract_claim via the facade
# ===========================================================================


def test_10_retraction_references_the_original_claim():
    core, _ = _canonical_core()
    core.mark_claim_spoken("hotel_claim")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)

    retraction = core.retract_claim("hotel_claim", reason="party_size changed")
    assert retraction.claim_id == "hotel_claim"
    assert retraction.previous_claim is not None
    assert retraction.previous_claim.text == "Hotel price is ₹18,000"
    assert retraction.previous_claim.claim_id == "hotel_claim"


def test_11_retraction_preserves_the_reason():
    core, _ = _canonical_core()
    core.mark_claim_spoken("hotel_claim")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)

    retraction = core.retract_claim("hotel_claim", reason="party_size changed from 2 to 5")
    assert retraction.reason == "party_size changed from 2 to 5"


def test_retraction_can_carry_which_facts_changed_when_supplied():
    core, _ = _canonical_core()
    core.mark_claim_spoken("hotel_claim")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)

    retraction = core.retract_claim(
        "hotel_claim", reason=invalidation.reason, changed_facts=invalidation.changed_facts
    )
    assert len(retraction.changed_facts) == 1
    assert retraction.changed_facts[0].key == "party_size"
    assert retraction.changed_facts[0].value == 5


def test_retraction_omits_changed_facts_when_not_supplied():
    core, _ = _canonical_core()
    core.mark_claim_spoken("hotel_claim")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)

    retraction = core.retract_claim("hotel_claim", reason="x")
    assert retraction.changed_facts == []


def test_13_duplicate_retraction_is_idempotent_via_the_facade():
    core, _ = _canonical_core()
    core.mark_claim_spoken("hotel_claim")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)

    first = core.retract_claim("hotel_claim", reason="reason A")
    for _ in range(10):
        again = core.retract_claim("hotel_claim", reason="a different reason each time")
        assert again is first
    assert core.get_retraction("hotel_claim") is first


def test_retract_claim_missing_claim_raises():
    core = BackspaceCore()
    with pytest.raises(ClaimNotFoundError):
        core.retract_claim("ghost", "x")


def test_retract_claim_unspoken_raises_via_facade():
    core = BackspaceCore()
    core.register_claim(Claim(text="never spoken", claim_id="C1"))
    core.invalidate_claim("C1", "manual")
    with pytest.raises(ClaimNotSpokenError):
        core.retract_claim("C1", "x")


def test_retract_claim_not_invalidated_raises_via_facade():
    core = BackspaceCore()
    claim = core.register_claim(Claim(text="fine claim", claim_id="C1"))
    core.mark_claim_spoken("C1")
    with pytest.raises(ClaimNotInvalidatedError):
        core.retract_claim("C1", "x")


# ===========================================================================
# 18/19/20 - graph remains the source of truth; only affected claims move
# ===========================================================================


def test_18_fact_work_claim_invalidation_still_works_through_the_facade():
    core, _ = _canonical_core()
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)
    assert invalidation.invalidated_claim_ids == ["hotel_claim"]


def test_19_and_20_only_affected_claims_invalidated_others_remain_valid():
    core, _ = _canonical_core()
    unrelated_claim = core.register_claim(Claim(text="unrelated claim", claim_id="unrelated"))
    core.register_dependency(WTC, "hotel_search", "unrelated")

    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    invalidation = core.invalidate(change.changeset)

    assert invalidation.invalidated_claim_ids == ["hotel_claim"]
    assert "unrelated" not in invalidation.invalidated_claim_ids
    assert unrelated_claim.status is ClaimStatus.GENERATED  # untouched


def test_22_retraction_does_not_mutate_unrelated_claims():
    core, _ = _canonical_core()
    unrelated_claim = core.register_claim(Claim(text="unrelated claim", claim_id="unrelated"))
    core.register_dependency(WTC, "hotel_search", "unrelated")
    core.mark_claim_spoken("unrelated")

    core.mark_claim_spoken("hotel_claim")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)
    core.retract_claim("hotel_claim", reason="x")

    assert unrelated_claim.status is ClaimStatus.SPOKEN  # untouched by the retraction
    assert core.get_retraction("unrelated") is None


# ===========================================================================
# 24 - snapshot()
# ===========================================================================


def test_24_snapshot_includes_claim_lifecycle_and_retraction_state():
    core, _ = _canonical_core()
    core.mark_claim_spoken("hotel_claim")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)
    core.retract_claim("hotel_claim", reason="party_size changed")

    snapshot = core.snapshot()
    assert "claims" in snapshot
    assert "retractions" in snapshot

    claim_snapshot = snapshot["claims"]["hotel_claim"]
    assert claim_snapshot["status"] == "retracted"
    assert claim_snapshot["spoken_at"] is not None
    assert claim_snapshot["retraction_id"] is not None

    retraction_snapshot = snapshot["retractions"]["hotel_claim"]
    assert retraction_snapshot["reason"] == "party_size changed"
    assert retraction_snapshot["previous_claim"]["status"] == "invalidated"


def test_snapshot_is_read_only_and_returns_copies():
    core, _ = _canonical_core()
    snapshot = core.snapshot()
    snapshot["claims"]["hotel_claim"]["status"] = "tampered"
    assert core.get_claim("hotel_claim").status is ClaimStatus.GENERATED


# ===========================================================================
# reset() and session isolation
# ===========================================================================


def test_21_reset_clears_the_retraction_ledger():
    core, _ = _canonical_core()
    core.mark_claim_spoken("hotel_claim")
    change = core.assert_fact("party_size", 5, source="user", turn_id="t2")
    core.invalidate(change.changeset)
    core.retract_claim("hotel_claim", reason="x")
    assert core.get_retraction("hotel_claim") is not None

    core.reset()

    assert core.get_claim("hotel_claim") is None
    assert core.get_retraction("hotel_claim") is None


def test_session_isolation_for_the_claim_ledger():
    core_a, _ = _canonical_core()
    core_b, _ = _canonical_core()

    core_a.mark_claim_spoken("hotel_claim")
    change_a = core_a.assert_fact("party_size", 5, source="user", turn_id="t2")
    core_a.invalidate(change_a.changeset)
    core_a.retract_claim("hotel_claim", reason="x")

    # Core B's identical-looking claim was never spoken, never invalidated,
    # never retracted - core A's actions must not leak across.
    assert core_b.get_claim("hotel_claim").status is ClaimStatus.GENERATED
    assert core_b.get_claim("hotel_claim").spoken_at is None
    assert core_b.get_retraction("hotel_claim") is None


# ===========================================================================
# 25 - package isolation (re-confirmed for this phase's new files)
# ===========================================================================


def test_claim_ledger_functions_are_synchronous_and_side_effect_free_of_asyncio():
    """Cheap, direct confirmation alongside test_package_isolation.py's
    static check: every claim-ledger call here ran to completion above with
    no event loop involved."""
    core = BackspaceCore()
    claim = core.register_claim(Claim(text="x", claim_id="C1"))
    core.mark_claim_spoken("C1")
    assert claim.status is ClaimStatus.SPOKEN