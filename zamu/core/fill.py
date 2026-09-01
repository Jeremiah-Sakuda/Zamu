"""The fill loop: observe, evaluate, rank, authorize, ask, verify, report.

This is the layer the Strands tools wrap. It is deliberately ordinary Python with no
model anywhere in it, so that every behaviour a coordinator is asked to trust —
who gets asked, in what order, under whose permission, and whether the roster really
changed — is a thing that can be unit tested rather than prompted for.

The agent's job is to decide *which* of these operations to call and when. Its job is
never to decide what any of them return.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from zamu.core.authority import ProposedAction, authorize, granted_levels
from zamu.core.clock import Clock
from zamu.core.coverage import CoverageAssessment, assess_duty, gaps
from zamu.core.errors import NotFound
from zamu.core.ids import (
    ask_idempotency_key,
    assignment_idempotency_key,
    idempotency_key,
    new_id,
    new_token,
)
from zamu.core.ledger import Ledger
from zamu.core.messages import compose_ask, compose_draft_note
from zamu.core.models import (
    ActionClass,
    ActionResult,
    Ask,
    AskState,
    Channel,
    Duty,
    Person,
    Roster,
)
from zamu.core.ranking import Candidate, CandidateOrder, rank
from zamu.core.store import Store
from zamu.infra.notify import Message, Notifier


class Outcome(StrEnum):
    """What one turn of the fill loop achieved."""

    ASKED = "asked"
    """An ask was sent to one person and the row was verified on re-read."""

    DRAFTED = "drafted"
    """Zamu was not allowed to send, so it prepared the message for a human."""

    ALREADY_COVERED = "already_covered"
    WAITING = "waiting"
    """An ask is already open for this duty. One at a time means waiting is correct."""

    NO_CANDIDATES = "no_candidates"
    """Nobody authorized can cover this. The coordinator has a real decision to make."""

    BLOCKED = "blocked"
    """The policy gate refused and there was no lesser action available."""

    FAILED = "failed"
    REPLAYED = "replayed"
    """This exact action was already performed. Nothing was done a second time."""


@dataclass(frozen=True, slots=True)
class AskOutcome:
    """The result of one attempt to fill one duty, with everything a receipt needs."""

    outcome: Outcome
    duty_id: str
    detail: str
    person_id: str | None = None
    person_name: str | None = None
    ask_id: str | None = None
    action_id: str | None = None
    rationale: str = ""
    policy_rule: str = ""
    expires_at: datetime | None = None
    needs_coordinator: bool = False
    excluded: tuple[tuple[str, str], ...] = ()
    """(person name, why not) for everybody the ranking ruled out, so it is auditable."""

    draft_subject: str = ""
    draft_text: str = ""
    """The ready-to-send message, populated when authority stopped at DRAFT_ASK.

    Stopping at a draft only helps if the work is still done, so the coordinator gets
    the finished text rather than a note telling them to write one."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "duty_id": self.duty_id,
            "detail": self.detail,
            "person_id": self.person_id,
            "person_name": self.person_name,
            "ask_id": self.ask_id,
            "action_id": self.action_id,
            "rationale": self.rationale,
            "policy_rule": self.policy_rule,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "needs_coordinator": self.needs_coordinator,
            "excluded": [{"person": n, "reason": r} for n, r in self.excluded],
            "draft_subject": self.draft_subject,
            "draft_text": self.draft_text,
        }


class ResponseOutcome(StrEnum):
    """What happened when a volunteer tapped a link."""

    ACCEPTED_AND_ASSIGNED = "accepted_and_assigned"
    ACCEPTED_PENDING_COORDINATOR = "accepted_pending_coordinator"
    """They said yes, but Zamu has no grant to write the roster. A human must finish."""

    DECLINED = "declined"
    ALREADY_ANSWERED = "already_answered"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
    SUPERSEDED = "superseded"
    """Somebody else took the shift while this ask was open."""

    WRITE_FAILED = "write_failed"


@dataclass(frozen=True, slots=True)
class Response:
    """The result of one volunteer answering one ask."""

    outcome: ResponseOutcome
    detail: str
    ask_id: str | None = None
    duty_id: str | None = None
    person_id: str | None = None
    person_name: str = ""
    duty_title: str = ""
    action_id: str | None = None
    verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "detail": self.detail,
            "ask_id": self.ask_id,
            "duty_id": self.duty_id,
            "person_id": self.person_id,
            "person_name": self.person_name,
            "duty_title": self.duty_title,
            "action_id": self.action_id,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class SweepResult:
    """One pass over an org: expire what lapsed, then try to fill what is open."""

    org_id: str
    at: datetime
    expired: tuple[str, ...] = ()
    outcomes: tuple[AskOutcome, ...] = ()

    @property
    def asked(self) -> tuple[AskOutcome, ...]:
        return tuple(o for o in self.outcomes if o.outcome is Outcome.ASKED)

    @property
    def needing_coordinator(self) -> tuple[AskOutcome, ...]:
        return tuple(o for o in self.outcomes if o.needs_coordinator)

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "at": self.at.isoformat(),
            "expired_asks": list(self.expired),
            "outcomes": [o.as_dict() for o in self.outcomes],
        }


@dataclass
class CoverageService:
    """Every operation Zamu can perform on a roster, gated and receipted."""

    store: Store
    clock: Clock
    notifier: Notifier
    base_url: str = "http://localhost:8000"
    _ledger: Ledger = field(init=False)

    def __post_init__(self) -> None:
        self._ledger = Ledger(self.store, self.clock)

    # -- reading -----------------------------------------------------------------------

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    def roster(self, org_id: str) -> Roster:
        return self.store.load_roster(org_id)

    def find_gaps(
        self, org_id: str, horizon_days: int | None = 21
    ) -> tuple[CoverageAssessment, ...]:
        """Duties that need somebody found for them, soonest first."""
        return gaps(self.roster(org_id), self.clock.now(), horizon_days=horizon_days)

    def rank_for(
        self, org_id: str, duty_id: str, *, for_action: ActionClass | None = None
    ) -> CandidateOrder:
        """The candidate order for one duty, at the highest authority currently held."""
        roster = self.roster(org_id)
        duty = self._require_duty(roster, duty_id)
        action = for_action if for_action is not None else self._best_available_action(roster)
        return rank(duty, roster, self.clock.now(), for_action=action)

    # -- asking ------------------------------------------------------------------------

    def ask_next(self, org_id: str, duty_id: str) -> AskOutcome:
        """Ask exactly one person about one duty, or explain precisely why not.

        The whole product is in this method's refusal to broadcast. If the first
        candidate cannot be asked, Zamu does not widen the net — it stops, records
        why, and either drops to a draft the coordinator can send or hands over.
        """
        now = self.clock.now()
        roster = self.roster(org_id)
        duty = self._require_duty(roster, duty_id)

        assessment = assess_duty(duty, roster.org, now, roster)
        if not assessment.needs_filling:
            return AskOutcome(
                Outcome.ALREADY_COVERED,
                duty_id,
                assessment.reason,
                person_id=duty.assigned_person_id,
            )

        open_ask = self._open_ask_for(roster, duty_id, now)
        if open_ask is not None:
            person = roster.person(open_ask.person_id)
            return AskOutcome(
                Outcome.WAITING,
                duty_id,
                f"Waiting on {person.name if person else open_ask.person_id} until "
                f"{open_ask.expires_at.isoformat()}.",
                person_id=open_ask.person_id,
                person_name=person.name if person else None,
                ask_id=open_ask.id,
                expires_at=open_ask.expires_at,
            )

        intent = self._best_available_action(roster)
        order = rank(duty, roster, now, for_action=intent)
        excluded = tuple((e.person_name, e.explanation) for e in order.excluded)

        candidate = order.first
        if candidate is None:
            return AskOutcome(
                Outcome.NO_CANDIDATES,
                duty_id,
                "Nobody Zamu is allowed to ask can cover this duty. "
                "Reducing scope or widening permission is a decision for you.",
                needs_coordinator=True,
                excluded=excluded,
            )

        person = roster.person(candidate.person_id)
        if person is None:  # pragma: no cover - ranking only yields roster members
            raise NotFound(f"ranked candidate {candidate.person_id} is not on the roster")

        send_action = ProposedAction(
            org_id=org_id,
            action_class=ActionClass.SEND_ASK,
            summary=f"Ask {person.name} to cover {duty.title}",
            person_id=person.id,
            duty_id=duty_id,
        )
        decision = authorize(send_action, roster, now)

        if decision.allowed:
            return self._send_ask(roster, duty, person, candidate, decision.rule, excluded)

        # Sending was refused. Record that plainly, then try the lesser action.
        self._ledger.record_blocked(
            send_action, decision, idempotency_key("blocked_send", duty_id, person.id)
        )

        draft_action = replace(
            send_action,
            action_class=ActionClass.DRAFT_ASK,
            summary=f"Draft an ask to {person.name} about {duty.title}",
        )
        draft_decision = authorize(draft_action, roster, now)
        if draft_decision.allowed:
            return self._draft_ask(
                roster, duty, person, candidate, decision.reason, draft_decision.rule, excluded
            )

        return AskOutcome(
            Outcome.BLOCKED,
            duty_id,
            decision.reason,
            person_id=person.id,
            person_name=person.name,
            rationale=candidate.rationale,
            policy_rule=decision.rule,
            needs_coordinator=True,
            excluded=excluded,
        )

    def _send_ask(
        self,
        roster: Roster,
        duty: Duty,
        person: Person,
        candidate: Candidate,
        rule: str,
        excluded: tuple[tuple[str, str], ...],
    ) -> AskOutcome:
        now = self.clock.now()
        key = ask_idempotency_key(duty.id, person.id)
        ask_id = new_id("ask")
        expires_at = now + roster.org.window_for(duty.start, now)

        intended = {
            "ask_id": ask_id,
            "duty_id": duty.id,
            "person_id": person.id,
            "state": AskState.SENT.value,
        }
        action = ProposedAction(
            roster.org.id,
            ActionClass.SEND_ASK,
            f"Ask {person.name} to cover {duty.title}",
            person_id=person.id,
            duty_id=duty.id,
        )
        from zamu.core.authority import Decision

        entry = self._ledger.begin(
            action, Decision(True, ActionClass.SEND_ASK, rule, "authorized"), intended, key
        )
        if entry.replayed:
            return AskOutcome(
                Outcome.REPLAYED,
                duty.id,
                f"{person.name} has already been asked about this duty; nothing was sent again.",
                person_id=person.id,
                person_name=person.name,
                ask_id=entry.record.intended.get("ask_id"),
                action_id=entry.record.id,
                policy_rule=entry.record.policy_rule,
            )

        token = new_token()
        ask = Ask(
            id=ask_id,
            org_id=roster.org.id,
            duty_id=duty.id,
            person_id=person.id,
            sent_at=now,
            expires_at=expires_at,
            channel=Channel.EMAIL,
            state=AskState.SENT,
            token=token,
            rank=1,
            rationale=candidate.rationale,
        )
        self.store.put_ask(ask)

        composed = compose_ask(
            person,
            duty,
            roster.org,
            now,
            accept_url=self.accept_url(token),
            decline_url=self.decline_url(token),
            expires_at=expires_at,
        )
        delivery = self.notifier.send(
            Message(
                to_email=person.email,
                to_name=person.name,
                subject=composed.subject,
                text=composed.text,
                html=composed.html,
                kind="ask",
                org_id=roster.org.id,
                duty_id=duty.id,
                person_id=person.id,
            )
        )
        executed = self._ledger.mark_executed(entry.record)

        if not delivery.ok:
            # The message did not go out, so the ask must not survive as if it had.
            self.store.put_ask(replace(ask, state=AskState.WITHDRAWN, responded_at=now))
            self._ledger.fail(executed, f"Delivery failed: {delivery.detail}")
            return AskOutcome(
                Outcome.FAILED,
                duty.id,
                f"Could not reach {person.name}: {delivery.detail}",
                person_id=person.id,
                person_name=person.name,
                ask_id=ask_id,
                action_id=executed.id,
                needs_coordinator=True,
                excluded=excluded,
            )

        observed = self._observe_ask(roster.org.id, ask_id)
        closed = self._ledger.close(executed, observed)

        return AskOutcome(
            Outcome.ASKED if closed.result is ActionResult.VERIFIED else Outcome.FAILED,
            duty.id,
            closed.detail,
            person_id=person.id,
            person_name=person.name,
            ask_id=ask_id,
            action_id=closed.id,
            rationale=candidate.rationale,
            policy_rule=rule,
            expires_at=expires_at,
            needs_coordinator=closed.result is not ActionResult.VERIFIED,
            excluded=excluded,
        )

    def _draft_ask(
        self,
        roster: Roster,
        duty: Duty,
        person: Person,
        candidate: Candidate,
        refusal: str,
        rule: str,
        excluded: tuple[tuple[str, str], ...],
    ) -> AskOutcome:
        """Same intelligence, lesser authority: prepare the message, send nothing."""
        now = self.clock.now()
        from zamu.core.authority import Decision

        key = idempotency_key("draft", duty.id, person.id)
        ask_id = new_id("ask")
        intended = {
            "ask_id": ask_id,
            "duty_id": duty.id,
            "person_id": person.id,
            "state": AskState.SENT.value,
        }
        action = ProposedAction(
            roster.org.id,
            ActionClass.DRAFT_ASK,
            f"Draft an ask to {person.name} about {duty.title}",
            person_id=person.id,
            duty_id=duty.id,
        )
        entry = self._ledger.begin(
            action, Decision(True, ActionClass.DRAFT_ASK, rule, refusal), intended, key
        )
        if entry.replayed:
            return AskOutcome(
                Outcome.REPLAYED,
                duty.id,
                f"A draft asking {person.name} already exists.",
                person_id=person.id,
                person_name=person.name,
                ask_id=entry.record.intended.get("ask_id"),
                action_id=entry.record.id,
                needs_coordinator=True,
            )

        token = new_token()
        self.store.put_ask(
            Ask(
                id=ask_id,
                org_id=roster.org.id,
                duty_id=duty.id,
                person_id=person.id,
                sent_at=now,
                expires_at=now + roster.org.window_for(duty.start, now),
                channel=Channel.WEB,
                state=AskState.SENT,
                token=token,
                rank=1,
                rationale=candidate.rationale,
                drafted_only=True,
            )
        )
        note = compose_draft_note(person, duty, roster.org, refusal)
        executed = self._ledger.mark_executed(entry.record)
        closed = self._ledger.close(executed, self._observe_ask(roster.org.id, ask_id))

        return AskOutcome(
            Outcome.DRAFTED,
            duty.id,
            f"{refusal} A ready-to-send draft is waiting for you.",
            person_id=person.id,
            person_name=person.name,
            ask_id=ask_id,
            action_id=closed.id,
            rationale=candidate.rationale,
            policy_rule=rule,
            needs_coordinator=True,
            excluded=excluded,
            draft_subject=note.subject,
            draft_text=note.text,
        )

    # -- answering ---------------------------------------------------------------------

    def record_response(self, token: str, *, accept: bool) -> Response:
        """Handle one volunteer tapping accept or decline.

        Idempotent by construction: the same tap twice produces the same answer and
        writes nothing the second time, because people double-tap links and email
        clients prefetch them.
        """
        now = self.clock.now()
        ask = self.store.get_ask_by_token(token)
        if ask is None:
            return Response(ResponseOutcome.UNKNOWN, "That link is not one Zamu recognises.")

        roster = self.roster(ask.org_id)
        duty = roster.duty(ask.duty_id)
        person = roster.person(ask.person_id)
        title = duty.title if duty else ask.duty_id
        name = person.name if person else ask.person_id

        if ask.state is AskState.ACCEPTED:
            return Response(
                ResponseOutcome.ALREADY_ANSWERED,
                f"You already accepted {title}. Nothing more to do.",
                ask.id, ask.duty_id, ask.person_id, name, title,
            )
        if ask.state is AskState.DECLINED:
            return Response(
                ResponseOutcome.ALREADY_ANSWERED,
                f"You already declined {title}. Zamu has moved on.",
                ask.id, ask.duty_id, ask.person_id, name, title,
            )
        if ask.state is AskState.SUPERSEDED:
            return Response(
                ResponseOutcome.SUPERSEDED,
                f"{title} was covered by someone else. Thank you anyway.",
                ask.id, ask.duty_id, ask.person_id, name, title,
            )
        if ask.state is not AskState.SENT:
            return Response(
                ResponseOutcome.EXPIRED,
                f"This request for {title} is no longer open.",
                ask.id, ask.duty_id, ask.person_id, name, title,
            )
        if ask.is_expired(now):
            self.store.put_ask(replace(ask, state=AskState.EXPIRED, responded_at=now))
            return Response(
                ResponseOutcome.EXPIRED,
                f"This request for {title} expired and Zamu has asked someone else.",
                ask.id, ask.duty_id, ask.person_id, name, title,
            )

        if not accept:
            self.store.put_ask(replace(ask, state=AskState.DECLINED, responded_at=now))
            return Response(
                ResponseOutcome.DECLINED,
                f"Thanks for answering. Zamu will ask someone else about {title}.",
                ask.id, ask.duty_id, ask.person_id, name, title,
            )

        if duty is not None and duty.assigned_person_id not in (None, ask.person_id):
            self.store.put_ask(replace(ask, state=AskState.SUPERSEDED, responded_at=now))
            return Response(
                ResponseOutcome.SUPERSEDED,
                f"{title} was covered by someone else just before you answered.",
                ask.id, ask.duty_id, ask.person_id, name, title,
            )

        accepted = replace(ask, state=AskState.ACCEPTED, responded_at=now)
        self.store.put_ask(accepted)
        return self._write_assignment(accepted, name, title)

    def _write_assignment(self, ask: Ask, name: str, title: str) -> Response:
        """Put the acceptance onto the roster, then re-read to prove it landed."""
        now = self.clock.now()
        roster = self.roster(ask.org_id)
        duty = roster.duty(ask.duty_id)
        if duty is None:  # pragma: no cover - the ask cannot outlive its duty
            raise NotFound(f"no duty {ask.duty_id}")

        key = assignment_idempotency_key(duty.id, ask.person_id, ask.id)
        action = ProposedAction(
            org_id=ask.org_id,
            action_class=ActionClass.WRITE_ROSTER,
            summary=f"Assign {name} to {title}",
            person_id=ask.person_id,
            duty_id=duty.id,
            payload={"ask_id": ask.id, "idempotency_key": key},
        )
        decision = authorize(action, roster, now)

        if not decision.allowed:
            record = self._ledger.record_blocked(action, decision, key)
            return Response(
                ResponseOutcome.ACCEPTED_PENDING_COORDINATOR,
                f"{name} accepted {title}, but Zamu is not allowed to update the roster. "
                f"{decision.reason}",
                ask.id, duty.id, ask.person_id, name, title, record.id,
            )

        intended = {"duty_id": duty.id, "assigned_person_id": ask.person_id}
        entry = self._ledger.begin(action, decision, intended, key)
        if entry.replayed:
            return Response(
                ResponseOutcome.ACCEPTED_AND_ASSIGNED,
                f"{name} is already on {title}. Nothing was written twice.",
                ask.id, duty.id, ask.person_id, name, title, entry.record.id,
                verified=entry.record.result is ActionResult.VERIFIED,
            )

        self.store.put_duty(duty.assigned_to(ask.person_id, now))
        executed = self._ledger.mark_executed(entry.record)

        # The whole point: do not believe the write, go and look.
        reread = self.store.get_duty(ask.org_id, duty.id)
        observed = (
            {"duty_id": reread.id, "assigned_person_id": reread.assigned_person_id}
            if reread is not None
            else None
        )
        closed = self._ledger.close(executed, observed)

        if closed.result is not ActionResult.VERIFIED:
            return Response(
                ResponseOutcome.WRITE_FAILED,
                f"{name} accepted, but Zamu could not confirm the roster changed. {closed.detail}",
                ask.id, duty.id, ask.person_id, name, title, closed.id,
            )

        self._supersede_other_asks(ask)
        return Response(
            ResponseOutcome.ACCEPTED_AND_ASSIGNED,
            f"{name} is on {title}, confirmed on the roster.",
            ask.id, duty.id, ask.person_id, name, title, closed.id, verified=True,
        )

    def _supersede_other_asks(self, winning: Ask) -> None:
        """Close every other open question about a duty that is now covered."""
        now = self.clock.now()
        for other in self.store.list_asks(winning.org_id):
            if other.duty_id != winning.duty_id or other.id == winning.id:
                continue
            if other.state.is_open:
                self.store.put_ask(replace(other, state=AskState.SUPERSEDED, responded_at=now))

    # -- sweeping ----------------------------------------------------------------------

    def expire_lapsed(self, org_id: str) -> tuple[str, ...]:
        """Close asks whose window has passed. Silence is an answer with a deadline."""
        now = self.clock.now()
        expired: list[str] = []
        for ask in self.store.list_asks(org_id):
            if ask.state.is_open and ask.is_expired(now):
                self.store.put_ask(replace(ask, state=AskState.EXPIRED, responded_at=now))
                expired.append(ask.id)
        return tuple(expired)

    def sweep(self, org_id: str, *, horizon_days: int | None = 21, limit: int = 10) -> SweepResult:
        """One full pass: expire what lapsed, then advance every open gap by one ask."""
        now = self.clock.now()
        expired = self.expire_lapsed(org_id)
        outcomes = [
            self.ask_next(org_id, assessment.duty_id)
            for assessment in self.find_gaps(org_id, horizon_days=horizon_days)[:limit]
        ]
        return SweepResult(org_id, now, expired, tuple(outcomes))

    # -- helpers -----------------------------------------------------------------------

    def accept_url(self, token: str) -> str:
        return f"{self.base_url.rstrip('/')}/r/{token}/yes"

    def decline_url(self, token: str) -> str:
        return f"{self.base_url.rstrip('/')}/r/{token}/no"

    def _observe_ask(self, org_id: str, ask_id: str) -> dict[str, Any] | None:
        stored = self.store.get_ask(org_id, ask_id)
        if stored is None:
            return None
        return {
            "ask_id": stored.id,
            "duty_id": stored.duty_id,
            "person_id": stored.person_id,
            "state": stored.state.value,
        }

    def _require_duty(self, roster: Roster, duty_id: str) -> Duty:
        duty = roster.duty(duty_id)
        if duty is None:
            raise NotFound(f"no duty {duty_id} in {roster.org.id}")
        return duty

    @staticmethod
    def _open_ask_for(roster: Roster, duty_id: str, now: datetime) -> Ask | None:
        for ask in roster.asks_for_duty(duty_id):
            if ask.state.is_open and not ask.is_expired(now) and not ask.drafted_only:
                return ask
        return None

    def _best_available_action(self, roster: Roster) -> ActionClass:
        """The strongest rung currently granted, so ranking reflects real reach.

        This is why revoking the send grant changes the shortlist rather than only
        the outcome: with no send grant, candidates who never opted in to being
        contacted become valid again, because a human will be doing the contacting.
        """
        levels = granted_levels(roster, self.clock.now())
        return ActionClass.SEND_ASK if ActionClass.SEND_ASK in levels else ActionClass.DRAFT_ASK
