"""Measuring who has actually carried what.

Volunteer organizations destroy their most reliable people. They do it without
malice: the coordinator asks whoever they trust, trust is built by saying yes, and
so saying yes is taxed until the person stops. The only way out is to make load a
fact the software knows, rather than a feeling the coordinator half-remembers.

Everything here is a pure function of the roster. Nothing is hand-editable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zamu.core.clock import utc
from zamu.core.models import (
    AskState,
    Duty,
    FairnessRecord,
    Org,
    Roster,
    TimeWindow,
)

#: Hours that count as ordinary daytime availability, in local time, Monday to Friday.
SOCIABLE_START_HOUR = 8
SOCIABLE_END_HOUR = 18


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def unsociable_hours(window: TimeWindow, timezone_name: str) -> float:
    """How many of a duty's hours fall outside ordinary weekday daytime.

    Computed by walking the window in quarter-hour steps rather than by closed-form
    arithmetic, because the closed form has to reason about DST transitions, weekend
    boundaries and multi-day shifts simultaneously, and gets one of them wrong.
    """
    tz = _zone(timezone_name)
    step = timedelta(minutes=15)
    step_hours = step.total_seconds() / 3600.0

    total = 0.0
    cursor = window.start
    while cursor < window.end:
        chunk = min(step, window.end - cursor)
        local = cursor.astimezone(tz)
        weekend = local.weekday() >= 5
        outside_daytime = not (SOCIABLE_START_HOUR <= local.hour < SOCIABLE_END_HOUR)
        if weekend or outside_daytime:
            total += chunk.total_seconds() / 3600.0
        cursor += step
        if chunk < step:
            break
    # Guard against float drift accumulating over long windows.
    return round(min(total, window.hours), 4) if step_hours else 0.0


def fairness_window(org: Org, now: datetime) -> TimeWindow:
    """The rolling period fairness is measured over. Six weeks by default."""
    end = utc(now)
    return TimeWindow(end - org.fairness_window, end)


def _counts_toward_load(duty: Duty, window: TimeWindow, now: datetime) -> bool:
    """A duty counts as carried once it has started and was not cancelled.

    Future duties deliberately do not count. Somebody who has accepted four shifts
    next month has not yet carried them, and treating a promise as a payment would
    let a single burst of generosity exclude a person from being asked for weeks.
    """
    if duty.cancelled:
        return False
    if duty.start > utc(now):
        return False
    return window.contains(duty.start)


def build_records(roster: Roster, now: datetime) -> dict[str, FairnessRecord]:
    """Derive one FairnessRecord per person on the roster."""
    window = fairness_window(roster.org, now)
    moment = utc(now)
    records: dict[str, FairnessRecord] = {}

    for person in roster.people:
        shifts = 0
        hours = 0.0
        unsociable = 0.0
        last_carried: datetime | None = None

        for duty in roster.duties_for(person.id):
            if not _counts_toward_load(duty, window, moment):
                continue
            shifts += 1
            hours += duty.hours
            unsociable += unsociable_hours(duty.window, roster.org.timezone)
            if last_carried is None or duty.start > last_carried:
                last_carried = duty.start

        asks_sent = 0
        declines = 0
        accepts = 0
        expirations = 0
        last_asked: datetime | None = None

        for ask in roster.asks_for_person(person.id):
            if ask.drafted_only:
                continue
            if not window.contains(ask.sent_at):
                continue
            asks_sent += 1
            if last_asked is None or ask.sent_at > last_asked:
                last_asked = ask.sent_at
            if ask.state is AskState.DECLINED:
                declines += 1
            elif ask.state is AskState.ACCEPTED:
                accepts += 1
            elif ask.state is AskState.EXPIRED:
                expirations += 1

        records[person.id] = FairnessRecord(
            person_id=person.id,
            window_start=window.start,
            window_end=window.end,
            shifts_carried=shifts,
            hours_carried=round(hours, 4),
            unsociable_hours_carried=round(unsociable, 4),
            asks_sent=asks_sent,
            declines=declines,
            accepts=accepts,
            expirations=expirations,
            last_asked_at=last_asked,
            last_carried_at=last_carried,
        )

    return records


def cohort_mean_load(records: dict[str, FairnessRecord], org: Org, cohort: set[str] | None = None) -> float:
    """Average weighted load across the people being compared.

    The cohort is normally the eligible candidates for one duty, not the whole
    organization: being compared against people who could never do this shift is
    not fairness, it is noise.
    """
    ids = cohort if cohort is not None else set(records)
    loads = [records[pid].weighted_load(org.unsociable_hour_weight) for pid in ids if pid in records]
    if not loads:
        return 0.0
    return sum(loads) / len(loads)


def fairness_debt(
    record: FairnessRecord,
    org: Org,
    mean_load: float,
) -> float:
    """How much this person is owed, relative to their cohort.

    Positive means under-carried and therefore first in line to be asked. Negative
    means over-carried and therefore protected. Zero means exactly average.
    """
    return round(mean_load - record.weighted_load(org.unsociable_hour_weight), 4)


def normalised_debt(debt: float, spread: float) -> float:
    """Squash a raw debt in hours into 0..1 for use as a ranking component.

    `spread` is the largest absolute debt in the cohort. When everybody has carried
    the same amount the spread is zero, every candidate scores 0.5, and the other
    ranking components decide the order — which is the correct behaviour, not a
    degenerate one.
    """
    if spread <= 0:
        return 0.5
    return max(0.0, min(1.0, 0.5 + (debt / (2.0 * spread))))


def ask_budget_remaining(record: FairnessRecord, org: Org, now: datetime, roster: Roster) -> int:
    """Asks this person may still receive this week.

    Deliberately measured over seven days rather than the six-week fairness window:
    the point is to stop a person being pestered, and pestering happens on a
    weekly rhythm.
    """
    moment = utc(now)
    week_start = moment - timedelta(days=7)
    recent = [
        a
        for a in roster.asks_for_person(record.person_id)
        if not a.drafted_only and utc(a.sent_at) >= week_start
    ]
    return max(0, org.max_asks_per_person_per_week - len(recent))


def describe_load(record: FairnessRecord, org: Org, mean_load: float) -> str:
    """One plain sentence about this person's load, for receipts and the console."""
    load = record.weighted_load(org.unsociable_hour_weight)
    weeks = max(1, int(org.fairness_window.days / 7))
    if record.shifts_carried == 0:
        return f"has carried nothing in {weeks} weeks"
    shift_word = "shift" if record.shifts_carried == 1 else "shifts"
    delta = mean_load - load
    if abs(delta) < 0.5:
        comparison = "about the team average"
    elif delta > 0:
        comparison = f"{abs(delta):.1f}h below the team average"
    else:
        comparison = f"{abs(delta):.1f}h above the team average"
    return (
        f"has carried {record.shifts_carried} {shift_word} "
        f"({record.hours_carried:.1f}h) in {weeks} weeks, {comparison}"
    )
