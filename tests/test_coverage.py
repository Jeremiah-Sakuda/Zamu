"""Coverage state is the one thing Zamu must never overstate."""

from __future__ import annotations

from datetime import timedelta

from tests import factories as f
from zamu.core.coverage import assess_duty, assess_roster, coverage_summary, gaps
from zamu.core.models import CoverageState


def test_unassigned_duty_is_uncovered():
    duty = f.duty("dut_a", f.local(2026, 9, 4, 18))
    result = assess_duty(duty, f.org(), f.NOW)
    assert result.state is CoverageState.UNCOVERED
    assert result.needs_filling


def test_recently_confirmed_duty_is_covered():
    duty = f.duty(
        "dut_a",
        f.local(2026, 9, 4, 18),
        assigned_person_id=f.AMARA.id,
        confirmed_at=f.NOW - timedelta(days=1),
    )
    roster = f.roster(people=(f.AMARA,), duties=(duty,))
    result = assess_duty(duty, roster.org, f.NOW, roster)
    assert result.state is CoverageState.COVERED
    assert "Amara" in result.reason


def test_assigned_but_never_confirmed_is_unknown_not_covered():
    """The whole point: an unconfirmed assignment is not a green cell."""
    duty = f.duty("dut_a", f.local(2026, 9, 4, 18), assigned_person_id=f.AMARA.id)
    roster = f.roster(people=(f.AMARA,), duties=(duty,))
    result = assess_duty(duty, roster.org, f.NOW, roster)
    assert result.state is CoverageState.UNKNOWN
    assert result.needs_filling


def test_stale_confirmation_degrades_to_at_risk():
    duty = f.duty(
        "dut_a",
        f.local(2026, 9, 20, 18),
        assigned_person_id=f.AMARA.id,
        confirmed_at=f.NOW - timedelta(days=21),
    )
    roster = f.roster(people=(f.AMARA,), duties=(duty,))
    result = assess_duty(duty, roster.org, f.NOW, roster)
    assert result.state is CoverageState.AT_RISK
    assert "21 days ago" in result.reason


def test_assignment_to_departed_person_is_uncovered():
    departed = f.person("per_gone", "Gone Away", qualifications=("food-safety",), active=False)
    duty = f.duty(
        "dut_a",
        f.local(2026, 9, 4, 18),
        assigned_person_id=departed.id,
        confirmed_at=f.NOW - timedelta(days=1),
    )
    roster = f.roster(people=(departed,), duties=(duty,))
    assert assess_duty(duty, roster.org, f.NOW, roster).state is CoverageState.UNCOVERED


def test_blackout_added_after_acceptance_makes_duty_at_risk():
    window = f.duty("dut_a", f.local(2026, 9, 4, 18)).window
    conflicted = f.person(
        "per_x", "Conflicted Volunteer", qualifications=("food-safety",), blackouts=(window,)
    )
    duty = f.duty(
        "dut_a",
        f.local(2026, 9, 4, 18),
        assigned_person_id=conflicted.id,
        confirmed_at=f.NOW - timedelta(days=1),
    )
    roster = f.roster(people=(conflicted,), duties=(duty,))
    assert assess_duty(duty, roster.org, f.NOW, roster).state is CoverageState.AT_RISK


def test_gaps_exclude_the_past_and_sort_soonest_first():
    past = f.duty("dut_past", f.local(2026, 9, 1, 18))
    soon = f.duty("dut_soon", f.local(2026, 9, 4, 18))
    later = f.duty("dut_later", f.local(2026, 9, 9, 18))
    roster = f.roster(duties=(later, past, soon))

    found = gaps(roster, f.NOW)
    assert [g.duty_id for g in found] == ["dut_soon", "dut_later"]


def test_gaps_respect_a_horizon():
    soon = f.duty("dut_soon", f.local(2026, 9, 4, 18))
    far = f.duty("dut_far", f.local(2026, 10, 20, 18))
    roster = f.roster(duties=(soon, far))
    assert [g.duty_id for g in gaps(roster, f.NOW, horizon_days=14)] == ["dut_soon"]


def test_cancelled_duty_never_needs_filling():
    duty = f.duty("dut_a", f.local(2026, 9, 4, 18), cancelled=True)
    roster = f.roster(duties=(duty,))
    assert gaps(roster, f.NOW) == ()


def test_summary_counts_every_state():
    roster = f.roster(
        people=(f.AMARA,),
        duties=(
            f.duty("dut_1", f.local(2026, 9, 4, 18)),
            f.duty(
                "dut_2",
                f.local(2026, 9, 5, 18),
                assigned_person_id=f.AMARA.id,
                confirmed_at=f.NOW,
            ),
            f.duty("dut_3", f.local(2026, 9, 6, 18), assigned_person_id=f.AMARA.id),
        ),
    )
    summary = coverage_summary(roster, f.NOW)
    assert summary["uncovered"] == 1
    assert summary["covered"] == 1
    assert summary["unknown"] == 1


def test_assess_roster_is_ordered_by_start():
    roster = f.roster(
        duties=(
            f.duty("dut_b", f.local(2026, 9, 6, 18)),
            f.duty("dut_a", f.local(2026, 9, 4, 18)),
        )
    )
    assert [a.duty_id for a in assess_roster(roster, f.NOW)] == ["dut_a", "dut_b"]
