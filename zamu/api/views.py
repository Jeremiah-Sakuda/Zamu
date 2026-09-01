"""Turning the domain into what the console renders.

Kept apart from the HTTP layer so the shapes the console depends on can be tested
without a server, and apart from `serde` because these are views — denormalised,
pre-joined, and carrying the sentences a coordinator reads rather than the fields a
database stores.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from zamu.core.authority import granted_levels
from zamu.core.coverage import assess_roster, coverage_summary
from zamu.core.fairness import build_records, cohort_mean_load, describe_load
from zamu.core.messages import format_when, format_when_moment, relative_notice
from zamu.core.models import (
    FORBIDDEN_ACTION_CLASSES,
    ActionClass,
    ActionRecord,
    CoverageState,
    Roster,
)
from zamu.core.ranking import CandidateOrder


def org_view(roster: Roster, now: datetime) -> dict[str, Any]:
    return {
        "id": roster.org.id,
        "name": roster.org.name,
        "timezone": roster.org.timezone,
        "demo": roster.org.demo,
        "now": now.isoformat(),
        "local_time": format_when_moment(now, roster.org.timezone),
        "coverage": coverage_summary(roster, now),
        "fairness_window_weeks": int(roster.org.fairness_window.days / 7),
        "max_asks_per_person_per_week": roster.org.max_asks_per_person_per_week,
    }


def duties_view(roster: Roster, now: datetime) -> list[dict[str, Any]]:
    """Every duty with its coverage assessment and whoever is on it."""
    out: list[dict[str, Any]] = []
    for assessment in assess_roster(roster, now):
        duty = roster.duty(assessment.duty_id)
        if duty is None:
            continue
        holder = roster.person(duty.assigned_person_id) if duty.assigned_person_id else None
        open_ask = next(
            (
                a
                for a in roster.asks_for_duty(duty.id)
                if a.state.is_open and not a.is_expired(now)
            ),
            None,
        )
        asked = roster.person(open_ask.person_id) if open_ask else None
        out.append(
            {
                "id": duty.id,
                "title": duty.title,
                "role": duty.role,
                "required_qualification": duty.required_qualification,
                "starts_at": duty.start.isoformat(),
                "ends_at": duty.end.isoformat(),
                "when": format_when(duty, roster.org.timezone),
                "notice": relative_notice(duty, now) if duty.start > now else "past",
                "hours": round(duty.hours, 2),
                "state": assessment.state.value,
                "reason": assessment.reason,
                "needs_filling": assessment.needs_filling,
                "is_past": duty.end < now,
                "assigned": (
                    {"id": holder.id, "name": holder.name} if holder else None
                ),
                "confirmed_at": duty.confirmed_at.isoformat() if duty.confirmed_at else None,
                "pending_ask": (
                    {
                        "id": open_ask.id,
                        "person": asked.name if asked else open_ask.person_id,
                        "expires_at": open_ask.expires_at.isoformat(),
                        "rationale": open_ask.rationale,
                        "drafted_only": open_ask.drafted_only,
                    }
                    if open_ask
                    else None
                ),
            }
        )
    return out


def people_view(roster: Roster, now: datetime) -> list[dict[str, Any]]:
    """The fairness ledger, which is also the people list. They are the same screen."""
    records = build_records(roster, now)
    active_ids = {p.id for p in roster.people if p.active}
    mean = cohort_mean_load(records, roster.org, active_ids)

    rows = []
    for person in roster.people:
        record = records.get(person.id)
        if record is None:
            continue
        rows.append(
            {
                "id": person.id,
                "name": person.name,
                "email": person.email,
                "active": person.active,
                "qualifications": sorted(person.qualifications),
                "opted_in": ActionClass.SEND_ASK in person.opt_ins,
                "quiet_hours": (
                    f"{person.quiet_hours.start:%H:%M}–{person.quiet_hours.end:%H:%M}"
                    if person.quiet_hours.enabled
                    else None
                ),
                "shifts_carried": record.shifts_carried,
                "hours_carried": round(record.hours_carried, 1),
                "unsociable_hours": round(record.unsociable_hours_carried, 1),
                "weighted_load": round(
                    record.weighted_load(roster.org.unsociable_hour_weight), 1
                ),
                "asks_received": record.asks_sent,
                "declines": record.declines,
                "accepts": record.accepts,
                "last_asked_at": (
                    record.last_asked_at.isoformat() if record.last_asked_at else None
                ),
                "summary": describe_load(record, roster.org, mean),
            }
        )
    rows.sort(key=lambda r: (-r["weighted_load"], r["name"]))
    return rows


def candidates_view(order: CandidateOrder, roster: Roster) -> dict[str, Any]:
    duty = roster.duty(order.duty_id)
    return {
        "duty_id": order.duty_id,
        "duty_title": duty.title if duty else order.duty_id,
        "when": format_when(duty, roster.org.timezone) if duty else "",
        "team_average_load_hours": order.mean_load,
        "computed_at": order.computed_at.isoformat(),
        "candidates": [
            {
                "rank": i + 1,
                "person_id": c.person_id,
                "name": c.person_name,
                "score": round(c.score, 4),
                "rationale": c.rationale,
                "load_summary": c.load_summary,
                "fairness_debt_hours": c.debt_hours,
                "components": c.components.as_dict(),
                "asks_remaining": c.asks_remaining,
            }
            for i, c in enumerate(order.candidates)
        ],
        "excluded": [
            {"person_id": e.person_id, "name": e.person_name, "reason": e.explanation}
            for e in order.excluded
        ],
    }


def grants_view(roster: Roster, now: datetime) -> list[dict[str, Any]]:
    """The trust ladder, every rung, including the one that is never granted."""
    held = granted_levels(roster, now)
    active = {g.action_class: g for g in roster.grants if g.is_active(now)}

    rows = []
    for level in ActionClass:
        forbidden = level in FORBIDDEN_ACTION_CLASSES
        grant = active.get(level)
        rows.append(
            {
                "level": int(level),
                "key": level.name.lower(),
                "label": level.label,
                "granted": (not forbidden) and level in held,
                "changeable": level not in (ActionClass.READ, *FORBIDDEN_ACTION_CLASSES),
                "forbidden": forbidden,
                "default_on": level is ActionClass.READ,
                "granted_by": grant.granted_by if grant else None,
                "granted_at": grant.granted_at.isoformat() if grant else None,
                "note": grant.note if grant else _default_note(level),
                "description": _description(level),
            }
        )
    return rows


def _default_note(level: ActionClass) -> str:
    if level is ActionClass.READ:
        return "Granted by connecting a roster."
    if level in FORBIDDEN_ACTION_CLASSES:
        return "Not implemented, and never will be."
    return "Not granted."


def _description(level: ActionClass) -> str:
    return {
        ActionClass.READ: "See the roster, the people, and everything Zamu has done.",
        ActionClass.DRAFT_ASK: (
            "Prepare an ask for you to send. Nothing leaves the system without you."
        ),
        ActionClass.SEND_ASK: (
            "Message a volunteer directly, but only one who opted in, and only outside "
            "their quiet hours."
        ),
        ActionClass.WRITE_ROSTER: (
            "Update the roster once somebody has explicitly accepted, then re-read it "
            "to confirm the change landed."
        ),
        ActionClass.REASSIGN_WITHOUT_CONSENT: (
            "Move somebody onto a shift without asking them. Zamu will never do this: "
            "a promise cannot be created on somebody's behalf."
        ),
    }[level]


def receipt_view(record: ActionRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "at": record.created_at.isoformat(),
        "action": record.action_class.label,
        "action_level": int(record.action_class),
        "summary": record.summary,
        "policy_rule": record.policy_rule,
        "result": record.result.value if record.result else "in_progress",
        "detail": record.detail,
        "intended": record.intended,
        "observed": record.observed,
        "duty_id": record.duty_id,
        "person_id": record.person_id,
        "executed_at": record.executed_at.isoformat() if record.executed_at else None,
        "verified_at": record.verified_at.isoformat() if record.verified_at else None,
    }


def outbox_view(notifier, roster: Roster, base_url: str) -> list[dict[str, Any]]:
    """What Zamu actually sent, with the links a volunteer would tap.

    Only meaningful for the OutboxNotifier, which is the point: the sandbox lets you
    read the volunteer's inbox so the loop can be driven end to end by one person.
    """
    sent = getattr(notifier, "sent", [])
    rows = []
    for message, delivery in sent:
        ask = next(
            (
                a
                for a in roster.asks
                if a.person_id == message.person_id and a.duty_id == message.duty_id
            ),
            None,
        )
        rows.append(
            {
                "to_name": message.to_name,
                "to_email": message.to_email,
                "subject": message.subject,
                "text": message.text,
                "html": message.html,
                "delivered": delivery.ok,
                "provider": delivery.provider,
                "detail": delivery.detail,
                "ask_id": ask.id if ask else None,
                "state": ask.state.value if ask else None,
                "accept_url": f"{base_url.rstrip('/')}/r/{ask.token}/yes" if ask else None,
                "decline_url": f"{base_url.rstrip('/')}/r/{ask.token}/no" if ask else None,
            }
        )
    rows.reverse()
    return rows


def timeline_view(roster: Roster, now: datetime) -> dict[str, Any]:
    """A compact header for the console: what is wrong, right now, in three numbers."""
    summary = coverage_summary(roster, now)
    upcoming = [
        d
        for d in duties_view(roster, now)
        if not d["is_past"] and d["state"] != CoverageState.COVERED.value
    ]
    return {
        "covered": summary["covered"],
        "at_risk": summary["at_risk"],
        "uncovered": summary["uncovered"],
        "unknown": summary["unknown"],
        "needs_attention": len(upcoming),
        "open_asks": len([a for a in roster.open_asks() if not a.is_expired(now)]),
    }
