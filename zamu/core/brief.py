"""The handover brief: the only thing Zamu ever says to the coordinator unprompted.

Four questions, in this order, because it is the order a coordinator actually cares
about: what did you do, what is still open, what needs me, and what were you not
allowed to do.

There is no urgency theatre and there are no productivity scores. A brief that opens
with "3 shifts saved!" is optimising for the feeling of a useful assistant rather than
for the coordinator's next five minutes. If nothing needs a human, the brief says so
in one line and stops.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from zamu.core.clock import utc
from zamu.core.coverage import assess_roster
from zamu.core.fairness import build_records, cohort_mean_load
from zamu.core.messages import format_when
from zamu.core.models import (
    ActionClass,
    ActionRecord,
    ActionResult,
    AskState,
    CoverageState,
    Roster,
)
from zamu.core.ranking import rank
from zamu.core.store import Store


@dataclass(frozen=True, slots=True)
class Item:
    """One line of the brief, with the ids needed to act on it."""

    headline: str
    detail: str = ""
    duty_id: str | None = None
    person_id: str | None = None
    action_id: str | None = None
    policy_rule: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "detail": self.detail,
            "duty_id": self.duty_id,
            "person_id": self.person_id,
            "action_id": self.action_id,
            "policy_rule": self.policy_rule,
        }


@dataclass(frozen=True, slots=True)
class Brief:
    """What Zamu hands over, and nothing else."""

    org_id: str
    org_name: str
    generated_at: datetime
    since: datetime
    filled: tuple[Item, ...] = ()
    waiting: tuple[Item, ...] = ()
    needs_decision: tuple[Item, ...] = ()
    not_allowed: tuple[Item, ...] = ()
    fairness_note: str = ""

    @property
    def needs_human(self) -> bool:
        """Whether this brief is worth interrupting anyone for."""
        return bool(self.needs_decision or self.not_allowed)

    @property
    def is_quiet(self) -> bool:
        return not (self.filled or self.waiting or self.needs_decision or self.not_allowed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "org_name": self.org_name,
            "generated_at": self.generated_at.isoformat(),
            "since": self.since.isoformat(),
            "filled": [i.as_dict() for i in self.filled],
            "waiting": [i.as_dict() for i in self.waiting],
            "needs_decision": [i.as_dict() for i in self.needs_decision],
            "not_allowed": [i.as_dict() for i in self.not_allowed],
            "fairness_note": self.fairness_note,
            "needs_human": self.needs_human,
        }

    def to_text(self) -> str:
        """The brief as a coordinator would read it in an email."""
        if self.is_quiet:
            return f"{self.org_name}: nothing needed you since the last brief. Coverage is holding."

        lines: list[str] = [f"{self.org_name} — coverage update"]

        if self.needs_decision:
            lines.append("\nNeeds you:")
            lines += [f"  • {i.headline}\n    {i.detail}".rstrip() for i in self.needs_decision]

        if self.not_allowed:
            lines.append("\nZamu was not allowed to:")
            lines += [
                f"  • {i.headline}\n    {i.detail} [{i.policy_rule}]".rstrip()
                for i in self.not_allowed
            ]

        if self.filled:
            lines.append("\nFilled:")
            lines += [f"  • {i.headline}\n    {i.detail}".rstrip() for i in self.filled]

        if self.waiting:
            lines.append("\nWaiting on an answer:")
            lines += [f"  • {i.headline}\n    {i.detail}".rstrip() for i in self.waiting]

        if self.fairness_note:
            lines.append(f"\n{self.fairness_note}")

        return "\n".join(lines)


def build_brief(
    store: Store, org_id: str, now: datetime, *, since: datetime | None = None
) -> Brief:
    """Assemble the brief from the ledger and the current roster."""
    moment = utc(now)
    window_start = utc(since) if since is not None else moment - timedelta(hours=24)
    roster = store.load_roster(org_id)
    records = [r for r in store.list_actions(org_id) if utc(r.created_at) >= window_start]

    return Brief(
        org_id=org_id,
        org_name=roster.org.name,
        generated_at=moment,
        since=window_start,
        filled=_filled(roster, records),
        waiting=_waiting(roster, moment),
        needs_decision=_needs_decision(roster, moment, records),
        not_allowed=_not_allowed(roster, records),
        fairness_note=_fairness_note(roster, moment),
    )


def _filled(roster: Roster, records: list[ActionRecord]) -> tuple[Item, ...]:
    """Roster writes that were verified in the window."""
    out: list[Item] = []
    for record in reversed(records):
        if record.action_class is not ActionClass.WRITE_ROSTER:
            continue
        if record.result is not ActionResult.VERIFIED:
            continue
        duty = roster.duty(record.duty_id) if record.duty_id else None
        person = roster.person(record.person_id) if record.person_id else None
        if duty is None or person is None:
            continue
        out.append(
            Item(
                headline=f"{duty.title}, {format_when(duty, roster.org.timezone)} — {person.name}",
                detail=f"Confirmed on the roster and verified by re-read. [{record.policy_rule}]",
                duty_id=duty.id,
                person_id=person.id,
                action_id=record.id,
                policy_rule=record.policy_rule,
            )
        )
    return tuple(out)


def _waiting(roster: Roster, now: datetime) -> tuple[Item, ...]:
    """Asks still open. Listed so silence is visible rather than assumed."""
    out: list[Item] = []
    for ask in roster.open_asks():
        if ask.is_expired(now) or ask.drafted_only:
            continue
        duty = roster.duty(ask.duty_id)
        person = roster.person(ask.person_id)
        if duty is None or person is None:
            continue
        hours = max(0, round((ask.expires_at - now).total_seconds() / 3600))
        out.append(
            Item(
                headline=f"{person.name} on {duty.title}",
                detail=f"{format_when(duty, roster.org.timezone)}. "
                f"Moving on in about {hours}h if there is no answer.",
                duty_id=duty.id,
                person_id=person.id,
            )
        )
    return tuple(out)


def _needs_decision(
    roster: Roster, now: datetime, records: list[ActionRecord]
) -> tuple[Item, ...]:
    """Gaps with nobody left to ask, and acceptances Zamu could not finish."""
    out: list[Item] = []

    for assessment in assess_roster(roster, now):
        duty = roster.duty(assessment.duty_id)
        if duty is None or duty.cancelled or duty.start <= now:
            continue
        if assessment.state is not CoverageState.UNCOVERED:
            continue
        if any(a.state.is_open and not a.is_expired(now) for a in roster.asks_for_duty(duty.id)):
            continue

        # An uncovered duty is only a decision for the coordinator once Zamu has run
        # out of people it may ask. While somebody is still askable this is Zamu's
        # job, and putting it in the brief would be interrupting a human to tell them
        # about work that is already in hand — the exact behaviour this product exists
        # to remove. The coverage board still shows it; the brief stays quiet.
        order = rank(duty, roster, now)
        if order.has_candidates:
            continue

        already_asked = bool(roster.asks_for_duty(duty.id))
        out.append(
            Item(
                headline=f"{duty.title}, {format_when(duty, roster.org.timezone)} is uncovered",
                detail=(
                    "Everyone Zamu is allowed to ask has now been asked. Reducing scope "
                    "or widening permission is your call."
                    if already_asked
                    else "Nobody eligible is available for this one. It needs a human."
                ),
                duty_id=duty.id,
            )
        )

    for record in records:
        if record.action_class is ActionClass.WRITE_ROSTER and record.result in (
            ActionResult.CONFLICTED,
            ActionResult.FAILED,
        ):
            out.append(
                Item(
                    headline="A roster change did not land",
                    detail=record.detail,
                    duty_id=record.duty_id,
                    person_id=record.person_id,
                    action_id=record.id,
                    policy_rule=record.policy_rule,
                )
            )

    return tuple(out)


def _not_allowed(roster: Roster, records: list[ActionRecord]) -> tuple[Item, ...]:
    """Everything the policy gate refused, grouped by rule.

    Collapsed to one line per rule so a missing grant reads as one fixable problem
    rather than as forty separate failures.
    """
    grouped: dict[str, list[ActionRecord]] = {}
    for record in records:
        if record.result is ActionResult.BLOCKED:
            grouped.setdefault(record.policy_rule, []).append(record)

    out: list[Item] = []
    for rule, rows in sorted(grouped.items()):
        first = rows[0]
        names = sorted(
            {
                p.name
                for p in (roster.person(r.person_id) for r in rows if r.person_id)
                if p is not None
            }
        )
        who = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        headline = first.action_class.label.capitalize()
        if names:
            headline = f"{headline} ({who})"
        out.append(
            Item(
                headline=headline,
                detail=first.detail,
                action_id=first.id,
                policy_rule=rule,
                duty_id=first.duty_id,
                person_id=first.person_id,
            )
        )
    return tuple(out)


def _fairness_note(roster: Roster, now: datetime) -> str:
    """One sentence about how load is sitting. Never a scoreboard."""
    records = build_records(roster, now)
    active = [p for p in roster.people if p.active]
    if len(active) < 2:
        return ""

    mean = cohort_mean_load(records, roster.org, {p.id for p in active})
    loads = {
        p.id: records[p.id].weighted_load(roster.org.unsociable_hour_weight)
        for p in active
        if p.id in records
    }
    if not loads or max(loads.values()) == 0:
        return ""

    heaviest_id = max(loads, key=lambda pid: (loads[pid], pid))
    heaviest = roster.person(heaviest_id)
    spread = max(loads.values()) - min(loads.values())
    weeks = max(1, int(roster.org.fairness_window.days / 7))

    if spread < 2.0:
        return f"Load is even across the team over the last {weeks} weeks."
    if heaviest is None:
        return ""
    return (
        f"{heaviest.name} is carrying the most over the last {weeks} weeks "
        f"({loads[heaviest_id]:.1f}h against a {mean:.1f}h average). "
        "Zamu is already asking them last."
    )


def unanswered_since(roster: Roster, now: datetime) -> tuple[str, ...]:
    """Ask ids that have lapsed and need sweeping. Used by the scheduled job."""
    return tuple(
        a.id for a in roster.asks if a.state is AskState.SENT and a.is_expired(now)
    )
