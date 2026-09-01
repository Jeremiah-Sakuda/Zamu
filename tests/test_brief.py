"""The brief must be worth reading and must never overstate."""

from __future__ import annotations

from datetime import timedelta

from tests import factories as f
from zamu.core.brief import build_brief
from zamu.core.clock import FixedClock
from zamu.core.fill import CoverageService
from zamu.core.models import ActionClass
from zamu.infra.notify import OutboxNotifier


def build(grants=(ActionClass.SEND_ASK, ActionClass.WRITE_ROSTER), people=None, duties=None):
    roster = f.roster(
        people=people if people is not None else (f.MARCUS, f.AMARA, f.DEVON),
        duties=duties if duties is not None else (f.THURSDAY_GAP,),
        grants=tuple(f.grant(g) for g in grants),
    )
    store = f.store_with(roster)
    clock = FixedClock(f.NOW)
    return CoverageService(store, clock, OutboxNotifier(), base_url="https://t"), store, clock


def test_a_quiet_org_produces_one_line_and_stops():
    covered = f.duty(
        "dut_ok",
        f.local(2026, 9, 6, 18),
        assigned_person_id=f.AMARA.id,
        confirmed_at=f.NOW - timedelta(days=1),
    )
    _, store, _ = build(duties=(covered,))
    brief = build_brief(store, f.ORG_ID, f.NOW)

    assert brief.is_quiet
    assert not brief.needs_human
    assert brief.to_text().endswith("Coverage is holding.")


def test_a_verified_fill_appears_under_filled_with_its_rule():
    service, store, _ = build()
    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    service.record_response(store.get_ask(f.ORG_ID, asked.ask_id).token, accept=True)

    brief = build_brief(store, f.ORG_ID, f.NOW)
    assert len(brief.filled) == 1
    assert "verified by re-read" in brief.filled[0].detail
    assert brief.filled[0].policy_rule == "R10-explicit-acceptance"
    assert not brief.needs_human


def test_an_open_ask_is_shown_as_waiting_rather_than_assumed_answered():
    service, store, _ = build()
    service.ask_next(f.ORG_ID, "dut_thursday")

    brief = build_brief(store, f.ORG_ID, f.NOW)
    assert len(brief.waiting) == 1
    # The duty is 33 hours out, inside the urgent threshold, so the window is 90
    # minutes rather than the usual six hours.
    assert "Moving on in about 2h" in brief.waiting[0].detail
    assert brief.filled == ()


def test_an_unfillable_gap_is_a_decision_for_the_coordinator():
    service, store, _ = build(people=(f.SOFIA,))
    service.ask_next(f.ORG_ID, "dut_thursday")

    brief = build_brief(store, f.ORG_ID, f.NOW)
    assert brief.needs_human
    assert len(brief.needs_decision) == 1
    assert "uncovered" in brief.needs_decision[0].headline
    detail = brief.needs_decision[0].detail
    assert "needs a human" in detail


def test_refusals_are_grouped_by_rule_so_one_missing_grant_reads_as_one_problem():
    duties = (
        f.duty("dut_a", f.local(2026, 9, 5, 18)),
        f.duty("dut_b", f.local(2026, 9, 6, 18)),
        f.duty("dut_c", f.local(2026, 9, 7, 18)),
    )
    service, store, _ = build(grants=(ActionClass.DRAFT_ASK,), duties=duties)
    service.sweep(f.ORG_ID)

    brief = build_brief(store, f.ORG_ID, f.NOW)
    assert len(brief.not_allowed) == 1
    assert brief.not_allowed[0].policy_rule == "R3-no-grant"
    assert "Send an ask" in brief.not_allowed[0].headline
    assert brief.needs_human


def test_the_fairness_note_names_who_is_carrying_the_most():
    heavy = (
        f.duty("dut_h1", f.local(2026, 8, 15, 9), hours=8.0, assigned_person_id=f.AMARA.id),
        f.duty("dut_h2", f.local(2026, 8, 22, 9), hours=8.0, assigned_person_id=f.AMARA.id),
        f.THURSDAY_GAP,
    )
    _, store, _ = build(duties=heavy)
    brief = build_brief(store, f.ORG_ID, f.NOW)

    assert "Amara Okonkwo is carrying the most" in brief.fairness_note
    assert "asking them last" in brief.fairness_note


def test_the_fairness_note_stays_quiet_when_load_is_even():
    _, store, _ = build()
    assert build_brief(store, f.ORG_ID, f.NOW).fairness_note == ""


def test_the_brief_reads_as_prose_with_needs_you_first():
    service, store, _ = build(grants=(ActionClass.DRAFT_ASK,))
    service.ask_next(f.ORG_ID, "dut_thursday")

    text = build_brief(store, f.ORG_ID, f.NOW).to_text()
    assert text.index("Zamu was not allowed to:") < len(text)
    assert "R3-no-grant" in text
    # No urgency theatre and no scores.
    for banned in ("!", "urgent", "ASAP", "score", "streak"):
        assert banned.lower() not in text.lower()


def test_the_window_excludes_older_activity():
    service, store, clock = build()
    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    service.record_response(store.get_ask(f.ORG_ID, asked.ask_id).token, accept=True)

    clock.advance(timedelta(days=3).total_seconds())
    brief = build_brief(store, f.ORG_ID, clock.now())
    assert brief.filled == ()


def test_the_brief_serialises_for_the_console():
    service, store, _ = build()
    service.ask_next(f.ORG_ID, "dut_thursday")
    payload = build_brief(store, f.ORG_ID, f.NOW).as_dict()

    assert payload["org_name"] == "Riverside Food Bank"
    assert isinstance(payload["waiting"], list)
    assert payload["needs_human"] is False


def test_a_gap_zamu_can_still_work_on_stays_out_of_the_brief():
    """Telling a coordinator about work already in hand is the interruption this
    product exists to remove. The coverage board shows it; the brief stays quiet."""
    service, store, _ = build()
    brief = build_brief(store, f.ORG_ID, f.NOW)

    assert brief.needs_decision == ()
    assert not brief.needs_human
    assert service.rank_for(f.ORG_ID, "dut_thursday").has_candidates


def test_the_brief_escalates_once_the_last_candidate_has_been_asked():
    service, store, _ = build(people=(f.MARCUS,))
    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    service.record_response(store.get_ask(f.ORG_ID, asked.ask_id).token, accept=False)

    brief = build_brief(store, f.ORG_ID, f.NOW)
    assert brief.needs_human
    assert "has now been asked" in brief.needs_decision[0].detail
