"""The tools the Strands agent is allowed to call.

Every tool is a thin, typed wrapper over `CoverageService`. None of them contain
judgement. That is the point of the whole design: the model chooses *which* tool to
call and in what order, and the answer it gets back was computed by ordinary Python
with tests behind it.

Tools are built by a factory rather than declared at module scope so that the service,
store and clock are bound explicitly. A module-level tool reaching for a global
service is how a multi-tenant agent ends up reading the wrong organisation's roster.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from strands import tool

from zamu.core.brief import build_brief
from zamu.core.coverage import assess_roster, coverage_summary
from zamu.core.errors import NotFound
from zamu.core.fairness import build_records, cohort_mean_load, describe_load
from zamu.core.fill import CoverageService
from zamu.core.messages import format_when
from zamu.core.models import ActionClass, CoverageState


@dataclass(frozen=True, slots=True)
class ToolAuthority:
    """What a tool needs before it may run.

    `degrades_to` exists because one tool deliberately does less rather than failing:
    `ask_next_person` falls back to preparing a draft when it may not send. The hook
    must not cancel that call, or revoking the send grant would break the fallback
    instead of exercising it.
    """

    required: ActionClass
    degrades_to: ActionClass | None = None

    @property
    def acceptable(self) -> tuple[ActionClass, ...]:
        return (self.required,) if self.degrades_to is None else (self.required, self.degrades_to)


#: Which rung of the trust ladder each tool sits on. The authority hook reads this to
#: decide what to block, so a tool that is missing from this table is refused outright
#: rather than allowed — omission fails closed.
TOOL_AUTHORITY: dict[str, ToolAuthority] = {
    "list_gaps": ToolAuthority(ActionClass.READ),
    "read_duty": ToolAuthority(ActionClass.READ),
    "find_person": ToolAuthority(ActionClass.READ),
    "rank_candidates": ToolAuthority(ActionClass.READ),
    "read_receipts": ToolAuthority(ActionClass.READ),
    "read_fairness_ledger": ToolAuthority(ActionClass.READ),
    "write_handover_brief": ToolAuthority(ActionClass.READ),
    "expire_lapsed_asks": ToolAuthority(ActionClass.READ),
    "ask_next_person": ToolAuthority(ActionClass.SEND_ASK, degrades_to=ActionClass.DRAFT_ASK),
    "record_withdrawal": ToolAuthority(ActionClass.WRITE_ROSTER),
}

#: Tools that change something outside the agent's own head.
MUTATING_TOOLS = frozenset(
    name for name, auth in TOOL_AUTHORITY.items() if auth.required is not ActionClass.READ
)


def build_tools(service: CoverageService, org_id: str) -> list[Any]:
    """Bind the toolset to one organisation and return it for `Agent(tools=...)`."""

    def now() -> datetime:
        return service.clock.now()

    @tool
    def list_gaps(horizon_days: int = 21) -> dict:
        """Find duties that need somebody found for them, soonest first.

        Use this first, every time. It is the only way to learn what needs doing.

        Args:
            horizon_days: How far ahead to look. Defaults to three weeks.
        """
        roster = service.roster(org_id)
        found = service.find_gaps(org_id, horizon_days=horizon_days)
        return {
            "org": roster.org.name,
            "now": now().isoformat(),
            "coverage": coverage_summary(roster, now()),
            "gaps": [
                {
                    "duty_id": g.duty_id,
                    "title": roster.duty(g.duty_id).title,
                    "when": format_when(roster.duty(g.duty_id), roster.org.timezone),
                    "role": roster.duty(g.duty_id).role,
                    "state": g.state.value,
                    "why": g.reason,
                }
                for g in found
            ],
            "at_risk": [
                {
                    "duty_id": a.duty_id,
                    "title": roster.duty(a.duty_id).title,
                    "why": a.reason,
                }
                for a in assess_roster(roster, now())
                if a.state is CoverageState.AT_RISK
            ],
        }

    @tool
    def read_duty(duty_id: str) -> dict:
        """Read one duty in full, including who is on it and whether they confirmed.

        Args:
            duty_id: The duty's id, as returned by list_gaps.
        """
        roster = service.roster(org_id)
        duty = roster.duty(duty_id)
        if duty is None:
            return {"error": f"No duty {duty_id} on this roster."}
        holder = roster.person(duty.assigned_person_id) if duty.assigned_person_id else None
        return {
            "duty_id": duty.id,
            "title": duty.title,
            "when": format_when(duty, roster.org.timezone),
            "starts_at": duty.start.isoformat(),
            "role": duty.role,
            "required_qualification": duty.required_qualification,
            "minimum_notice_hours": duty.min_notice.total_seconds() / 3600,
            "assigned_to": holder.name if holder else None,
            "assigned_person_id": duty.assigned_person_id,
            "confirmed_at": duty.confirmed_at.isoformat() if duty.confirmed_at else None,
            "cancelled": duty.cancelled,
        }

    @tool
    def find_person(query: str) -> dict:
        """Resolve a name, first name, or email to a person on this roster.

        Use this to turn a name mentioned in a message into an id before acting on it.
        Returns every plausible match rather than guessing when a name is ambiguous.

        Args:
            query: A name, partial name, or email address.
        """
        roster = service.roster(org_id)
        needle = query.strip().lower()
        matches = [
            p
            for p in roster.people
            if needle in p.name.lower() or needle == p.email.lower()
        ]
        return {
            "query": query,
            "match_count": len(matches),
            "matches": [
                {
                    "person_id": p.id,
                    "name": p.name,
                    "email": p.email,
                    "qualifications": sorted(p.qualifications),
                    "active": p.active,
                    "opted_in_to_direct_contact": ActionClass.SEND_ASK in p.opt_ins,
                    "assigned_duties": [
                        {"duty_id": d.id, "title": d.title,
                         "when": format_when(d, roster.org.timezone)}
                        for d in roster.duties_for(p.id)
                        if d.start >= now()
                    ],
                }
                for p in matches
            ],
        }

    @tool
    def rank_candidates(duty_id: str) -> dict:
        """Work out who should be asked to cover a duty, and why, in order.

        The ordering is computed deterministically from fairness debt, qualification
        fit, response history, notice and rest. Do not reorder it or substitute your
        own judgement — report it. It also returns everybody who was ruled out and the
        reason, which is what the coordinator will ask about first.

        Args:
            duty_id: The duty to fill.
        """
        try:
            order = service.rank_for(org_id, duty_id)
        except NotFound as exc:
            return {"error": str(exc)}
        return {
            "duty_id": duty_id,
            "team_average_load_hours": order.mean_load,
            "candidates": [
                {
                    "rank": i + 1,
                    "person_id": c.person_id,
                    "name": c.person_name,
                    "score": round(c.score, 4),
                    "why": c.rationale,
                    "load": c.load_summary,
                    "fairness_debt_hours": c.debt_hours,
                    "components": c.components.as_dict(),
                }
                for i, c in enumerate(order.candidates)
            ],
            "excluded": [
                {"person_id": e.person_id, "name": e.person_name, "why": e.explanation}
                for e in order.excluded
            ],
        }

    @tool
    def ask_next_person(duty_id: str) -> dict:
        """Ask the single fairest qualified person to cover a duty.

        Asks exactly one person. Never call this repeatedly to reach more people at
        once — if the answer is 'waiting', the correct action is to wait. If it comes
        back 'no_candidates' or 'blocked', stop and tell the coordinator; do not try
        to work around it.

        Args:
            duty_id: The duty to fill.
        """
        try:
            return service.ask_next(org_id, duty_id).as_dict()
        except NotFound as exc:
            return {"error": str(exc)}

    @tool
    def record_withdrawal(person_id: str, duty_id: str, evidence: str) -> dict:
        """Record that a named person has dropped off a duty they were assigned to.

        Only call this when a person has clearly said they cannot make a specific
        shift. If either the person or the shift is ambiguous, use find_person and
        read_duty first, and if it is still ambiguous, say so instead of guessing.

        Args:
            person_id: The person withdrawing, from find_person.
            duty_id: The duty they are dropping.
            evidence: The words they actually used, quoted, for the receipt.
        """
        return service.record_withdrawal(org_id, duty_id, person_id, evidence).as_dict()

    @tool
    def expire_lapsed_asks() -> dict:
        """Close any asks whose response window has passed, so the duty can move on."""
        expired = service.expire_lapsed(org_id)
        return {"expired_count": len(expired), "expired_ask_ids": list(expired)}

    @tool
    def read_fairness_ledger() -> dict:
        """Read how much each volunteer has actually carried recently.

        Use this when explaining a ranking, or when the coordinator asks who is
        doing too much.
        """
        roster = service.roster(org_id)
        records = build_records(roster, now())
        mean = cohort_mean_load(records, roster.org, {p.id for p in roster.people if p.active})
        return {
            "window_weeks": int(roster.org.fairness_window.days / 7),
            "team_average_load_hours": round(mean, 2),
            "people": sorted(
                (
                    {
                        "person_id": p.id,
                        "name": p.name,
                        "shifts_carried": records[p.id].shifts_carried,
                        "hours_carried": records[p.id].hours_carried,
                        "unsociable_hours": records[p.id].unsociable_hours_carried,
                        "asks_received": records[p.id].asks_sent,
                        "declines": records[p.id].declines,
                        "summary": describe_load(records[p.id], roster.org, mean),
                    }
                    for p in roster.people
                    if p.id in records
                ),
                key=lambda row: -row["hours_carried"],
            ),
        }

    @tool
    def read_receipts(limit: int = 10) -> dict:
        """Read what Zamu has recently done, tried, or been refused.

        Every entry records what was intended, what was observed after re-reading the
        target, and which policy rule permitted or refused it.

        Args:
            limit: How many entries to return, newest first.
        """
        return {
            "receipts": [
                {
                    "action_id": r.id,
                    "at": r.created_at.isoformat(),
                    "action": r.action_class.label,
                    "summary": r.summary,
                    "policy_rule": r.policy_rule,
                    "result": r.result.value if r.result else "in progress",
                    "intended": r.intended,
                    "observed": r.observed,
                    "detail": r.detail,
                }
                for r in service.ledger.recent(org_id, limit=limit)
            ]
        }

    @tool
    def write_handover_brief(hours: int = 24) -> dict:
        """Assemble what the coordinator needs to know, and nothing else.

        Call this last. If it comes back with needs_human false, say so plainly and
        do not manufacture something to report.

        Args:
            hours: How far back to summarise.
        """
        from datetime import timedelta

        moment = now()
        brief = build_brief(
            service.store, org_id, moment, since=moment - timedelta(hours=hours)
        )
        payload = brief.as_dict()
        payload["text"] = brief.to_text()
        return payload

    return [
        list_gaps,
        read_duty,
        find_person,
        rank_candidates,
        ask_next_person,
        record_withdrawal,
        expire_lapsed_asks,
        read_fairness_ledger,
        read_receipts,
        write_handover_brief,
    ]
