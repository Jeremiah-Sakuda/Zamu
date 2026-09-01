"""Deriving how covered a duty actually is.

The honest answer is often "unknown", and this module is willing to say so. A duty
someone accepted three weeks ago and has not confirmed since is not the same thing
as a duty someone confirmed this morning, and colouring both green is how coverage
software loses the trust of the person relying on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from zamu.core.clock import utc
from zamu.core.models import CoverageState, Duty, Org, Roster


@dataclass(frozen=True, slots=True)
class CoverageAssessment:
    """A coverage state plus the single sentence that justifies it."""

    duty_id: str
    state: CoverageState
    reason: str
    assigned_person_id: str | None = None

    @property
    def needs_filling(self) -> bool:
        return self.state in (CoverageState.UNCOVERED, CoverageState.UNKNOWN)


def assess_duty(
    duty: Duty, org: Org, now: datetime, roster: Roster | None = None
) -> CoverageAssessment:
    """Work out the coverage state of a single duty.

    Order matters. Cancellation beats everything; an unassigned duty is uncovered;
    an assignment to somebody who has left the organisation is uncovered rather than
    covered; and a stale, unreconfirmed assignment degrades to at-risk.
    """
    moment = utc(now)

    if duty.cancelled:
        return CoverageAssessment(duty.id, CoverageState.COVERED, "This duty was cancelled.")

    if duty.assigned_person_id is None:
        return CoverageAssessment(
            duty.id, CoverageState.UNCOVERED, "Nobody is assigned to this duty."
        )

    person = roster.person(duty.assigned_person_id) if roster is not None else None

    if roster is not None and person is None:
        return CoverageAssessment(
            duty.id,
            CoverageState.UNCOVERED,
            "The assigned person is no longer on this roster.",
            duty.assigned_person_id,
        )

    if person is not None and not person.active:
        return CoverageAssessment(
            duty.id,
            CoverageState.UNCOVERED,
            f"{person.name} is no longer active in this organization.",
            duty.assigned_person_id,
        )

    if person is not None and person.is_blacked_out(duty.window):
        return CoverageAssessment(
            duty.id,
            CoverageState.AT_RISK,
            f"{person.name} has marked this time unavailable since accepting.",
            duty.assigned_person_id,
        )

    if duty.confirmed_at is None:
        return CoverageAssessment(
            duty.id,
            CoverageState.UNKNOWN,
            "Someone is assigned but has never confirmed.",
            duty.assigned_person_id,
        )

    age = moment - utc(duty.confirmed_at)
    if age > org.stale_confirmation_after and duty.start > moment:
        days = int(age.days)
        name = person.name if person else "The assigned volunteer"
        return CoverageAssessment(
            duty.id,
            CoverageState.AT_RISK,
            f"{name} accepted {days} days ago and has not confirmed since.",
            duty.assigned_person_id,
        )

    name = person.name if person else "Someone"
    return CoverageAssessment(
        duty.id,
        CoverageState.COVERED,
        f"{name} confirmed and is expected.",
        duty.assigned_person_id,
    )


def assess_roster(roster: Roster, now: datetime) -> tuple[CoverageAssessment, ...]:
    """Assess every duty on a roster, in duty start order."""
    ordered = sorted(roster.duties, key=lambda d: (d.window.start, d.id))
    return tuple(assess_duty(d, roster.org, now, roster) for d in ordered)


def gaps(
    roster: Roster, now: datetime, horizon_days: int | None = None
) -> tuple[CoverageAssessment, ...]:
    """Duties that need a human found for them, soonest first.

    Past duties are excluded: Zamu cannot fill yesterday, and pretending otherwise
    fills the coordinator's brief with noise.
    """
    moment = utc(now)
    out = []
    for assessment in assess_roster(roster, now):
        if not assessment.needs_filling:
            continue
        duty = roster.duty(assessment.duty_id)
        if duty is None or duty.cancelled or duty.start <= moment:
            continue
        if horizon_days is not None and (duty.start - moment).days > horizon_days:
            continue
        out.append(assessment)
    return tuple(out)


def coverage_summary(roster: Roster, now: datetime) -> dict[str, int]:
    """Counts by state, for the header of the coordinator console."""
    counts = dict.fromkeys((s.value for s in CoverageState), 0)
    for assessment in assess_roster(roster, now):
        counts[assessment.state.value] += 1
    return counts
