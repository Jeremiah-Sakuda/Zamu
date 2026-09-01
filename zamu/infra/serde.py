"""Turning the domain into JSON and back.

One place, used by every backing and by the HTTP layer, so that a duty means the same
thing in SQLite, in DynamoDB, and on the wire. Written by hand rather than generated
because the round trip has to be exact — a `frozenset` that comes back as a list, or a
naive datetime that comes back without a zone, would silently change what Zamu decides.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from zamu.core.clock import utc
from zamu.core.models import (
    ActionClass,
    ActionRecord,
    ActionResult,
    Ask,
    AskState,
    Channel,
    Duty,
    Grant,
    Org,
    Person,
    QuietHours,
    TimeWindow,
)


def _dt(value: datetime | None) -> str | None:
    return utc(value).isoformat() if value is not None else None


def _parse_dt(value: str | None) -> datetime | None:
    return utc(datetime.fromisoformat(value)) if value else None


def _secs(value: timedelta) -> float:
    return value.total_seconds()


# -- windows and quiet hours -----------------------------------------------------------


def dump_window(window: TimeWindow) -> dict[str, Any]:
    return {"start": _dt(window.start), "end": _dt(window.end)}


def load_window(raw: dict[str, Any]) -> TimeWindow:
    return TimeWindow(_parse_dt(raw["start"]), _parse_dt(raw["end"]))


def dump_quiet_hours(quiet: QuietHours) -> dict[str, Any]:
    return {
        "start": quiet.start.isoformat(),
        "end": quiet.end.isoformat(),
        "enabled": quiet.enabled,
    }


def load_quiet_hours(raw: dict[str, Any] | None) -> QuietHours:
    if not raw:
        return QuietHours()
    return QuietHours(
        start=time.fromisoformat(raw["start"]),
        end=time.fromisoformat(raw["end"]),
        enabled=bool(raw.get("enabled", True)),
    )


# -- org -------------------------------------------------------------------------------


def dump_org(org: Org) -> dict[str, Any]:
    return {
        "id": org.id,
        "name": org.name,
        "timezone": org.timezone,
        "ask_window_seconds": _secs(org.ask_window),
        "urgent_ask_window_seconds": _secs(org.urgent_ask_window),
        "urgent_threshold_seconds": _secs(org.urgent_threshold),
        "max_asks_per_person_per_week": org.max_asks_per_person_per_week,
        "fairness_window_seconds": _secs(org.fairness_window),
        "unsociable_hour_weight": org.unsociable_hour_weight,
        "stale_confirmation_after_seconds": _secs(org.stale_confirmation_after),
        "require_ranking_approval": org.require_ranking_approval,
        "demo": org.demo,
    }


def load_org(raw: dict[str, Any]) -> Org:
    return Org(
        id=raw["id"],
        name=raw["name"],
        timezone=raw.get("timezone", "UTC"),
        ask_window=timedelta(seconds=raw.get("ask_window_seconds", 21600)),
        urgent_ask_window=timedelta(seconds=raw.get("urgent_ask_window_seconds", 5400)),
        urgent_threshold=timedelta(seconds=raw.get("urgent_threshold_seconds", 172800)),
        max_asks_per_person_per_week=raw.get("max_asks_per_person_per_week", 3),
        fairness_window=timedelta(seconds=raw.get("fairness_window_seconds", 3628800)),
        unsociable_hour_weight=raw.get("unsociable_hour_weight", 1.5),
        stale_confirmation_after=timedelta(
            seconds=raw.get("stale_confirmation_after_seconds", 1209600)
        ),
        require_ranking_approval=raw.get("require_ranking_approval", False),
        demo=raw.get("demo", False),
    )


# -- person ----------------------------------------------------------------------------


def dump_person(person: Person) -> dict[str, Any]:
    return {
        "id": person.id,
        "org_id": person.org_id,
        "name": person.name,
        "email": person.email,
        "qualifications": sorted(person.qualifications),
        "blackouts": [dump_window(w) for w in person.blackouts],
        "quiet_hours": dump_quiet_hours(person.quiet_hours),
        "timezone": person.timezone,
        "opt_ins": sorted(int(a) for a in person.opt_ins),
        "active": person.active,
        "joined_at": _dt(person.joined_at),
    }


def load_person(raw: dict[str, Any]) -> Person:
    return Person(
        id=raw["id"],
        org_id=raw["org_id"],
        name=raw["name"],
        email=raw["email"],
        qualifications=frozenset(raw.get("qualifications") or ()),
        blackouts=tuple(load_window(w) for w in raw.get("blackouts") or ()),
        quiet_hours=load_quiet_hours(raw.get("quiet_hours")),
        timezone=raw.get("timezone", "UTC"),
        opt_ins=frozenset(ActionClass(int(a)) for a in raw.get("opt_ins") or ()),
        active=raw.get("active", True),
        joined_at=_parse_dt(raw.get("joined_at")),
    )


# -- duty ------------------------------------------------------------------------------


def dump_duty(duty: Duty) -> dict[str, Any]:
    return {
        "id": duty.id,
        "org_id": duty.org_id,
        "title": duty.title,
        "window": dump_window(duty.window),
        "role": duty.role,
        "required_qualification": duty.required_qualification,
        "min_notice_seconds": _secs(duty.min_notice),
        "assigned_person_id": duty.assigned_person_id,
        "assigned_at": _dt(duty.assigned_at),
        "confirmed_at": _dt(duty.confirmed_at),
        "source": duty.source,
        "cancelled": duty.cancelled,
        "notes": duty.notes,
    }


def load_duty(raw: dict[str, Any]) -> Duty:
    return Duty(
        id=raw["id"],
        org_id=raw["org_id"],
        title=raw["title"],
        window=load_window(raw["window"]),
        role=raw["role"],
        required_qualification=raw.get("required_qualification"),
        min_notice=timedelta(seconds=raw.get("min_notice_seconds", 43200)),
        assigned_person_id=raw.get("assigned_person_id"),
        assigned_at=_parse_dt(raw.get("assigned_at")),
        confirmed_at=_parse_dt(raw.get("confirmed_at")),
        source=raw.get("source", "manual"),
        cancelled=raw.get("cancelled", False),
        notes=raw.get("notes", ""),
    )


# -- ask -------------------------------------------------------------------------------


def dump_ask(ask: Ask) -> dict[str, Any]:
    return {
        "id": ask.id,
        "org_id": ask.org_id,
        "duty_id": ask.duty_id,
        "person_id": ask.person_id,
        "sent_at": _dt(ask.sent_at),
        "expires_at": _dt(ask.expires_at),
        "channel": ask.channel.value,
        "state": ask.state.value,
        "token": ask.token,
        "rank": ask.rank,
        "rationale": ask.rationale,
        "responded_at": _dt(ask.responded_at),
        "drafted_only": ask.drafted_only,
    }


def load_ask(raw: dict[str, Any]) -> Ask:
    return Ask(
        id=raw["id"],
        org_id=raw["org_id"],
        duty_id=raw["duty_id"],
        person_id=raw["person_id"],
        sent_at=_parse_dt(raw["sent_at"]),
        expires_at=_parse_dt(raw["expires_at"]),
        channel=Channel(raw.get("channel", "email")),
        state=AskState(raw.get("state", "sent")),
        token=raw.get("token", ""),
        rank=raw.get("rank", 0),
        rationale=raw.get("rationale", ""),
        responded_at=_parse_dt(raw.get("responded_at")),
        drafted_only=raw.get("drafted_only", False),
    )


# -- grant -----------------------------------------------------------------------------


def dump_grant(grant: Grant) -> dict[str, Any]:
    return {
        "id": grant.id,
        "org_id": grant.org_id,
        "action_class": int(grant.action_class),
        "granted_by": grant.granted_by,
        "granted_at": _dt(grant.granted_at),
        "revoked_at": _dt(grant.revoked_at),
        "person_scope": sorted(grant.person_scope) if grant.person_scope is not None else None,
        "note": grant.note,
    }


def load_grant(raw: dict[str, Any]) -> Grant:
    scope = raw.get("person_scope")
    return Grant(
        id=raw["id"],
        org_id=raw["org_id"],
        action_class=ActionClass(int(raw["action_class"])),
        granted_by=raw.get("granted_by", ""),
        granted_at=_parse_dt(raw["granted_at"]),
        revoked_at=_parse_dt(raw.get("revoked_at")),
        person_scope=frozenset(scope) if scope is not None else None,
        note=raw.get("note", ""),
    )


# -- ledger ----------------------------------------------------------------------------


def dump_action(record: ActionRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "org_id": record.org_id,
        "idempotency_key": record.idempotency_key,
        "action_class": int(record.action_class),
        "summary": record.summary,
        "intended": record.intended,
        "policy_rule": record.policy_rule,
        "created_at": _dt(record.created_at),
        "executed_at": _dt(record.executed_at),
        "verified_at": _dt(record.verified_at),
        "observed": record.observed,
        "result": record.result.value if record.result else None,
        "detail": record.detail,
        "duty_id": record.duty_id,
        "person_id": record.person_id,
    }


def load_action(raw: dict[str, Any]) -> ActionRecord:
    return ActionRecord(
        id=raw["id"],
        org_id=raw["org_id"],
        idempotency_key=raw["idempotency_key"],
        action_class=ActionClass(int(raw["action_class"])),
        summary=raw.get("summary", ""),
        intended=raw.get("intended") or {},
        policy_rule=raw.get("policy_rule", ""),
        created_at=_parse_dt(raw["created_at"]),
        executed_at=_parse_dt(raw.get("executed_at")),
        verified_at=_parse_dt(raw.get("verified_at")),
        observed=raw.get("observed"),
        result=ActionResult(raw["result"]) if raw.get("result") else None,
        detail=raw.get("detail", ""),
        duty_id=raw.get("duty_id"),
        person_id=raw.get("person_id"),
    )


DUMPERS = {
    "org": dump_org,
    "person": dump_person,
    "duty": dump_duty,
    "ask": dump_ask,
    "grant": dump_grant,
    "action": dump_action,
}

LOADERS = {
    "org": load_org,
    "person": load_person,
    "duty": load_duty,
    "ask": load_ask,
    "grant": load_grant,
    "action": load_action,
}
