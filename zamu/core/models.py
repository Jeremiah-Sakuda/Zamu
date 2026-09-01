"""The domain. Frozen dataclasses, explicit enums, no behaviour that isn't a pure derivation.

Design note: `CoverageState.UNKNOWN` is a first-class state and is never rendered as
covered. An agent that reports uncertainty as success is worse than no agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from enum import Enum, IntEnum
from typing import Any

from zamu.core.clock import utc

# --------------------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------------------


class CoverageState(str, Enum):
    """How confident we are that a duty will actually be performed by someone."""

    COVERED = "covered"
    AT_RISK = "at_risk"
    UNCOVERED = "uncovered"
    UNKNOWN = "unknown"


class ActionClass(IntEnum):
    """The trust ladder. Each rung is granted separately and enforced by code.

    The ordering is meaningful: a grant at a given level never implies the levels
    above it, but the numbering makes the escalation legible in receipts and logs.
    """

    READ = 0
    """Read roster, people, asks, history. On by default once a roster is connected."""

    DRAFT_ASK = 1
    """Compose an ask for the coordinator to send. Nothing leaves the system."""

    SEND_ASK = 2
    """Send an ask directly to a volunteer. Off by default."""

    WRITE_ROSTER = 3
    """Update the roster after an explicit acceptance. Off by default."""

    REASSIGN_WITHOUT_CONSENT = 4
    """Never implemented. A promise cannot be created on someone's behalf."""

    @property
    def label(self) -> str:
        return {
            ActionClass.READ: "read",
            ActionClass.DRAFT_ASK: "draft an ask",
            ActionClass.SEND_ASK: "send an ask",
            ActionClass.WRITE_ROSTER: "update the roster",
            ActionClass.REASSIGN_WITHOUT_CONSENT: "reassign without asking",
        }[self]


#: Action classes Zamu will refuse to execute under any grant. Enforced in authority.py.
FORBIDDEN_ACTION_CLASSES: frozenset[ActionClass] = frozenset(
    {ActionClass.REASSIGN_WITHOUT_CONSENT}
)


class AskState(str, Enum):
    """Lifecycle of a single ask to a single person about a single duty."""

    SENT = "sent"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"
    """The duty was filled by someone else while this ask was still open."""

    @property
    def is_open(self) -> bool:
        return self is AskState.SENT

    @property
    def is_terminal(self) -> bool:
        return not self.is_open


class ActionResult(str, Enum):
    """What actually happened to an attempted action, after verification."""

    VERIFIED = "verified"
    """The write landed and re-reading the target confirmed it."""

    FAILED = "failed"
    """The write did not happen."""

    CONFLICTED = "conflicted"
    """The write happened but the target holds a different value than intended."""

    BLOCKED = "blocked"
    """The policy gate refused. No write was attempted."""

    REVERSED = "reversed"
    """The write landed and was later undone by Zamu."""


class Channel(str, Enum):
    EMAIL = "email"
    WEB = "web"
    """The coordinator handed the volunteer a link directly. Needs no delivery provider."""


class DisqualifyingReason(str, Enum):
    """Why a person cannot take a duty. Each maps to one plain-English sentence."""

    INACTIVE = "inactive"
    MISSING_QUALIFICATION = "missing_qualification"
    BLACKOUT = "blackout"
    DOUBLE_BOOKED = "double_booked"
    INSUFFICIENT_NOTICE = "insufficient_notice"
    ALREADY_ASSIGNED = "already_assigned"
    DECLINED_THIS_DUTY = "declined_this_duty"
    ASK_BUDGET_EXHAUSTED = "ask_budget_exhausted"
    OPEN_ASK_ELSEWHERE = "open_ask_elsewhere"
    NOT_OPTED_IN = "not_opted_in"
    QUIET_HOURS_BLOCK_NOTICE = "quiet_hours_block_notice"


REASON_SENTENCES: dict[DisqualifyingReason, str] = {
    DisqualifyingReason.INACTIVE: "is not currently active in this organization",
    DisqualifyingReason.MISSING_QUALIFICATION: "is not trained for this role",
    DisqualifyingReason.BLACKOUT: "marked this time unavailable",
    DisqualifyingReason.DOUBLE_BOOKED: "is already covering an overlapping duty",
    DisqualifyingReason.INSUFFICIENT_NOTICE: "would get less notice than this duty requires",
    DisqualifyingReason.ALREADY_ASSIGNED: "is already assigned to this duty",
    DisqualifyingReason.DECLINED_THIS_DUTY: "already declined this duty",
    DisqualifyingReason.ASK_BUDGET_EXHAUSTED: "has already been asked as often as allowed",
    DisqualifyingReason.OPEN_ASK_ELSEWHERE: "has an unanswered ask open right now",
    DisqualifyingReason.NOT_OPTED_IN: "has not opted in to being contacted directly",
    DisqualifyingReason.QUIET_HOURS_BLOCK_NOTICE: (
        "could not be reached outside quiet hours in time for this duty"
    ),
}


# --------------------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A half-open interval [start, end). Used for duties and blackouts alike."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        if self.end <= self.start:
            raise ValueError("TimeWindow end must be strictly after start")

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def hours(self) -> float:
        return self.duration.total_seconds() / 3600.0

    def overlaps(self, other: TimeWindow) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, moment: datetime) -> bool:
        return self.start <= utc(moment) < self.end


@dataclass(frozen=True, slots=True)
class QuietHours:
    """A nightly window during which Zamu will not contact a person.

    Expressed in the person's own local timezone, because 9pm means 9pm where they are.
    A window that wraps midnight (22:00 to 07:00) is the normal case.
    """

    start: time = time(21, 0)
    end: time = time(8, 0)
    enabled: bool = True

    def covers(self, local_moment: time) -> bool:
        if not self.enabled:
            return False
        if self.start <= self.end:
            return self.start <= local_moment < self.end
        return local_moment >= self.start or local_moment < self.end


# --------------------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Org:
    """A group that runs a roster. The unit of tenancy, fairness, and authority."""

    id: str
    name: str
    timezone: str = "UTC"
    ask_window: timedelta = timedelta(hours=6)
    """Default time a single ask stays open before Zamu advances to the next candidate."""

    urgent_ask_window: timedelta = timedelta(minutes=90)
    """Shorter window used when the duty starts inside `urgent_threshold`."""

    urgent_threshold: timedelta = timedelta(hours=48)
    max_asks_per_person_per_week: int = 3
    """Hard ceiling. Reliability is taxed until it stops; this is the tax cap."""

    fairness_window: timedelta = timedelta(days=42)
    """Six weeks. Long enough to be fair, short enough to reflect who is around now."""

    unsociable_hour_weight: float = 1.5
    """Early mornings, late nights, and weekends count for more when measuring load."""

    stale_confirmation_after: timedelta = timedelta(days=14)
    """An acceptance older than this with no reconfirmation degrades to AT_RISK."""

    require_ranking_approval: bool = False
    """When true, the coordinator approves the candidate order before the first ask."""

    demo: bool = False

    def window_for(self, duty_start: datetime, now: datetime) -> timedelta:
        """How long one ask stays open, scaled to how soon the duty starts."""
        remaining = utc(duty_start) - utc(now)
        if remaining <= self.urgent_threshold:
            return self.urgent_ask_window
        return self.ask_window


@dataclass(frozen=True, slots=True)
class Person:
    """A volunteer. They install nothing and learn nothing."""

    id: str
    org_id: str
    name: str
    email: str
    qualifications: frozenset[str] = frozenset()
    blackouts: tuple[TimeWindow, ...] = ()
    quiet_hours: QuietHours = QuietHours()
    timezone: str = "UTC"
    opt_ins: frozenset[ActionClass] = frozenset()
    """Action classes this person has agreed to. Absence of an opt-in is a hard stop."""

    active: bool = True
    joined_at: datetime | None = None

    def is_opted_in(self, action_class: ActionClass) -> bool:
        return action_class in self.opt_ins

    def is_blacked_out(self, window: TimeWindow) -> bool:
        return any(b.overlaps(window) for b in self.blackouts)


@dataclass(frozen=True, slots=True)
class Duty:
    """One shift. A commitment a person made to other people, not a row in a table."""

    id: str
    org_id: str
    title: str
    window: TimeWindow
    role: str
    required_qualification: str | None = None
    min_notice: timedelta = timedelta(hours=12)
    assigned_person_id: str | None = None
    assigned_at: datetime | None = None
    confirmed_at: datetime | None = None
    """Last time the assigned person affirmed they are still coming."""

    source: str = "manual"
    cancelled: bool = False
    notes: str = ""

    @property
    def start(self) -> datetime:
        return self.window.start

    @property
    def end(self) -> datetime:
        return self.window.end

    @property
    def hours(self) -> float:
        return self.window.hours

    def assigned_to(self, person_id: str, at: datetime) -> Duty:
        return replace(self, assigned_person_id=person_id, assigned_at=utc(at), confirmed_at=utc(at))

    def vacated(self) -> Duty:
        return replace(self, assigned_person_id=None, assigned_at=None, confirmed_at=None)


@dataclass(frozen=True, slots=True)
class Ask:
    """One question, to one named person, about one duty, with an expiry.

    Broadcast is a failure mode, so there is no such thing as an Ask with many recipients.
    """

    id: str
    org_id: str
    duty_id: str
    person_id: str
    sent_at: datetime
    expires_at: datetime
    channel: Channel = Channel.EMAIL
    state: AskState = AskState.SENT
    token: str = ""
    """Opaque single-use secret backing the one-tap accept/decline links."""

    rank: int = 0
    """Position in the candidate order this ask came from. 1 means first choice."""

    rationale: str = ""
    """The one sentence Zamu would say out loud about why this person was asked."""

    responded_at: datetime | None = None
    drafted_only: bool = False
    """True when authority stopped at DRAFT_ASK and a human must send it."""

    def is_expired(self, now: datetime) -> bool:
        return self.state is AskState.SENT and utc(now) >= utc(self.expires_at)


@dataclass(frozen=True, slots=True)
class Grant:
    """Permission for one action class, in one org, granted by a named human."""

    id: str
    org_id: str
    action_class: ActionClass
    granted_by: str
    granted_at: datetime
    revoked_at: datetime | None = None
    person_scope: frozenset[str] | None = None
    """When set, the grant only covers these people. None means every person in the org."""

    note: str = ""

    def is_active(self, now: datetime) -> bool:
        moment = utc(now)
        if moment < utc(self.granted_at):
            return False
        return self.revoked_at is None or moment < utc(self.revoked_at)

    def covers_person(self, person_id: str | None) -> bool:
        if self.person_scope is None:
            return True
        return person_id is not None and person_id in self.person_scope


@dataclass(frozen=True, slots=True)
class FairnessRecord:
    """What one person has actually carried, and how often they have been asked.

    Derived from duties and asks; never hand-edited. Kept as a value object so the
    ranking function stays pure.
    """

    person_id: str
    window_start: datetime
    window_end: datetime
    shifts_carried: int = 0
    hours_carried: float = 0.0
    unsociable_hours_carried: float = 0.0
    asks_sent: int = 0
    declines: int = 0
    accepts: int = 0
    expirations: int = 0
    last_asked_at: datetime | None = None
    last_carried_at: datetime | None = None

    def weighted_load(self, unsociable_weight: float) -> float:
        """Hours carried, with unsociable hours counted at a premium.

        A Sunday 6am shift is not the same favour as a Wednesday afternoon, and any
        fairness measure that pretends otherwise will quietly punish the people who
        take the hard slots.
        """
        sociable = max(0.0, self.hours_carried - self.unsociable_hours_carried)
        return sociable + self.unsociable_hours_carried * unsociable_weight

    @property
    def responses(self) -> int:
        return self.accepts + self.declines

    @property
    def acceptance_rate(self) -> float | None:
        """Share of answered asks that were accepted. None when never asked."""
        if self.responses == 0:
            return None
        return self.accepts / self.responses

    @property
    def response_rate(self) -> float | None:
        """Share of asks that got any answer at all. None when never asked."""
        if self.asks_sent == 0:
            return None
        return self.responses / self.asks_sent


@dataclass(frozen=True, slots=True)
class ActionRecord:
    """One row of the append-only ledger. Written before execution, closed after verification.

    `intended` and `observed` sit next to each other on purpose: a receipt that only
    records what was attempted is a log, not proof.
    """

    id: str
    org_id: str
    idempotency_key: str
    action_class: ActionClass
    summary: str
    intended: dict[str, Any]
    policy_rule: str
    created_at: datetime
    executed_at: datetime | None = None
    verified_at: datetime | None = None
    observed: dict[str, Any] | None = None
    result: ActionResult | None = None
    detail: str = ""
    duty_id: str | None = None
    person_id: str | None = None

    @property
    def is_closed(self) -> bool:
        return self.result is not None


@dataclass(frozen=True, slots=True)
class Roster:
    """A snapshot of one org's world at one instant. The unit the agent reasons over."""

    org: Org
    people: tuple[Person, ...] = ()
    duties: tuple[Duty, ...] = ()
    asks: tuple[Ask, ...] = ()
    grants: tuple[Grant, ...] = ()

    _person_index: dict[str, Person] = field(default_factory=dict, repr=False, compare=False)
    _duty_index: dict[str, Duty] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._person_index.update({p.id: p for p in self.people})
        self._duty_index.update({d.id: d for d in self.duties})

    def person(self, person_id: str) -> Person | None:
        return self._person_index.get(person_id)

    def duty(self, duty_id: str) -> Duty | None:
        return self._duty_index.get(duty_id)

    def duties_for(self, person_id: str) -> tuple[Duty, ...]:
        return tuple(d for d in self.duties if d.assigned_person_id == person_id and not d.cancelled)

    def asks_for_duty(self, duty_id: str) -> tuple[Ask, ...]:
        return tuple(a for a in self.asks if a.duty_id == duty_id)

    def asks_for_person(self, person_id: str) -> tuple[Ask, ...]:
        return tuple(a for a in self.asks if a.person_id == person_id)

    def open_asks(self) -> tuple[Ask, ...]:
        return tuple(a for a in self.asks if a.state.is_open)
