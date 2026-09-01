"""Fairness is only humane if the arithmetic is right."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests import factories as f
from zamu.core.fairness import (
    ask_budget_remaining,
    build_records,
    cohort_mean_load,
    debt_scale,
    describe_load,
    fairness_debt,
    normalised_debt,
    unsociable_hours,
)
from zamu.core.models import AskState, TimeWindow


@pytest.mark.parametrize(
    ("start_hour", "hours", "day", "expected"),
    [
        (9, 4.0, 3, 0.0),  # Thursday 9am-1pm: entirely ordinary
        (18, 2.0, 3, 2.0),  # Thursday 6pm-8pm: entirely after hours
        (16, 4.0, 3, 2.0),  # Thursday 4pm-8pm: half after hours
        (9, 4.0, 5, 4.0),  # Saturday: all of it counts
        (6, 3.0, 3, 2.0),  # Thursday 6am-9am: 6-8 is early, 8-9 is not
    ],
)
def test_unsociable_hours_across_the_week(start_hour, hours, day, expected):
    start = f.local(2026, 9, day, start_hour)
    window = TimeWindow(start, start + timedelta(hours=hours))
    assert unsociable_hours(window, "America/Chicago") == pytest.approx(expected, abs=0.26)


def test_unsociable_hours_never_exceed_the_window():
    start = f.local(2026, 9, 5, 22)
    window = TimeWindow(start, start + timedelta(hours=10))
    assert unsociable_hours(window, "America/Chicago") <= window.hours


def test_weighted_load_prices_unsociable_hours_higher():
    roster = f.roster(
        people=(f.AMARA,),
        duties=(
            f.duty("dut_1", f.local(2026, 8, 20, 9), hours=4.0, assigned_person_id=f.AMARA.id),
            f.duty("dut_2", f.local(2026, 8, 22, 9), hours=4.0, assigned_person_id=f.AMARA.id),
        ),
    )
    record = build_records(roster, f.NOW)[f.AMARA.id]
    assert record.hours_carried == pytest.approx(8.0)
    # dut_2 falls on a Saturday, so four of the eight hours are unsociable.
    assert record.unsociable_hours_carried == pytest.approx(4.0, abs=0.26)
    assert record.weighted_load(1.5) > record.hours_carried


def test_future_duties_do_not_count_as_carried():
    """A promise is not a payment. Accepting four shifts next month must not
    exclude somebody from being asked for the next six weeks."""
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.duty("dut_future", f.local(2026, 9, 20, 18), assigned_person_id=f.MARCUS.id),),
    )
    record = build_records(roster, f.NOW)[f.MARCUS.id]
    assert record.shifts_carried == 0
    assert record.hours_carried == 0.0


def test_duties_outside_the_window_do_not_count():
    long_ago = f.local(2026, 6, 1, 9)
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.duty("dut_old", long_ago, assigned_person_id=f.MARCUS.id),),
    )
    assert build_records(roster, f.NOW)[f.MARCUS.id].shifts_carried == 0


def test_cancelled_duties_do_not_count():
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(
            f.duty(
                "dut_c",
                f.local(2026, 8, 20, 9),
                assigned_person_id=f.MARCUS.id,
                cancelled=True,
            ),
        ),
    )
    assert build_records(roster, f.NOW)[f.MARCUS.id].shifts_carried == 0


def test_ask_history_is_counted_and_drafts_are_not():
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP,),
        asks=(
            f.ask(
                "ask_1",
                "dut_thursday",
                f.MARCUS.id,
                sent_at=f.NOW - timedelta(days=10),
                state=AskState.ACCEPTED,
            ),
            f.ask(
                "ask_2",
                "dut_thursday",
                f.MARCUS.id,
                sent_at=f.NOW - timedelta(days=5),
                state=AskState.DECLINED,
            ),
            f.ask(
                "ask_3",
                "dut_thursday",
                f.MARCUS.id,
                sent_at=f.NOW - timedelta(days=2),
                state=AskState.SENT,
                drafted_only=True,
            ),
        ),
    )
    record = build_records(roster, f.NOW)[f.MARCUS.id]
    assert record.asks_sent == 2
    assert record.accepts == 1
    assert record.declines == 1
    assert record.acceptance_rate == pytest.approx(0.5)
    assert record.last_asked_at == f.NOW - timedelta(days=5)


def test_rates_are_none_when_never_asked():
    roster = f.roster(people=(f.MARCUS,))
    record = build_records(roster, f.NOW)[f.MARCUS.id]
    assert record.acceptance_rate is None
    assert record.response_rate is None


def test_debt_is_positive_for_the_under_carried():
    roster = f.roster(
        people=(f.AMARA, f.MARCUS),
        duties=(
            f.duty("dut_1", f.local(2026, 8, 20, 9), hours=4.0, assigned_person_id=f.AMARA.id),
            f.duty("dut_2", f.local(2026, 8, 24, 9), hours=4.0, assigned_person_id=f.AMARA.id),
        ),
    )
    records = build_records(roster, f.NOW)
    mean = cohort_mean_load(records, roster.org)
    assert fairness_debt(records[f.MARCUS.id], roster.org, mean) > 0
    assert fairness_debt(records[f.AMARA.id], roster.org, mean) < 0


def test_normalised_debt_is_neutral_when_everyone_is_equal():
    assert normalised_debt(0.0, 0.0) == 0.5


def test_normalised_debt_is_bounded():
    assert normalised_debt(999.0, 1.0) == 1.0
    assert normalised_debt(-999.0, 1.0) == 0.0


def test_ask_budget_counts_only_the_last_seven_days():
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP,),
        asks=(
            f.ask("ask_old", "dut_thursday", f.MARCUS.id, sent_at=f.NOW - timedelta(days=9)),
            f.ask("ask_new", "dut_thursday", f.MARCUS.id, sent_at=f.NOW - timedelta(days=1)),
        ),
    )
    record = build_records(roster, f.NOW)[f.MARCUS.id]
    assert ask_budget_remaining(record, roster.org, f.NOW, roster) == 2


def test_ask_budget_reaches_zero_and_does_not_go_negative():
    asks = tuple(
        f.ask(f"ask_{i}", "dut_thursday", f.MARCUS.id, sent_at=f.NOW - timedelta(hours=i + 1))
        for i in range(5)
    )
    roster = f.roster(people=(f.MARCUS,), duties=(f.THURSDAY_GAP,), asks=asks)
    record = build_records(roster, f.NOW)[f.MARCUS.id]
    assert ask_budget_remaining(record, roster.org, f.NOW, roster) == 0


def test_describe_load_is_a_readable_sentence():
    roster = f.roster(people=(f.MARCUS,))
    record = build_records(roster, f.NOW)[f.MARCUS.id]
    assert describe_load(record, roster.org, 0.0) == "has carried nothing in 6 weeks"


def test_cohort_mean_ignores_people_outside_the_cohort():
    roster = f.roster(
        people=(f.AMARA, f.MARCUS),
        duties=(
            f.duty("dut_1", f.local(2026, 8, 20, 9), hours=4.0, assigned_person_id=f.AMARA.id),
        ),
    )
    records = build_records(roster, f.NOW)
    assert cohort_mean_load(records, roster.org, {f.MARCUS.id}) == 0.0
    assert cohort_mean_load(records, roster.org, {f.AMARA.id}) == pytest.approx(4.0)


def test_one_heavy_carrier_does_not_flatten_everybody_else():
    """The bug this scale exists to prevent.

    With a max-based normaliser, a single person carrying far more than the rest
    stretches the denominator until ordinary differences collapse toward 0.5, fairness
    stops discriminating, and the lighter components decide the order. A lopsided
    roster is exactly the situation Zamu is for, so fairness has to keep working there.
    """
    lopsided = [40.0, -3.0, -10.0, -12.0]  # one outlier, three ordinary candidates
    scale = debt_scale(lopsided)

    ordinary = [normalised_debt(d, scale) for d in lopsided[1:]]
    assert max(ordinary) - min(ordinary) > 0.15

    # A max-based scale would have squashed the same three into near-indistinguishable.
    max_scale = max(abs(d) for d in lopsided)
    squashed = [normalised_debt(d, max_scale) for d in lopsided[1:]]
    assert max(squashed) - min(squashed) < 0.12


def test_the_scale_is_zero_when_everybody_matches():
    assert debt_scale([0.0, 0.0, 0.0]) == 0.0
    assert debt_scale([]) == 0.0
    assert normalised_debt(0.0, 0.0) == 0.5


def test_the_scale_grows_with_the_spread():
    assert debt_scale([-1.0, 1.0]) < debt_scale([-10.0, 10.0])
