"""Deterministic fixtures.

Every test builds its world from here so that failures point at the code under test
rather than at incidental differences between hand-rolled setups. `NOW` is fixed and
every other timestamp in the suite is expressed relative to it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from zamu.core.models import (
    ActionClass,
    Ask,
    AskState,
    Channel,
    Duty,
    Grant,
    Org,
    Person,
    QuietHours,
    Roster,
    TimeWindow,
)
from zamu.core.store import InMemoryStore

CHICAGO = ZoneInfo("America/Chicago")

#: Thursday 3 September 2026, 09:00 in Chicago. The morning a volunteer drops out.
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=UTC)

ORG_ID = "org_riverside"
SEND = ActionClass.SEND_ASK


def local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """A Chicago wall-clock moment, normalised to UTC."""
    return datetime(year, month, day, hour, minute, tzinfo=CHICAGO).astimezone(UTC)


def org(**overrides) -> Org:
    base = Org(
        id=ORG_ID,
        name="Riverside Food Bank",
        timezone="America/Chicago",
    )
    return replace(base, **overrides) if overrides else base


def person(
    person_id: str,
    name: str,
    *,
    qualifications: tuple[str, ...] = (),
    opted_in: bool = True,
    active: bool = True,
    quiet_hours: QuietHours | None = None,
    blackouts: tuple[TimeWindow, ...] = (),
    timezone_name: str = "America/Chicago",
) -> Person:
    return Person(
        id=person_id,
        org_id=ORG_ID,
        name=name,
        email=f"{person_id.removeprefix('per_')}@example.org",
        qualifications=frozenset(qualifications),
        blackouts=blackouts,
        quiet_hours=quiet_hours if quiet_hours is not None else QuietHours(),
        timezone=timezone_name,
        opt_ins=frozenset({ActionClass.SEND_ASK}) if opted_in else frozenset(),
        active=active,
    )


def duty(
    duty_id: str,
    start: datetime,
    hours: float = 2.0,
    *,
    role: str = "Distribution",
    required_qualification: str | None = "food-safety",
    assigned_person_id: str | None = None,
    confirmed_at: datetime | None = None,
    min_notice: timedelta = timedelta(hours=12),
    cancelled: bool = False,
    title: str = "Evening distribution",
) -> Duty:
    return Duty(
        id=duty_id,
        org_id=ORG_ID,
        title=title,
        window=TimeWindow(start, start + timedelta(hours=hours)),
        role=role,
        required_qualification=required_qualification,
        min_notice=min_notice,
        assigned_person_id=assigned_person_id,
        assigned_at=confirmed_at,
        confirmed_at=confirmed_at,
        cancelled=cancelled,
    )


def ask(
    ask_id: str,
    duty_id: str,
    person_id: str,
    *,
    sent_at: datetime | None = None,
    state: AskState = AskState.SENT,
    expires_in: timedelta = timedelta(hours=6),
    drafted_only: bool = False,
    token: str = "",
) -> Ask:
    sent = sent_at if sent_at is not None else NOW
    return Ask(
        id=ask_id,
        org_id=ORG_ID,
        duty_id=duty_id,
        person_id=person_id,
        sent_at=sent,
        expires_at=sent + expires_in,
        channel=Channel.EMAIL,
        state=state,
        token=token or f"tok-{ask_id}",
        drafted_only=drafted_only,
    )


def grant(
    action_class: ActionClass,
    *,
    grant_id: str | None = None,
    granted_at: datetime | None = None,
    revoked_at: datetime | None = None,
    person_scope: frozenset[str] | None = None,
) -> Grant:
    return Grant(
        id=grant_id or f"gr_{action_class.name.lower()}",
        org_id=ORG_ID,
        action_class=action_class,
        granted_by="per_coordinator",
        granted_at=granted_at or (NOW - timedelta(days=30)),
        revoked_at=revoked_at,
        person_scope=person_scope,
    )


def roster(
    *,
    people: tuple[Person, ...] = (),
    duties: tuple[Duty, ...] = (),
    asks: tuple[Ask, ...] = (),
    grants: tuple[Grant, ...] = (),
    org_overrides: dict | None = None,
) -> Roster:
    return Roster(
        org=org(**(org_overrides or {})),
        people=people,
        duties=duties,
        asks=asks,
        grants=grants,
    )


def store_with(roster_obj: Roster) -> InMemoryStore:
    """An InMemoryStore preloaded with a roster, for tests that need persistence."""
    store = InMemoryStore()
    store.put_org(roster_obj.org)
    for p in roster_obj.people:
        store.put_person(p)
    for d in roster_obj.duties:
        store.put_duty(d)
    for a in roster_obj.asks:
        store.put_ask(a)
    for g in roster_obj.grants:
        store.put_grant(g)
    return store


# --------------------------------------------------------------------------------------
# A standard cast, used wherever a test needs several plausible volunteers.
# --------------------------------------------------------------------------------------

AMARA = person("per_amara", "Amara Okonkwo", qualifications=("food-safety", "forklift"))
MARCUS = person("per_marcus", "Marcus Tran", qualifications=("food-safety",))
DEVON = person("per_devon", "Devon Reyes", qualifications=("food-safety",))
PRIYA = person("per_priya", "Priya Nair", qualifications=("food-safety",))
SOFIA = person("per_sofia", "Sofia Marchetti", qualifications=())
BEN = person("per_ben", "Ben Whitfield", qualifications=("food-safety",), opted_in=False)

CAST = (AMARA, MARCUS, DEVON, PRIYA, SOFIA, BEN)

#: The uncovered Thursday from the PRD: 6pm local, the day after NOW.
THURSDAY_GAP = duty("dut_thursday", local(2026, 9, 4, 18), hours=2.0)
