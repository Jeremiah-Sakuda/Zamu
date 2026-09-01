"""Every disqualification must be true, and must be explainable in one sentence."""

from __future__ import annotations

from datetime import time, timedelta

from tests import factories as f
from zamu.core.eligibility import evaluate, evaluate_all, next_contactable_moment
from zamu.core.fairness import build_records
from zamu.core.models import ActionClass, AskState, DisqualifyingReason, QuietHours


def _evaluate(person, duty, roster, *, now=None, for_action=ActionClass.SEND_ASK):
    moment = now or f.NOW
    records = build_records(roster, moment)
    return evaluate(person, duty, roster, records[person.id], moment, for_action=for_action)


def test_a_qualified_free_opted_in_person_is_eligible():
    roster = f.roster(people=(f.MARCUS,), duties=(f.THURSDAY_GAP,))
    result = _evaluate(f.MARCUS, f.THURSDAY_GAP, roster)
    assert result.eligible
    assert result.explain("Marcus Tran") == "Marcus Tran is eligible."


def test_missing_qualification_disqualifies():
    roster = f.roster(people=(f.SOFIA,), duties=(f.THURSDAY_GAP,))
    result = _evaluate(f.SOFIA, f.THURSDAY_GAP, roster)
    assert DisqualifyingReason.MISSING_QUALIFICATION in result.reasons
    assert "not trained for this role" in result.explain("Sofia Marchetti")


def test_blackout_disqualifies():
    blocked = f.person(
        "per_blocked",
        "Blocked Volunteer",
        qualifications=("food-safety",),
        blackouts=(f.THURSDAY_GAP.window,),
    )
    roster = f.roster(people=(blocked,), duties=(f.THURSDAY_GAP,))
    result = _evaluate(blocked, f.THURSDAY_GAP, roster)
    assert DisqualifyingReason.BLACKOUT in result.reasons


def test_overlapping_duty_disqualifies():
    clash = f.duty(
        "dut_clash",
        f.local(2026, 9, 4, 19),
        hours=2.0,
        assigned_person_id=f.MARCUS.id,
    )
    roster = f.roster(people=(f.MARCUS,), duties=(f.THURSDAY_GAP, clash))
    result = _evaluate(f.MARCUS, f.THURSDAY_GAP, roster)
    assert DisqualifyingReason.DOUBLE_BOOKED in result.reasons


def test_adjacent_but_not_overlapping_duty_is_fine():
    adjacent = f.duty(
        "dut_after", f.local(2026, 9, 4, 20), hours=2.0, assigned_person_id=f.MARCUS.id
    )
    roster = f.roster(people=(f.MARCUS,), duties=(f.THURSDAY_GAP, adjacent))
    assert _evaluate(f.MARCUS, f.THURSDAY_GAP, roster).eligible


def test_insufficient_notice_disqualifies():
    imminent = f.duty("dut_now", f.NOW + timedelta(hours=2), min_notice=timedelta(hours=12))
    roster = f.roster(people=(f.MARCUS,), duties=(imminent,))
    result = _evaluate(f.MARCUS, imminent, roster)
    assert DisqualifyingReason.INSUFFICIENT_NOTICE in result.reasons


def test_already_assigned_person_is_not_a_candidate_for_their_own_duty():
    assigned = f.duty("dut_mine", f.local(2026, 9, 6, 18), assigned_person_id=f.MARCUS.id)
    roster = f.roster(people=(f.MARCUS,), duties=(assigned,))
    result = _evaluate(f.MARCUS, assigned, roster)
    assert DisqualifyingReason.ALREADY_ASSIGNED in result.reasons


def test_declining_a_duty_removes_the_person_from_that_duty_only():
    other = f.duty("dut_other", f.local(2026, 9, 6, 18))
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP, other),
        asks=(
            f.ask(
                "ask_no",
                "dut_thursday",
                f.MARCUS.id,
                sent_at=f.NOW - timedelta(days=1),
                state=AskState.DECLINED,
            ),
        ),
    )
    assert (
        DisqualifyingReason.DECLINED_THIS_DUTY
        in _evaluate(f.MARCUS, f.THURSDAY_GAP, roster).reasons
    )
    assert _evaluate(f.MARCUS, other, roster).eligible


def test_an_open_ask_elsewhere_blocks_a_second_simultaneous_ask():
    """One ask at a time, per person as well as per duty. Two open questions is how
    a volunteer ends up double-booking themselves by accident."""
    other = f.duty("dut_other", f.local(2026, 9, 6, 18))
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP, other),
        asks=(f.ask("ask_open", "dut_other", f.MARCUS.id, sent_at=f.NOW - timedelta(hours=1)),),
    )
    assert (
        DisqualifyingReason.OPEN_ASK_ELSEWHERE
        in _evaluate(f.MARCUS, f.THURSDAY_GAP, roster).reasons
    )


def test_an_expired_ask_no_longer_blocks():
    other = f.duty("dut_other", f.local(2026, 9, 6, 18))
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP, other),
        asks=(
            f.ask(
                "ask_stale",
                "dut_other",
                f.MARCUS.id,
                sent_at=f.NOW - timedelta(days=2),
                expires_in=timedelta(hours=6),
            ),
        ),
    )
    assert _evaluate(f.MARCUS, f.THURSDAY_GAP, roster).eligible


def test_ask_budget_exhaustion_disqualifies():
    asks = tuple(
        f.ask(
            f"ask_{i}",
            "dut_other",
            f.MARCUS.id,
            sent_at=f.NOW - timedelta(days=i + 1),
            state=AskState.DECLINED,
        )
        for i in range(3)
    )
    roster = f.roster(people=(f.MARCUS,), duties=(f.THURSDAY_GAP,), asks=asks)
    result = _evaluate(f.MARCUS, f.THURSDAY_GAP, roster)
    assert DisqualifyingReason.ASK_BUDGET_EXHAUSTED in result.reasons
    assert result.asks_remaining == 0


def test_opt_in_is_required_to_send_but_not_to_draft():
    """Ben never opted in. Zamu may not message him, but the coordinator may."""
    roster = f.roster(people=(f.BEN,), duties=(f.THURSDAY_GAP,))
    sending = _evaluate(f.BEN, f.THURSDAY_GAP, roster, for_action=ActionClass.SEND_ASK)
    drafting = _evaluate(f.BEN, f.THURSDAY_GAP, roster, for_action=ActionClass.DRAFT_ASK)
    assert DisqualifyingReason.NOT_OPTED_IN in sending.reasons
    assert drafting.eligible


def test_inactive_person_is_disqualified():
    gone = f.person("per_gone", "Departed", qualifications=("food-safety",), active=False)
    roster = f.roster(people=(gone,), duties=(f.THURSDAY_GAP,))
    assert DisqualifyingReason.INACTIVE in _evaluate(gone, f.THURSDAY_GAP, roster).reasons


def test_quiet_hours_delay_contact_without_disqualifying_when_there_is_time():
    night_owl = f.person(
        "per_night",
        "Night Owl",
        qualifications=("food-safety",),
        quiet_hours=QuietHours(start=time(21, 0), end=time(8, 0)),
    )
    # 22:00 Chicago on the Wednesday: inside quiet hours, so contact waits for 08:00.
    night = f.local(2026, 9, 3, 22)
    # Four hours of required notice still leaves a 14:00 deadline, which 08:00 clears.
    relaxed = f.duty("dut_thursday", f.local(2026, 9, 4, 18), min_notice=timedelta(hours=4))
    roster = f.roster(people=(night_owl,), duties=(relaxed,))
    result = _evaluate(night_owl, relaxed, roster, now=night)
    assert result.eligible
    assert result.contactable_from == f.local(2026, 9, 4, 8)


def test_quiet_hours_disqualify_when_they_eat_the_notice_period():
    night_owl = f.person(
        "per_night",
        "Night Owl",
        qualifications=("food-safety",),
        quiet_hours=QuietHours(start=time(21, 0), end=time(8, 0)),
    )
    imminent = f.duty("dut_morning", f.local(2026, 9, 4, 9), min_notice=timedelta(hours=2))
    night = f.local(2026, 9, 3, 22)
    roster = f.roster(people=(night_owl,), duties=(imminent,))
    result = _evaluate(night_owl, imminent, roster, now=night)
    assert DisqualifyingReason.QUIET_HOURS_BLOCK_NOTICE in result.reasons


def test_next_contactable_moment_is_now_when_quiet_hours_are_off():
    always = f.person(
        "per_always",
        "Always Reachable",
        quiet_hours=QuietHours(enabled=False),
    )
    assert next_contactable_moment(always, f.NOW) == f.NOW


def test_multiple_reasons_are_joined_into_one_sentence():
    doubly = f.person("per_double", "Doubly Blocked", qualifications=(), active=False)
    roster = f.roster(people=(doubly,), duties=(f.THURSDAY_GAP,))
    sentence = _evaluate(doubly, f.THURSDAY_GAP, roster).explain("Doubly Blocked")
    assert sentence.count(",") >= 1
    assert " and " in sentence


def test_evaluate_all_covers_every_person():
    roster = f.roster(people=f.CAST, duties=(f.THURSDAY_GAP,))
    results = evaluate_all(f.THURSDAY_GAP, roster, build_records(roster, f.NOW), f.NOW)
    assert set(results) == {p.id for p in f.CAST}
    assert results[f.MARCUS.id].eligible
    assert not results[f.SOFIA.id].eligible
