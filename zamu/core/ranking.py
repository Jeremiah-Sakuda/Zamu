"""Deciding who to ask first.

The model never computes this. It calls it. That distinction is the whole reason a
coordinator can be shown the ranking and told, truthfully, that the same inputs will
always produce the same order.

Five components, explicitly weighted, each in 0..1 so the weights mean what they
look like. Fairness carries the most weight on purpose: an agent that optimises only
for speed of fill will quietly destroy the organization it serves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from zamu.core.clock import utc
from zamu.core.eligibility import Eligibility, evaluate_all
from zamu.core.fairness import (
    build_records,
    cohort_mean_load,
    describe_load,
    fairness_debt,
    normalised_debt,
)
from zamu.core.models import (
    ActionClass,
    Duty,
    FairnessRecord,
    Person,
    Roster,
)

WEIGHT_FAIRNESS = 0.45
WEIGHT_FIT = 0.20
WEIGHT_RESPONSIVENESS = 0.20
WEIGHT_NOTICE = 0.10
WEIGHT_REST = 0.05

#: Notice beyond this adds nothing to the score. Three days is comfortable.
NOTICE_SATURATION = timedelta(hours=72)
#: Time since the last ask beyond which a person is considered fully rested.
REST_SATURATION = timedelta(days=14)
#: Number of past duties in the same role after which familiarity is maxed out.
FAMILIARITY_SATURATION = 3
#: How much of the fit score familiarity may account for.
#:
#: Kept deliberately small. Familiarity points at whoever has done the role most, which
#: is precisely the person fairness is trying to protect, so it belongs in the ranking
#: as a tiebreak and not as a driver. At 0.4 it was strong enough to overturn a two-hour
#: fairness gap, which is the incumbent behaviour this product exists to replace.
FAMILIARITY_SHARE = 0.2

#: Pseudo-observations of average behaviour mixed into every response history.
#:
#: One accepted ask is not a reputation. Without this, a volunteer who has been asked
#: once and said yes scores a perfect 1.0 and outranks somebody who has never been
#: asked at all — which is exactly how new volunteers stay invisible and then leave.
RESPONSE_PRIOR_STRENGTH = 3.0
RESPONSE_PRIOR_MEAN = 0.5


@dataclass(frozen=True, slots=True)
class Components:
    """The five parts of a candidate's score, kept separate so they can be shown."""

    fairness: float
    fit: float
    responsiveness: float
    notice: float
    rest: float

    @property
    def total(self) -> float:
        return round(
            self.fairness * WEIGHT_FAIRNESS
            + self.fit * WEIGHT_FIT
            + self.responsiveness * WEIGHT_RESPONSIVENESS
            + self.notice * WEIGHT_NOTICE
            + self.rest * WEIGHT_REST,
            6,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "fairness": round(self.fairness, 4),
            "fit": round(self.fit, 4),
            "responsiveness": round(self.responsiveness, 4),
            "notice": round(self.notice, 4),
            "rest": round(self.rest, 4),
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    """One eligible person, scored and explained."""

    person_id: str
    person_name: str
    score: float
    components: Components
    debt_hours: float
    rationale: str
    load_summary: str
    contactable_from: datetime | None
    asks_remaining: int


@dataclass(frozen=True, slots=True)
class Excluded:
    """One person who cannot take the duty, and why. Shown so the ranking is auditable."""

    person_id: str
    person_name: str
    explanation: str


@dataclass(frozen=True, slots=True)
class CandidateOrder:
    """The full ranking decision for one duty."""

    duty_id: str
    candidates: tuple[Candidate, ...]
    excluded: tuple[Excluded, ...]
    mean_load: float
    computed_at: datetime

    @property
    def first(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _fit_score(person: Person, duty: Duty, roster: Roster) -> float:
    """Qualification match plus demonstrated familiarity with this role."""
    required = duty.required_qualification
    qualified = 1.0 if (required is None or required in person.qualifications) else 0.0

    past_same_role = sum(
        1 for d in roster.duties_for(person.id) if d.role == duty.role and d.id != duty.id
    )
    familiarity = _clamp(past_same_role / FAMILIARITY_SATURATION)
    return _clamp((1.0 - FAMILIARITY_SHARE) * qualified + FAMILIARITY_SHARE * familiarity)


def _shrink(successes: float, trials: float) -> float:
    """A rate regressed toward 0.5 in proportion to how little evidence there is.

    With no history this returns exactly 0.5; with one success it returns 0.625, not
    1.0; and it converges on the observed rate as the record grows. This is the
    difference between "reliable" and "has answered the phone once".
    """
    return (successes + RESPONSE_PRIOR_STRENGTH * RESPONSE_PRIOR_MEAN) / (
        trials + RESPONSE_PRIOR_STRENGTH
    )


def _responsiveness_score(record: FairnessRecord) -> float:
    """How reliably this person answers when asked.

    Someone who has never been asked scores neutral rather than badly. Penalising an
    absence of history is how new volunteers stay invisible and then leave — and so,
    just as surely, is treating a single yes as proof of dependability.
    """
    if record.asks_sent == 0:
        return 0.5
    acceptance = _shrink(record.accepts, record.responses)
    response = _shrink(record.responses, record.asks_sent)
    return _clamp(0.6 * acceptance + 0.4 * response)


def _notice_score(duty: Duty, eligibility: Eligibility, now: datetime) -> float:
    """How much warning this specific person would actually get.

    Uses `contactable_from` rather than `now`, so a candidate who cannot be reached
    until 8am tomorrow is scored on the notice they would really receive.
    """
    reachable = eligibility.contactable_from or utc(now)
    available = duty.start - reachable
    if available <= timedelta(0):
        return 0.0
    return _clamp(available / NOTICE_SATURATION)


def _rest_score(record: FairnessRecord, now: datetime) -> float:
    """How long since this person was last asked anything at all."""
    if record.last_asked_at is None:
        return 1.0
    since = utc(now) - utc(record.last_asked_at)
    if since <= timedelta(0):
        return 0.0
    return _clamp(since / REST_SATURATION)


def _rationale(person: Person, duty: Duty, components: Components, load_summary: str) -> str:
    """The single sentence Zamu says out loud about why this person is first.

    Built from whichever components actually drove the decision, so the sentence is
    a description of the arithmetic rather than a flattering paraphrase of it.
    """
    clauses: list[str] = []

    required = duty.required_qualification
    if required and required in person.qualifications:
        clauses.append(f"is trained for {required}")
    elif components.fit >= 0.7:
        clauses.append(f"has covered {duty.role} before")

    if components.fairness >= 0.65:
        clauses.append(load_summary)
    elif components.fairness <= 0.35:
        clauses.append(f"{load_summary}, so is not the fairest pick")

    if components.rest >= 0.99 and components.responsiveness == 0.5:
        clauses.append("has not been asked anything yet")
    elif components.responsiveness >= 0.8:
        clauses.append("answers reliably")

    if not clauses:
        clauses.append("is eligible and available")

    head, tail = clauses[:-1], clauses[-1]
    body = f"{', '.join(head)} and {tail}" if head else tail
    return f"{person.name} {body}."


def rank(
    duty: Duty,
    roster: Roster,
    now: datetime,
    *,
    for_action: ActionClass = ActionClass.SEND_ASK,
    records: dict[str, FairnessRecord] | None = None,
    limit: int | None = None,
) -> CandidateOrder:
    """Produce the ordered list of who to ask, and the audit trail of who was excluded.

    Deterministic all the way down: ties break on fairness debt, then on who has gone
    longest without being asked, then on person id. The same roster and the same
    instant always produce the same order.
    """
    moment = utc(now)
    records = records if records is not None else build_records(roster, moment)
    eligibilities = evaluate_all(duty, roster, records, moment, for_action=for_action)

    eligible_ids = {pid for pid, e in eligibilities.items() if e.eligible}
    mean_load = cohort_mean_load(records, roster.org, eligible_ids or None)

    debts = {
        pid: fairness_debt(records[pid], roster.org, mean_load)
        for pid in eligible_ids
        if pid in records
    }
    spread = max((abs(d) for d in debts.values()), default=0.0)

    candidates: list[Candidate] = []
    excluded: list[Excluded] = []

    for person in roster.people:
        eligibility = eligibilities.get(person.id)
        record = records.get(person.id)
        if eligibility is None or record is None:
            continue

        if not eligibility.eligible:
            excluded.append(Excluded(person.id, person.name, eligibility.explain(person.name)))
            continue

        components = Components(
            fairness=normalised_debt(debts[person.id], spread),
            fit=_fit_score(person, duty, roster),
            responsiveness=_responsiveness_score(record),
            notice=_notice_score(duty, eligibility, moment),
            rest=_rest_score(record, moment),
        )
        load_summary = describe_load(record, roster.org, mean_load)
        candidates.append(
            Candidate(
                person_id=person.id,
                person_name=person.name,
                score=components.total,
                components=components,
                debt_hours=debts[person.id],
                rationale=_rationale(person, duty, components, load_summary),
                load_summary=load_summary,
                contactable_from=eligibility.contactable_from,
                asks_remaining=eligibility.asks_remaining,
            )
        )

    candidates.sort(
        key=lambda c: (
            -c.score,
            -c.debt_hours,
            _last_asked_sort_key(records[c.person_id], moment),
            c.person_id,
        )
    )
    excluded.sort(key=lambda e: e.person_id)

    if limit is not None:
        candidates = candidates[:limit]

    return CandidateOrder(
        duty_id=duty.id,
        candidates=tuple(candidates),
        excluded=tuple(excluded),
        mean_load=round(mean_load, 4),
        computed_at=moment,
    )


def _last_asked_sort_key(record: FairnessRecord, now: datetime) -> float:
    """Longest-since-asked sorts first. Never-asked sorts first of all."""
    if record.last_asked_at is None:
        return float("-inf")
    return -(utc(now) - utc(record.last_asked_at)).total_seconds()
