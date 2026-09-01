"""The jobs that make Zamu an agent rather than a button.

Nothing in this file decides anything. It exists because coverage breaks continuously
and a coordinator should not have to remember to check: EventBridge wakes Zamu on a
schedule, Zamu runs the same sweep the console's button runs, and if there is nothing
to do it says so and goes back to sleep.

Three schedules, deliberately separate so they can be tuned independently:

* `sweep`   — every 15 minutes. Expire lapsed asks and advance every open gap by one
              ask. This is the loop.
* `brief`   — once a day, at the hour the coordinator chose. Send the handover, but
              only when there is something in it.
* `risk`    — once an hour. Notice duties drifting into at-risk before the day of.

Each handler is a thin wrapper so the same code is reachable from Lambda, from a cron
job on a small server, or from `zamu sweep` in a terminal.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from zamu.config import Settings, build_notifier, build_store, ensure_seeded, load_settings
from zamu.core.brief import build_brief
from zamu.core.clock import SystemClock
from zamu.core.coverage import assess_roster
from zamu.core.fill import CoverageService
from zamu.core.models import CoverageState
from zamu.infra.notify import Message

log = logging.getLogger("zamu.scheduled")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)


def _wiring(settings: Settings | None = None):
    settings = settings or load_settings()
    clock = SystemClock()
    store = build_store(settings)
    ensure_seeded(store, settings, clock)
    notifier = build_notifier(settings)
    service = CoverageService(store, clock, notifier, base_url=settings.base_url)
    return settings, store, notifier, service, clock


def _org_ids(store, settings: Settings, event: dict[str, Any]) -> list[str]:
    """Which organizations this run covers.

    An explicit `org_id` in the event wins; otherwise every organization in the store,
    so adding a second one does not require touching the schedule.
    """
    if event.get("org_id"):
        return [str(event["org_id"])]
    orgs = [o.id for o in store.list_orgs()]
    return orgs or [settings.org_id]


# -- the loop --------------------------------------------------------------------------


def run_sweep(event: dict[str, Any] | None = None, settings: Settings | None = None) -> dict:
    """Expire what lapsed, then advance every open gap by exactly one ask."""
    event = event or {}
    settings, store, _notifier, service, _clock = _wiring(settings)
    horizon = int(event.get("horizon_days") or settings.sweep_horizon_days)

    results = []
    for org_id in _org_ids(store, settings, event):
        result = service.sweep(org_id, horizon_days=horizon)
        results.append(result.as_dict())
        log.info(
            json.dumps(
                {
                    "job": "sweep",
                    "org": org_id,
                    "expired": len(result.expired),
                    "asked": len(result.asked),
                    "needs_coordinator": len(result.needing_coordinator),
                }
            )
        )
    return {"ok": True, "job": "sweep", "results": results}


# -- the daily handover ----------------------------------------------------------------


def run_brief(event: dict[str, Any] | None = None, settings: Settings | None = None) -> dict:
    """Send the handover brief, but only when it contains something.

    An agent that emails a coordinator every morning to say nothing happened has
    replaced one chore with another. Silence is the correct output most days.
    """
    event = event or {}
    settings, store, notifier, _service, clock = _wiring(settings)
    hours = int(event.get("hours") or 24)
    force = bool(event.get("force"))
    to = event.get("to")

    sent = []
    for org_id in _org_ids(store, settings, event):
        now = clock.now()
        brief = build_brief(store, org_id, now, since=now - timedelta(hours=hours))

        if not brief.worth_sending and not force:
            log.info(
                json.dumps({"job": "brief", "org": org_id, "sent": False, "reason": "nothing new"})
            )
            continue

        recipient = to or event.get("coordinator_email")
        if not recipient:
            log.info(
                json.dumps({"job": "brief", "org": org_id, "sent": False, "reason": "no recipient"})
            )
            continue

        delivery = notifier.send(
            Message(
                to_email=str(recipient),
                to_name="Coordinator",
                subject=_subject(brief),
                text=brief.to_text(),
                kind="brief",
                org_id=org_id,
            )
        )
        sent.append({"org_id": org_id, "delivered": delivery.ok, "needs_human": brief.needs_human})
        log.info(
            json.dumps(
                {
                    "job": "brief",
                    "org": org_id,
                    "sent": delivery.ok,
                    "needs_human": brief.needs_human,
                }
            )
        )

    return {"ok": True, "job": "brief", "sent": sent}


def _subject(brief) -> str:
    """Say in the subject line whether this needs opening."""
    if brief.needs_human:
        count = len(brief.needs_decision) + len(brief.not_allowed)
        thing = "thing" if count == 1 else "things"
        return f"{brief.org_name}: {count} {thing} need you"
    if brief.filled:
        count = len(brief.filled)
        shift = "shift" if count == 1 else "shifts"
        return f"{brief.org_name}: {count} {shift} filled, nothing needs you"
    return f"{brief.org_name}: coverage update"


# -- the early warning -----------------------------------------------------------------


def run_risk_check(event: dict[str, Any] | None = None, settings: Settings | None = None) -> dict:
    """Report duties that have drifted into at-risk or unconfirmed.

    Deliberately a report rather than an action. A stale confirmation is a question for
    the person who made the promise, and Zamu has no grant to nag them.
    """
    event = event or {}
    settings, store, _notifier, service, clock = _wiring(settings)
    now = clock.now()

    at_risk = []
    for org_id in _org_ids(store, settings, event):
        roster = store.load_roster(org_id)
        for assessment in assess_roster(roster, now):
            duty = roster.duty(assessment.duty_id)
            if duty is None or duty.start <= now or duty.cancelled:
                continue
            if assessment.state in (CoverageState.AT_RISK, CoverageState.UNKNOWN):
                at_risk.append(
                    {
                        "org_id": org_id,
                        "duty_id": duty.id,
                        "title": duty.title,
                        "state": assessment.state.value,
                        "why": assessment.reason,
                        "starts_at": duty.start.isoformat(),
                    }
                )

    log.info(json.dumps({"job": "risk", "count": len(at_risk)}))
    return {"ok": True, "job": "risk", "at_risk": at_risk}


# -- Lambda entrypoints ----------------------------------------------------------------

JOBS = {"sweep": run_sweep, "brief": run_brief, "risk": run_risk_check}


def handler(event: dict[str, Any] | None, context: Any = None) -> dict:
    """One Lambda, three schedules, chosen by `job` in the EventBridge input.

    A single function keeps the deployment small and means all three share one warm
    container and one connection to the store.
    """
    event = event or {}
    job = str(event.get("job") or "sweep").lower()
    runner = JOBS.get(job)
    if runner is None:
        return {"ok": False, "error": f"unknown job {job!r}", "known": sorted(JOBS)}
    return runner(event)
