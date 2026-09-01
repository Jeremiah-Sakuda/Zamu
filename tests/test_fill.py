"""The fill loop end to end, with no model anywhere in it."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from tests import factories as f
from zamu.core.clock import FixedClock
from zamu.core.coverage import assess_duty
from zamu.core.fill import CoverageService, Outcome, ResponseOutcome
from zamu.core.models import ActionClass, ActionResult, AskState, CoverageState
from zamu.infra.notify import OutboxNotifier


def build(*, grants=(ActionClass.SEND_ASK, ActionClass.WRITE_ROSTER), people=None, duties=None,
          asks=(), now=None):
    roster = f.roster(
        people=people if people is not None else (f.MARCUS, f.AMARA, f.DEVON),
        duties=duties if duties is not None else (f.THURSDAY_GAP,),
        asks=asks,
        grants=tuple(f.grant(g) for g in grants),
    )
    store = f.store_with(roster)
    clock = FixedClock(now or f.NOW)
    notifier = OutboxNotifier()
    service = CoverageService(store, clock, notifier, base_url="https://zamu.test")
    return service, store, clock, notifier


# -- the headline path -----------------------------------------------------------------


def test_a_gap_becomes_a_verified_fill():
    """The Sep 5 gate: seed a gap, run the loop, end with a verified assignment."""
    service, store, _, notifier = build()

    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    assert asked.outcome is Outcome.ASKED
    assert asked.person_id is not None
    assert asked.policy_rule == "R6-opted-in-and-in-hours"
    assert "re-read the roster" in asked.detail.lower()

    # Exactly one person heard from Zamu. Broadcast is the failure mode.
    assert len(notifier.sent) == 1
    assert notifier.sent[0][0].to_email.endswith("@example.org")

    ask = store.get_ask(f.ORG_ID, asked.ask_id)
    response = service.record_response(ask.token, accept=True)

    assert response.outcome is ResponseOutcome.ACCEPTED_AND_ASSIGNED
    assert response.verified

    duty = store.get_duty(f.ORG_ID, "dut_thursday")
    assert duty.assigned_person_id == asked.person_id
    roster = store.load_roster(f.ORG_ID)
    assert assess_duty(duty, roster.org, f.NOW, roster).state is CoverageState.COVERED


def test_the_fill_leaves_a_receipt_with_observed_state_beside_intent():
    service, store, _, _ = build()
    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    ask = store.get_ask(f.ORG_ID, asked.ask_id)
    service.record_response(ask.token, accept=True)

    receipts = service.ledger.recent(f.ORG_ID)
    write = next(r for r in receipts if r.action_class is ActionClass.WRITE_ROSTER)
    assert write.result is ActionResult.VERIFIED
    assert write.intended["assigned_person_id"] == asked.person_id
    assert write.observed["assigned_person_id"] == asked.person_id
    assert write.policy_rule == "R10-explicit-acceptance"
    assert write.executed_at is not None and write.verified_at is not None


def test_only_one_person_is_asked_at_a_time():
    service, _, _, notifier = build()
    first = service.ask_next(f.ORG_ID, "dut_thursday")
    second = service.ask_next(f.ORG_ID, "dut_thursday")

    assert first.outcome is Outcome.ASKED
    assert second.outcome is Outcome.WAITING
    assert second.person_id == first.person_id
    assert len(notifier.sent) == 1


def test_a_decline_advances_to_the_next_candidate():
    service, store, _, notifier = build()
    first = service.ask_next(f.ORG_ID, "dut_thursday")
    declined = service.record_response(store.get_ask(f.ORG_ID, first.ask_id).token, accept=False)
    assert declined.outcome is ResponseOutcome.DECLINED

    second = service.ask_next(f.ORG_ID, "dut_thursday")
    assert second.outcome is Outcome.ASKED
    assert second.person_id != first.person_id
    assert len(notifier.sent) == 2


def test_a_declined_person_is_never_asked_about_that_duty_again():
    service, store, _, _ = build(people=(f.MARCUS,))
    first = service.ask_next(f.ORG_ID, "dut_thursday")
    service.record_response(store.get_ask(f.ORG_ID, first.ask_id).token, accept=False)

    again = service.ask_next(f.ORG_ID, "dut_thursday")
    assert again.outcome is Outcome.NO_CANDIDATES
    assert again.needs_coordinator
    assert any("already declined" in reason for _, reason in again.excluded)


# -- expiry ----------------------------------------------------------------------------


def test_an_unanswered_ask_expires_and_the_sweep_asks_someone_else():
    """Silence is an answer with a deadline."""
    service, store, clock, notifier = build()
    first = service.ask_next(f.ORG_ID, "dut_thursday")

    clock.advance(timedelta(hours=7).total_seconds())
    result = service.sweep(f.ORG_ID)

    assert first.ask_id in result.expired
    assert store.get_ask(f.ORG_ID, first.ask_id).state is AskState.EXPIRED
    assert [o.outcome for o in result.outcomes] == [Outcome.ASKED]
    assert result.outcomes[0].person_id != first.person_id
    assert len(notifier.sent) == 2


def test_the_ask_window_shortens_when_the_duty_is_imminent():
    soon = f.duty("dut_soon", f.NOW + timedelta(hours=30), min_notice=timedelta(hours=2))
    service, _, _, _ = build(duties=(soon,))
    asked = service.ask_next(f.ORG_ID, "dut_soon")
    assert asked.expires_at == f.NOW + timedelta(minutes=90)


def test_the_ask_window_is_generous_when_there_is_time():
    later = f.duty("dut_later", f.NOW + timedelta(days=9), min_notice=timedelta(hours=2))
    service, _, _, _ = build(duties=(later,))
    asked = service.ask_next(f.ORG_ID, "dut_later")
    assert asked.expires_at == f.NOW + timedelta(hours=6)


def test_answering_after_expiry_is_refused_rather_than_silently_accepted():
    service, store, clock, _ = build()
    first = service.ask_next(f.ORG_ID, "dut_thursday")
    token = store.get_ask(f.ORG_ID, first.ask_id).token

    clock.advance(timedelta(hours=7).total_seconds())
    response = service.record_response(token, accept=True)

    assert response.outcome is ResponseOutcome.EXPIRED
    assert store.get_duty(f.ORG_ID, "dut_thursday").assigned_person_id is None


# -- authority in the loop -------------------------------------------------------------


def test_without_a_send_grant_zamu_drafts_instead_of_sending():
    """The demo's counterfactual. Same intelligence, different authority."""
    with_send, _, _, sent_notifier = build()
    full = with_send.ask_next(f.ORG_ID, "dut_thursday")

    drafting, store, _, notifier = build(grants=(ActionClass.DRAFT_ASK,))
    drafted = drafting.ask_next(f.ORG_ID, "dut_thursday")

    assert full.outcome is Outcome.ASKED
    assert drafted.outcome is Outcome.DRAFTED
    assert drafted.person_id == full.person_id  # identical reasoning
    assert drafted.needs_coordinator
    assert notifier.sent == []  # and nothing left the system
    assert "Can you cover" in drafted.draft_text
    assert store.get_ask(f.ORG_ID, drafted.ask_id).drafted_only


def test_a_refused_send_is_recorded_before_the_draft_is_offered():
    service, _, _, _ = build(grants=(ActionClass.DRAFT_ASK,))
    service.ask_next(f.ORG_ID, "dut_thursday")

    blocked = [r for r in service.ledger.recent(f.ORG_ID) if r.result is ActionResult.BLOCKED]
    assert len(blocked) == 1
    assert blocked[0].action_class is ActionClass.SEND_ASK
    assert blocked[0].policy_rule == "R3-no-grant"


def test_with_no_grants_at_all_zamu_hands_over_without_acting():
    service, _, _, notifier = build(grants=())
    result = service.ask_next(f.ORG_ID, "dut_thursday")

    assert result.outcome is Outcome.BLOCKED
    assert result.needs_coordinator
    assert result.policy_rule == "R3-no-grant"
    assert notifier.sent == []


def test_an_acceptance_without_a_write_grant_stops_short_and_says_so():
    service, store, _, _ = build(grants=(ActionClass.SEND_ASK,))
    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    response = service.record_response(store.get_ask(f.ORG_ID, asked.ask_id).token, accept=True)

    assert response.outcome is ResponseOutcome.ACCEPTED_PENDING_COORDINATOR
    assert not response.verified
    assert store.get_duty(f.ORG_ID, "dut_thursday").assigned_person_id is None
    # The acceptance itself is still recorded; only the roster write was refused.
    assert store.get_ask(f.ORG_ID, asked.ask_id).state is AskState.ACCEPTED


def test_revoking_the_send_grant_widens_the_pool_to_people_who_never_opted_in():
    """Ben never opted in, so Zamu may not message him — but a human may."""
    service, _, _, _ = build(grants=(ActionClass.DRAFT_ASK,), people=(f.BEN,))
    result = service.ask_next(f.ORG_ID, "dut_thursday")
    assert result.outcome is Outcome.DRAFTED
    assert result.person_id == f.BEN.id


# -- idempotency and failure -----------------------------------------------------------


def test_tapping_accept_twice_writes_once():
    """Email clients prefetch links and people double-tap. Both must be harmless."""
    service, store, _, _ = build()
    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    token = store.get_ask(f.ORG_ID, asked.ask_id).token

    first = service.record_response(token, accept=True)
    second = service.record_response(token, accept=True)

    assert first.outcome is ResponseOutcome.ACCEPTED_AND_ASSIGNED
    assert second.outcome is ResponseOutcome.ALREADY_ANSWERED
    writes = [
        r for r in service.ledger.recent(f.ORG_ID) if r.action_class is ActionClass.WRITE_ROSTER
    ]
    assert len(writes) == 1


def test_an_unknown_token_is_rejected_politely():
    service, _, _, _ = build()
    result = service.record_response("not-a-real-token", accept=True)
    assert result.outcome is ResponseOutcome.UNKNOWN


def test_a_failed_delivery_does_not_leave_a_phantom_ask():
    """If the message never went out, the ask must not survive as though it had."""
    service, store, _, notifier = build()
    notifier.fail_next = "recipient rejected"

    result = service.ask_next(f.ORG_ID, "dut_thursday")

    assert result.outcome is Outcome.FAILED
    assert result.needs_coordinator
    assert store.get_ask(f.ORG_ID, result.ask_id).state is AskState.WITHDRAWN
    assert [a for a in store.list_asks(f.ORG_ID) if a.state.is_open] == []
    failed = [r for r in service.ledger.recent(f.ORG_ID) if r.result is ActionResult.FAILED]
    assert "recipient rejected" in failed[0].detail


def test_accepting_a_duty_someone_else_already_took_is_superseded():
    service, store, _, _ = build()
    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    token = store.get_ask(f.ORG_ID, asked.ask_id).token

    other = next(p for p in (f.MARCUS, f.AMARA, f.DEVON) if p.id != asked.person_id)
    duty = store.get_duty(f.ORG_ID, "dut_thursday")
    store.put_duty(duty.assigned_to(other.id, f.NOW))

    response = service.record_response(token, accept=True)
    assert response.outcome is ResponseOutcome.SUPERSEDED
    assert store.get_duty(f.ORG_ID, "dut_thursday").assigned_person_id == other.id


def test_other_open_asks_are_closed_once_a_duty_is_covered():
    stray = f.ask("ask_stray", "dut_thursday", f.DEVON.id, sent_at=f.NOW - timedelta(minutes=5))
    service, store, _, _ = build(asks=(stray,))

    accepted = replace(
        store.get_ask(f.ORG_ID, "ask_stray"), person_id=f.MARCUS.id, id="ask_win", token="tok-win"
    )
    store.put_ask(accepted)
    service.record_response("tok-win", accept=True)

    assert store.get_ask(f.ORG_ID, "ask_stray").state is AskState.SUPERSEDED


# -- covered and empty cases -----------------------------------------------------------


def test_a_covered_duty_is_left_alone():
    covered = f.duty(
        "dut_covered",
        f.local(2026, 9, 6, 18),
        assigned_person_id=f.AMARA.id,
        confirmed_at=f.NOW - timedelta(days=1),
    )
    service, _, _, notifier = build(duties=(covered,))
    result = service.ask_next(f.ORG_ID, "dut_covered")
    assert result.outcome is Outcome.ALREADY_COVERED
    assert notifier.sent == []


def test_no_eligible_candidate_escalates_with_the_reasons():
    service, _, _, _ = build(people=(f.SOFIA,))
    result = service.ask_next(f.ORG_ID, "dut_thursday")

    assert result.outcome is Outcome.NO_CANDIDATES
    assert result.needs_coordinator
    assert result.excluded == (
        ("Sofia Marchetti", "Sofia Marchetti is not trained for this role."),
    )


def test_a_sweep_advances_every_open_gap_by_exactly_one_ask():
    duties = (
        f.duty("dut_a", f.local(2026, 9, 5, 18)),
        f.duty("dut_b", f.local(2026, 9, 6, 18)),
    )
    service, _, _, notifier = build(duties=duties)
    result = service.sweep(f.ORG_ID)

    assert len(result.asked) == 2
    assert {o.duty_id for o in result.asked} == {"dut_a", "dut_b"}
    # Two duties, two people, two messages — and nobody asked twice at once.
    assert len({m.person_id for m, _ in notifier.sent}) == 2


def test_asking_about_an_unknown_duty_raises():
    from zamu.core.errors import NotFound

    service, _, _, _ = build()
    with pytest.raises(NotFound):
        service.ask_next(f.ORG_ID, "dut_imaginary")
