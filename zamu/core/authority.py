"""The policy gate.

This is the part of Zamu that must not be persuadable. Inference is not permission:
the agent may work out that Marcus is almost certainly free on Thursday, and that
conclusion still gives it no right to contact him. Rights come from grants a named
human created, stored in the database, checked here by ordinary code.

Every refusal names the rule that produced it. "Not authorized" without a rule id is
indistinguishable from a bug, and the coordinator has no way to fix what they cannot
see. The rule ids appear verbatim in receipts and in the handover brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zamu.core.clock import utc
from zamu.core.fairness import ask_budget_remaining, build_records
from zamu.core.models import (
    FORBIDDEN_ACTION_CLASSES,
    ActionClass,
    AskState,
    Grant,
    Roster,
)


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """Something the agent wants to do, described before it is attempted."""

    org_id: str
    action_class: ActionClass
    summary: str
    person_id: str | None = None
    duty_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    """The gate's answer, always carrying the rule that produced it."""

    allowed: bool
    action_class: ActionClass
    rule: str
    reason: str
    grant_id: str | None = None

    def require(self) -> Decision:
        """Raise if this decision is a refusal. For call sites that cannot continue."""
        if not self.allowed:
            from zamu.core.errors import NotAuthorized

            raise NotAuthorized(self.action_class.name, f"[{self.rule}] {self.reason}")
        return self


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def active_grants(roster: Roster, now: datetime) -> tuple[Grant, ...]:
    """Grants currently in force for this org."""
    moment = utc(now)
    return tuple(g for g in roster.grants if g.org_id == roster.org.id and g.is_active(moment))


def granted_levels(roster: Roster, now: datetime) -> frozenset[ActionClass]:
    """Which rungs of the ladder are currently lit. Used by the console and the brief."""
    levels = {ActionClass.READ}
    for grant in active_grants(roster, now):
        if grant.action_class not in FORBIDDEN_ACTION_CLASSES:
            levels.add(grant.action_class)
    return frozenset(levels)


def find_grant(
    roster: Roster, action_class: ActionClass, person_id: str | None, now: datetime
) -> Grant | None:
    """The grant covering this action class and this person, if one exists."""
    for grant in active_grants(roster, now):
        if grant.action_class is action_class and grant.covers_person(person_id):
            return grant
    return None


def authorize(action: ProposedAction, roster: Roster, now: datetime) -> Decision:
    """Decide whether one proposed action may proceed.

    Rules are evaluated in order and the first refusal wins, so the reason the
    coordinator sees is the most fundamental one rather than an incidental detail.
    """
    moment = utc(now)

    # R0 — some things are never done, regardless of what anyone granted.
    if action.action_class in FORBIDDEN_ACTION_CLASSES:
        return Decision(
            False,
            action.action_class,
            "R0-never-implemented",
            "Zamu never reassigns a duty without asking the person receiving it. "
            "A promise cannot be created on someone's behalf.",
        )

    # R1 — wrong org. A grant in one organization says nothing about another.
    if action.org_id != roster.org.id:
        return Decision(
            False,
            action.action_class,
            "R1-org-mismatch",
            f"This action targets {action.org_id}, which is not this roster.",
        )

    # R2 — reading is on as soon as a roster is connected.
    if action.action_class is ActionClass.READ:
        return Decision(
            True,
            action.action_class,
            "R2-read-is-default",
            "Reading the roster is granted by connecting it.",
        )

    # R3 — every other action class needs a grant a human created.
    grant = find_grant(roster, action.action_class, action.person_id, moment)
    if grant is None:
        # Distinguish "no such grant" from "a grant exists but is scoped past this
        # person". They look identical to the agent and completely different to the
        # coordinator, who can fix the second one in a single click.
        exists_for_class = any(
            g.action_class is action.action_class for g in active_grants(roster, moment)
        )
        if exists_for_class:
            return Decision(
                False,
                action.action_class,
                "R3-outside-grant-scope",
                f"The grant to {action.action_class.label} does not cover this person.",
            )
        return Decision(
            False,
            action.action_class,
            "R3-no-grant",
            f"Nobody has granted permission to {action.action_class.label}.",
        )

    if action.action_class is ActionClass.DRAFT_ASK:
        return Decision(
            True,
            action.action_class,
            "R4-draft-stays-inside",
            "Drafting is permitted; nothing leaves the system without a human sending it.",
            grant.id,
        )

    if action.action_class is ActionClass.SEND_ASK:
        return _authorize_send(action, roster, grant, moment)

    if action.action_class is ActionClass.WRITE_ROSTER:
        return _authorize_write(action, roster, grant, moment)

    return Decision(
        False,
        action.action_class,
        "R9-unknown-action-class",
        "Zamu does not know how to authorize this kind of action, so it will not do it.",
    )


def _authorize_send(
    action: ProposedAction, roster: Roster, grant: Grant, now: datetime
) -> Decision:
    """Extra conditions on contacting a human being directly."""
    person = roster.person(action.person_id) if action.person_id else None
    if person is None:
        return Decision(
            False,
            action.action_class,
            "R5-unknown-person",
            "Zamu will not send a message to somebody who is not on this roster.",
            grant.id,
        )

    if not person.active:
        return Decision(
            False,
            action.action_class,
            "R5-person-inactive",
            f"{person.name} is no longer active in this organization.",
            grant.id,
        )

    if not person.is_opted_in(ActionClass.SEND_ASK):
        return Decision(
            False,
            action.action_class,
            "R6-no-opt-in",
            f"{person.name} has not opted in to being contacted directly by Zamu.",
            grant.id,
        )

    local = now.astimezone(_zone(person.timezone)).time()
    if person.quiet_hours.covers(local):
        return Decision(
            False,
            action.action_class,
            "R7-quiet-hours",
            f"It is inside {person.name}'s quiet hours. The ask will wait.",
            grant.id,
        )

    records = build_records(roster, now)
    record = records.get(person.id)
    if record is not None and ask_budget_remaining(record, roster.org, now, roster) <= 0:
        return Decision(
            False,
            action.action_class,
            "R8-ask-budget",
            f"{person.name} has already been asked "
            f"{roster.org.max_asks_per_person_per_week} times this week.",
            grant.id,
        )

    return Decision(
        True,
        action.action_class,
        "R6-opted-in-and-in-hours",
        f"{person.name} opted in and it is inside their contactable hours.",
        grant.id,
    )


def _authorize_write(
    action: ProposedAction, roster: Roster, grant: Grant, now: datetime
) -> Decision:
    """A roster write is only ever justified by an acceptance that already happened."""
    ask_id = action.payload.get("ask_id")
    if not ask_id:
        return Decision(
            False,
            action.action_class,
            "R10-no-acceptance",
            "Zamu only updates the roster in response to an explicit acceptance, "
            "and this action does not reference one.",
            grant.id,
        )

    ask = next((a for a in roster.asks if a.id == ask_id), None)
    if ask is None:
        return Decision(
            False,
            action.action_class,
            "R10-unknown-acceptance",
            "The acceptance this action refers to does not exist.",
            grant.id,
        )

    if ask.state is not AskState.ACCEPTED:
        return Decision(
            False,
            action.action_class,
            "R10-acceptance-not-recorded",
            f"That ask is {ask.state.value}, not accepted.",
            grant.id,
        )

    if action.person_id and ask.person_id != action.person_id:
        return Decision(
            False,
            action.action_class,
            "R11-acceptance-mismatch",
            "The acceptance was given by a different person than this write assigns.",
            grant.id,
        )

    if action.duty_id and ask.duty_id != action.duty_id:
        return Decision(
            False,
            action.action_class,
            "R11-acceptance-mismatch",
            "The acceptance was given for a different duty than this write changes.",
            grant.id,
        )

    if not action.payload.get("idempotency_key"):
        return Decision(
            False,
            action.action_class,
            "R12-no-idempotency-key",
            "Zamu will not perform an unrepeatable write without an idempotency key.",
            grant.id,
        )

    return Decision(
        True,
        action.action_class,
        "R10-explicit-acceptance",
        "This write is backed by a recorded acceptance from the person being assigned.",
        grant.id,
    )
