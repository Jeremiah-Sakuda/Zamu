"""Who can genuinely do this duty.

Every check here returns a reason, not just a boolean, because the coordinator's
first question about any ranking is "why isn't X on this list?" and an agent that
cannot answer that question will not be trusted with the next decision.

Note the separation that runs through the whole product: this module decides
*capability and courtesy* — is this person trained, free, not over-asked. It does
not decide *permission*. Permission lives in authority.py and is enforced against
stored grants, so no amount of eligibility can conjure the right to send a message.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zamu.core.clock import utc
from zamu.core.fairness import ask_budget_remaining
from zamu.core.models import (
    REASON_SENTENCES,
    ActionClass,
    AskState,
    DisqualifyingReason,
    Duty,
    FairnessRecord,
    Org,
    Person,
    Roster,
)

#: How far ahead to search for a moment outside a person's quiet hours.
_QUIET_HOURS_SEARCH_HORIZON = timedelta(hours=36)
_QUIET_HOURS_STEP = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class Eligibility:
    """Whether one person can take one duty, and every reason they cannot."""

    person_id: str
    duty_id: str
    eligible: bool
    reasons: tuple[DisqualifyingReason, ...] = ()
    contactable_from: datetime | None = None
    """Earliest moment Zamu may contact this person, once quiet hours are respected."""

    asks_remaining: int = 0

    def explain(self, person_name: str) -> str:
        """One sentence a coordinator can read without a legend."""
        if self.eligible:
            return f"{person_name} is eligible."
        parts = [REASON_SENTENCES[r] for r in self.reasons]
        if len(parts) == 1:
            return f"{person_name} {parts[0]}."
        return f"{person_name} {', '.join(parts[:-1])}, and {parts[-1]}."


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def next_contactable_moment(person: Person, now: datetime) -> datetime:
    """The first instant at or after `now` when this person is outside quiet hours.

    Stepped rather than solved analytically so that a quiet-hours window which wraps
    midnight, and one that shifts under DST, both behave without special cases.
    """
    if not person.quiet_hours.enabled:
        return utc(now)

    tz = _zone(person.timezone)
    cursor = utc(now)
    horizon = cursor + _QUIET_HOURS_SEARCH_HORIZON
    while cursor < horizon:
        if not person.quiet_hours.covers(cursor.astimezone(tz).time()):
            return cursor
        cursor += _QUIET_HOURS_STEP
    return horizon


def evaluate(
    person: Person,
    duty: Duty,
    roster: Roster,
    record: FairnessRecord,
    now: datetime,
    *,
    for_action: ActionClass = ActionClass.SEND_ASK,
) -> Eligibility:
    """Assess one person against one duty.

    `for_action` matters: opting in to being contacted is required before Zamu may
    send an ask, but a coordinator may always be handed a draft to send themselves,
    so a person who never opted in is still a valid DRAFT_ASK candidate.
    """
    org: Org = roster.org
    moment = utc(now)
    reasons: list[DisqualifyingReason] = []

    if not person.active:
        reasons.append(DisqualifyingReason.INACTIVE)

    if duty.assigned_person_id == person.id:
        reasons.append(DisqualifyingReason.ALREADY_ASSIGNED)

    required = duty.required_qualification
    if required and required not in person.qualifications:
        reasons.append(DisqualifyingReason.MISSING_QUALIFICATION)

    if person.is_blacked_out(duty.window):
        reasons.append(DisqualifyingReason.BLACKOUT)

    for other in roster.duties_for(person.id):
        if other.id == duty.id or other.cancelled:
            continue
        if other.window.overlaps(duty.window):
            reasons.append(DisqualifyingReason.DOUBLE_BOOKED)
            break

    contactable_from = next_contactable_moment(person, moment)

    notice_deadline = duty.start - duty.min_notice
    if moment > notice_deadline:
        reasons.append(DisqualifyingReason.INSUFFICIENT_NOTICE)
    elif contactable_from > notice_deadline:
        reasons.append(DisqualifyingReason.QUIET_HOURS_BLOCK_NOTICE)

    for ask in roster.asks_for_person(person.id):
        if ask.duty_id == duty.id and ask.state in (AskState.DECLINED, AskState.WITHDRAWN):
            reasons.append(DisqualifyingReason.DECLINED_THIS_DUTY)
            break

    for ask in roster.asks_for_person(person.id):
        if ask.state.is_open and not ask.is_expired(moment):
            reasons.append(DisqualifyingReason.OPEN_ASK_ELSEWHERE)
            break

    remaining = ask_budget_remaining(record, org, moment, roster)
    if remaining <= 0:
        reasons.append(DisqualifyingReason.ASK_BUDGET_EXHAUSTED)

    if for_action >= ActionClass.SEND_ASK and not person.is_opted_in(ActionClass.SEND_ASK):
        reasons.append(DisqualifyingReason.NOT_OPTED_IN)

    # Preserve declaration order for stable, readable explanations.
    ordered = tuple(sorted(set(reasons), key=list(DisqualifyingReason).index))

    return Eligibility(
        person_id=person.id,
        duty_id=duty.id,
        eligible=not ordered,
        reasons=ordered,
        contactable_from=contactable_from,
        asks_remaining=remaining,
    )


def evaluate_all(
    duty: Duty,
    roster: Roster,
    records: dict[str, FairnessRecord],
    now: datetime,
    *,
    for_action: ActionClass = ActionClass.SEND_ASK,
) -> dict[str, Eligibility]:
    """Assess every person on the roster against one duty."""
    out: dict[str, Eligibility] = {}
    for person in roster.people:
        record = records.get(person.id)
        if record is None:
            continue
        out[person.id] = evaluate(person, duty, roster, record, now, for_action=for_action)
    return out
