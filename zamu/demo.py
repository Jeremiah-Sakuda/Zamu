"""The seeded demonstration organization.

Built relative to the current moment rather than to fixed dates, so a judge opening
the live link in October sees a roster with a gap tomorrow, not a museum piece from
September. Everything is fictional: no real volunteer's name, email, or availability
appears anywhere in this repository.

The shape of the data is chosen to make the product's argument visible in one screen:
Amara has carried far too much, Marcus has carried almost nothing and is trained for
the open role, Sofia is enthusiastic but untrained, and Ben never opted in to being
contacted. Those four facts are the whole product.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from zamu.core.clock import utc
from zamu.core.ids import seeded_id
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
    TimeWindow,
)
from zamu.core.store import Store

DEMO_ORG_ID = "org_demo_riverside"
DEMO_TZ = "America/Chicago"
COORDINATOR = "Nadia Ferreira"

FOOD_SAFETY = "food-safety"
FORKLIFT = "forklift"
DRIVER = "driver"


def _at(base: datetime, days: int, hour: int, minute: int = 0) -> datetime:
    """A local wall-clock time on a day offset from `base`, normalised to UTC."""
    tz = ZoneInfo(DEMO_TZ)
    local_day = (base.astimezone(tz) + timedelta(days=days)).date()
    return datetime.combine(local_day, time(hour, minute), tzinfo=tz).astimezone(
        ZoneInfo("UTC")
    )


SATURDAY = 5


def _saturday_offset(base: datetime, weeks: int) -> int:
    """Day offset from `base` to a Saturday, `weeks` weeks away.

    The demo is built relative to whenever it is seeded, so a shift called "Saturday
    intake" has to actually land on a Saturday. Nothing undermines a roster demo
    faster than a Saturday shift on a Tuesday.
    """
    tz = ZoneInfo(DEMO_TZ)
    today = base.astimezone(tz).date()
    ahead = (SATURDAY - today.weekday()) % 7
    if weeks >= 0:
        return ahead + 7 * weeks if ahead or weeks else 7
    return ahead - 7 * (abs(weeks))


def _id(prefix: str, name: str) -> str:
    return seeded_id(prefix, DEMO_ORG_ID, name)


def demo_org() -> Org:
    return Org(
        id=DEMO_ORG_ID,
        name="Riverside Community Food Bank",
        timezone=DEMO_TZ,
        demo=True,
    )


def demo_people() -> tuple[Person, ...]:
    """Six volunteers, each embodying one thing coverage software usually gets wrong.

    Quiet hours are off in the sandbox. They are a real feature, exercised throughout
    the test suite, but a public demo that refuses to do anything between 9pm and 8am
    cannot be driven by a judge in another timezone — and an agent that sits on its
    hands is indistinguishable from one that is broken.
    """

    def make(
        name: str,
        quals: tuple[str, ...],
        *,
        opted_in: bool = True,
        active: bool = True,
        quiet: QuietHours | None = None,
    ) -> Person:
        handle = name.split()[0].lower()
        return Person(
            id=_id("per", name),
            org_id=DEMO_ORG_ID,
            name=name,
            email=f"{handle}@riverside.example",
            qualifications=frozenset(quals),
            quiet_hours=quiet or QuietHours(enabled=False),
            timezone=DEMO_TZ,
            opt_ins=frozenset({ActionClass.SEND_ASK}) if opted_in else frozenset(),
            active=active,
        )

    return (
        # Carries far too much. The person this product exists to protect.
        make("Amara Okonkwo", (FOOD_SAFETY, FORKLIFT, DRIVER)),
        # Trained, rested, and almost never asked. The right answer on Thursday.
        make("Marcus Tran", (FOOD_SAFETY,)),
        # Reliable, moderately loaded.
        make("Devon Reyes", (FOOD_SAFETY, DRIVER)),
        # Withdrew from Thursday this morning. The event that starts the demo.
        make("Priya Nair", (FOOD_SAFETY,)),
        # Keen and available, but not trained for the open role.
        make("Sofia Marchetti", ()),
        # Trained and free, but never agreed to be contacted. Zamu may not message him.
        make("Ben Whitfield", (FOOD_SAFETY,), opted_in=False),
    )


def demo_duties(people: tuple[Person, ...], now: datetime) -> tuple[Duty, ...]:
    """Six weeks of history plus the week ahead, with one hole in it."""
    by_name = {p.name.split()[0]: p for p in people}
    amara, marcus, devon, priya = (
        by_name["Amara"],
        by_name["Marcus"],
        by_name["Devon"],
        by_name["Priya"],
    )
    duties: list[Duty] = []

    def add(
        label: str,
        days: int,
        hour: int,
        length: float,
        role: str,
        qual: str | None,
        holder: Person | None,
        *,
        title: str,
        confirmed_days_ago: int = 20,
    ) -> None:
        start = _at(now, days, hour)
        duties.append(
            Duty(
                id=_id("dut", label),
                org_id=DEMO_ORG_ID,
                title=title,
                window=TimeWindow(start, start + timedelta(hours=length)),
                role=role,
                required_qualification=qual,
                min_notice=timedelta(hours=6),
                assigned_person_id=holder.id if holder else None,
                assigned_at=now - timedelta(days=confirmed_days_ago) if holder else None,
                confirmed_at=(
                    now - timedelta(days=confirmed_days_ago) if holder else None
                ),
                source="demo-seed",
            )
        )

    # --- history: Amara has been carrying the organisation -----------------------------
    for week in range(1, 6):
        add(
            f"past-amara-{week}",
            -7 * week,
            17,
            4.0,
            "Distribution",
            FOOD_SAFETY,
            amara,
            title="Evening distribution",
        )
        add(
            f"past-amara-sat-{week}",
            _saturday_offset(now, -week),
            8,
            5.0,
            "Intake",
            FOOD_SAFETY,
            amara,
            title="Saturday intake",
        )

    for week in range(1, 4):
        add(
            f"past-devon-{week}",
            -7 * week - 1,
            17,
            3.0,
            "Distribution",
            FOOD_SAFETY,
            devon,
            title="Evening distribution",
        )

    add("past-priya-1", -9, 17, 3.0, "Distribution", FOOD_SAFETY, priya,
        title="Evening distribution")
    add("past-marcus-1", -30, 17, 2.0, "Distribution", FOOD_SAFETY, marcus,
        title="Evening distribution")

    # --- the week ahead ----------------------------------------------------------------
    add("next-early", 1, 16, 2.0, "Distribution", FOOD_SAFETY, amara,
        title="Afternoon distribution", confirmed_days_ago=2)

    # The gap. Priya withdrew this morning and nobody has posted in the group chat.
    add("thursday-gap", 1, 18, 2.0, "Distribution", FOOD_SAFETY, None,
        title="Evening distribution")

    add("next-late", 1, 20, 2.0, "Distribution", FOOD_SAFETY, devon, title="Closing shift",
        confirmed_days_ago=2)
    add("saturday-intake", _saturday_offset(now, 0), 8, 5.0, "Intake", FOOD_SAFETY, amara,
        title="Saturday intake", confirmed_days_ago=3)

    # Accepted three weeks ago and never reconfirmed: honestly at risk, not covered.
    stale_start = _at(now, 5, 9)
    duties.append(
        Duty(
            id=_id("dut", "stale-delivery"),
            org_id=DEMO_ORG_ID,
            title="Morning delivery run",
            window=TimeWindow(stale_start, stale_start + timedelta(hours=3)),
            role="Delivery",
            required_qualification=DRIVER,
            min_notice=timedelta(hours=12),
            assigned_person_id=devon.id,
            assigned_at=now - timedelta(days=24),
            confirmed_at=now - timedelta(days=24),
            source="demo-seed",
        )
    )

    # A second open gap next week, so the console shows more than one thing to do.
    add("next-week-intake", _saturday_offset(now, 1), 8, 5.0, "Intake", FOOD_SAFETY, None,
        title="Saturday intake")

    return tuple(duties)


def demo_asks(
    people: tuple[Person, ...], duties: tuple[Duty, ...], now: datetime
) -> tuple[Ask, ...]:
    """A little ask history, so responsiveness and fairness have something to read."""
    by_name = {p.name.split()[0]: p for p in people}
    past = next(d for d in duties if d.id == _id("dut", "past-priya-1"))
    return (
        Ask(
            id=_id("ask", "priya-past"),
            org_id=DEMO_ORG_ID,
            duty_id=past.id,
            person_id=by_name["Priya"].id,
            sent_at=now - timedelta(days=12),
            expires_at=now - timedelta(days=12) + timedelta(hours=6),
            channel=Channel.EMAIL,
            state=AskState.ACCEPTED,
            token=_id("tok", "priya-past"),
            responded_at=now - timedelta(days=12, hours=-1),
            rationale="Priya Nair was the fairest pick and is trained for Distribution.",
        ),
        Ask(
            id=_id("ask", "devon-past"),
            org_id=DEMO_ORG_ID,
            duty_id=past.id,
            person_id=by_name["Devon"].id,
            sent_at=now - timedelta(days=20),
            expires_at=now - timedelta(days=20) + timedelta(hours=6),
            channel=Channel.EMAIL,
            state=AskState.ACCEPTED,
            token=_id("tok", "devon-past"),
            responded_at=now - timedelta(days=20, hours=-2),
            rationale="Devon Reyes was trained and rested.",
        ),
    )


def demo_withdrawal(
    people: tuple[Person, ...], duties: tuple[Duty, ...], now: datetime
) -> Ask:
    """Priya's withdrawal, recorded as a declined ask.

    This is the event the whole demo starts from. Recording it this way is not a
    presentation trick: it is exactly what `record_withdrawal` writes, and it is what
    keeps Zamu from cheerfully emailing Priya about the shift Priya just dropped.
    """
    priya = next(p for p in people if p.name.startswith("Priya"))
    gap = next(d for d in duties if d.id == _id("dut", "thursday-gap"))
    withdrew_at = now - timedelta(hours=3)
    return Ask(
        id=_id("ask", "priya-withdrawal"),
        org_id=DEMO_ORG_ID,
        duty_id=gap.id,
        person_id=priya.id,
        sent_at=withdrew_at,
        expires_at=withdrew_at,
        channel=Channel.WEB,
        state=AskState.WITHDRAWN,
        token="",
        rationale=(
            "Withdrew from this duty: \"So sorry, I can't make Thursday evening after "
            "all — my shift at work got moved.\""
        ),
        responded_at=withdrew_at,
        drafted_only=True,
    )


def demo_grants(now: datetime, *, send: bool = True, write: bool = True) -> tuple[Grant, ...]:
    """The trust ladder as a new coordinator would have it after one sitting."""
    granted_at = now - timedelta(days=30)
    grants = [
        Grant(
            id=_id("gr", "draft"),
            org_id=DEMO_ORG_ID,
            action_class=ActionClass.DRAFT_ASK,
            granted_by=COORDINATOR,
            granted_at=granted_at,
            note="On by default. Nothing leaves the system without a human.",
        )
    ]
    if send:
        grants.append(
            Grant(
                id=_id("gr", "send"),
                org_id=DEMO_ORG_ID,
                action_class=ActionClass.SEND_ASK,
                granted_by=COORDINATOR,
                granted_at=granted_at,
                note="Zamu may message volunteers who opted in, outside their quiet hours.",
            )
        )
    if write:
        grants.append(
            Grant(
                id=_id("gr", "write"),
                org_id=DEMO_ORG_ID,
                action_class=ActionClass.WRITE_ROSTER,
                granted_by=COORDINATOR,
                granted_at=granted_at,
                note="Zamu may update the roster once somebody has explicitly accepted.",
            )
        )
    return tuple(grants)


def seed(store: Store, now: datetime, *, send: bool = True, write: bool = True) -> str:
    """Load the demo organisation into any store. Returns the org id.

    Idempotent by construction: every id is derived from stable parts, so re-seeding
    overwrites rather than duplicating.
    """
    moment = utc(now)
    people = demo_people()
    duties = demo_duties(people, moment)

    store.put_org(demo_org())
    for person in people:
        store.put_person(person)
    for duty in duties:
        store.put_duty(duty)
    for ask in demo_asks(people, duties, moment):
        store.put_ask(ask)
    store.put_ask(demo_withdrawal(people, duties, moment))
    for grant in demo_grants(moment, send=send, write=write):
        store.put_grant(grant)

    return DEMO_ORG_ID


def demo_gap_id() -> str:
    """The duty id of the Thursday hole, for scripts and the demo narration."""
    return _id("dut", "thursday-gap")
