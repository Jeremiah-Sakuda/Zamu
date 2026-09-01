"""`zamu` — drive the whole loop from a terminal.

Exists for three reasons. It is how the core loop was proved before any interface
existed; it is the fastest way for somebody evaluating this repository to watch a fill
happen without deploying anything; and it keeps the console honest, because every
screen in the web app is backed by an operation you can also run here and check.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from zamu.core.brief import build_brief
from zamu.core.clock import SystemClock
from zamu.core.coverage import assess_roster, coverage_summary
from zamu.core.errors import NotFound, ZamuError
from zamu.core.fairness import build_records, cohort_mean_load, describe_load
from zamu.core.fill import CoverageService
from zamu.core.messages import format_when
from zamu.core.models import ActionClass, CoverageState, Grant, Org
from zamu.demo import DEMO_ORG_ID, seed
from zamu.infra.notify import OutboxNotifier
from zamu.infra.sqlite_store import SqliteStore

DEFAULT_DB = Path(os.environ.get("ZAMU_DB", ".zamu/zamu.sqlite"))
DEFAULT_BASE_URL = os.environ.get("ZAMU_BASE_URL", "http://localhost:8000")

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
GREEN = "\033[32m"
AMBER = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

STATE_COLOUR = {
    CoverageState.COVERED: GREEN,
    CoverageState.AT_RISK: AMBER,
    CoverageState.UNCOVERED: RED,
    CoverageState.UNKNOWN: BLUE,
}


def _plain() -> bool:
    return not sys.stdout.isatty() or os.environ.get("NO_COLOR") is not None


def paint(text: str, colour: str) -> str:
    return text if _plain() else f"{colour}{text}{RESET}"


def heading(text: str) -> str:
    return paint(text, BOLD)


def muted(text: str) -> str:
    return paint(text, DIM)


# -- wiring ----------------------------------------------------------------------------


def open_service(args) -> tuple[CoverageService, str]:
    store = SqliteStore(args.db)
    notifier = OutboxNotifier(directory=Path(args.db).parent / "outbox")
    service = CoverageService(store, SystemClock(), notifier, base_url=args.base_url)
    return service, args.org


# -- commands --------------------------------------------------------------------------


def cmd_demo(args) -> int:
    """Seed the demonstration organisation into a fresh database."""
    path = Path(args.db)
    if args.reset and path.exists():
        path.unlink()
        for extra in (path.with_suffix(".sqlite-wal"), path.with_suffix(".sqlite-shm")):
            extra.unlink(missing_ok=True)

    store = SqliteStore(args.db)
    org_id = seed(store, datetime.now(UTC), send=not args.no_send, write=not args.no_write)
    roster = store.load_roster(org_id)

    print(heading(f"Seeded {roster.org.name}"))
    print(f"  database   {args.db}")
    print(f"  org        {org_id}")
    print(f"  people     {len(roster.people)}")
    print(f"  duties     {len(roster.duties)}")
    print(f"  grants     {', '.join(g.action_class.name for g in roster.grants)}")
    print()
    print(muted(f"  Next: zamu status --org {org_id}"))
    return 0


def cmd_status(args) -> int:
    """The roster, coloured by how confident Zamu is that each duty is covered."""
    service, org_id = open_service(args)
    roster = service.roster(org_id)
    now = service.clock.now()
    summary = coverage_summary(roster, now)

    print(heading(roster.org.name))
    print(
        "  "
        + "   ".join(
            paint(f"{summary[state.value]} {state.value.replace('_', ' ')}", STATE_COLOUR[state])
            for state in CoverageState
            if summary[state.value]
        )
    )
    print()

    for assessment in assess_roster(roster, now):
        duty = roster.duty(assessment.duty_id)
        if duty.end < now - timedelta(days=1):
            continue
        holder = roster.person(duty.assigned_person_id) if duty.assigned_person_id else None
        marker = paint("●", STATE_COLOUR[assessment.state])
        print(f"  {marker} {format_when(duty, roster.org.timezone):32} {duty.title:24} "
              f"{holder.name if holder else muted('nobody')}")
        if assessment.state is not CoverageState.COVERED:
            print(f"      {muted(assessment.reason)}")
    return 0


def cmd_gaps(args) -> int:
    """What needs a human found for it."""
    service, org_id = open_service(args)
    roster = service.roster(org_id)
    found = service.find_gaps(org_id, horizon_days=args.horizon)

    if not found:
        print("No gaps inside the horizon. Coverage is holding.")
        return 0

    print(heading(f"{len(found)} gap{'s' if len(found) != 1 else ''}"))
    for gap in found:
        duty = roster.duty(gap.duty_id)
        print(f"  {paint('●', STATE_COLOUR[gap.state])} {duty.title} — "
              f"{format_when(duty, roster.org.timezone)}")
        print(f"      {muted(gap.reason)}")
        print(f"      {muted(gap.duty_id)}")
    return 0


def cmd_rank(args) -> int:
    """Who Zamu would ask, in order, and everybody it ruled out."""
    service, org_id = open_service(args)
    duty_id = args.duty or _first_gap(service, org_id)
    order = service.rank_for(org_id, duty_id)
    roster = service.roster(org_id)
    duty = roster.duty(duty_id)

    print(heading(f"{duty.title} — {format_when(duty, roster.org.timezone)}"))
    print(muted(f"  team average load {order.mean_load:.1f}h"))
    print()

    if not order.candidates:
        print(paint("  Nobody Zamu is allowed to ask can cover this.", RED))
    for i, c in enumerate(order.candidates, 1):
        print(f"  {i}. {heading(c.person_name)}  {muted(f'score {c.score:.3f}')}")
        print(f"     {c.rationale}")
        parts = " ".join(f"{k} {v:.2f}" for k, v in c.components.as_dict().items())
        print(f"     {muted(parts)}")

    if order.excluded:
        print()
        print(muted("  Not asked:"))
        for e in order.excluded:
            print(muted(f"    · {e.explanation}"))
    return 0


def cmd_fill(args) -> int:
    """Ask the next person about one duty."""
    service, org_id = open_service(args)
    duty_id = args.duty or _first_gap(service, org_id)
    _report_outcome(service.ask_next(org_id, duty_id))
    return 0


def cmd_sweep(args) -> int:
    """Expire what lapsed, then advance every open gap by one ask."""
    service, org_id = open_service(args)
    result = service.sweep(org_id, horizon_days=args.horizon)

    if result.expired:
        print(muted(f"Expired {len(result.expired)} unanswered ask(s)."))
    if not result.outcomes:
        print("Nothing to do. Coverage is holding.")
    for outcome in result.outcomes:
        _report_outcome(outcome)
    return 0


def cmd_withdraw(args) -> int:
    """Record that somebody has dropped off a duty."""
    service, org_id = open_service(args)
    person = _resolve_person(service, org_id, args.person)
    _report_outcome(
        service.record_withdrawal(org_id, args.duty, person.id, args.evidence)
    )
    return 0


def cmd_outbox(args) -> int:
    """Everything Zamu has sent, with the one-tap links a volunteer would see."""
    service, org_id = open_service(args)
    roster = service.roster(org_id)
    live = [a for a in roster.asks if a.state.is_open and not a.drafted_only]

    if not live:
        print("No open asks.")
        return 0

    for ask in live:
        person = roster.person(ask.person_id)
        duty = roster.duty(ask.duty_id)
        print(heading(f"To {person.name} <{person.email}>"))
        print(f"  {duty.title} — {format_when(duty, roster.org.timezone)}")
        print(f"  {muted(ask.rationale)}")
        print(f"  {paint('accept ', GREEN)} zamu accept {ask.token}")
        print(f"  {paint('decline', RED)} zamu decline {ask.token}")
        print()
    return 0


def cmd_respond(args, *, accept: bool) -> int:
    """Answer an ask as the volunteer would, by tapping the link."""
    service, _ = open_service(args)
    response = service.record_response(args.token, accept=accept)
    colour = GREEN if response.verified else AMBER
    print(paint(response.outcome.value.replace("_", " "), colour))
    print(f"  {response.detail}")
    if response.action_id:
        print(muted(f"  receipt {response.action_id}"))
    return 0


def cmd_brief(args) -> int:
    """What Zamu would tell the coordinator, and nothing else."""
    service, org_id = open_service(args)
    now = service.clock.now()
    brief = build_brief(service.store, org_id, now, since=now - timedelta(hours=args.hours))
    print(brief.to_text())
    return 0


def cmd_fairness(args) -> int:
    """Who has actually carried what."""
    service, org_id = open_service(args)
    roster = service.roster(org_id)
    now = service.clock.now()
    records = build_records(roster, now)
    mean = cohort_mean_load(records, roster.org, {p.id for p in roster.people if p.active})

    print(heading(f"Load over {int(roster.org.fairness_window.days / 7)} weeks"))
    print(muted(f"  team average {mean:.1f}h weighted"))
    print()
    for person in sorted(roster.people, key=lambda p: -records[p.id].hours_carried):
        record = records[person.id]
        bar = "█" * min(40, int(record.hours_carried))
        print(f"  {person.name:22} {record.hours_carried:5.1f}h  {bar}")
        print("  " + muted(f"{'':22}{describe_load(record, roster.org, mean)}"))
    return 0


def cmd_receipts(args) -> int:
    """The ledger: what was intended, what was observed, which rule allowed it."""
    service, org_id = open_service(args)
    for record in service.ledger.recent(org_id, limit=args.limit):
        result = record.result.value if record.result else "in progress"
        colour = {"verified": GREEN, "blocked": AMBER, "failed": RED, "conflicted": RED}.get(
            result, DIM
        )
        print(f"{paint(f'{result.upper():10}', colour)} {record.summary}")
        stamp = record.created_at.isoformat(timespec="seconds")
        print(muted(f"  rule {record.policy_rule} · {stamp}"))
        if record.detail:
            print(muted(f"  {record.detail}"))
        print()
    return 0


def cmd_grants(args) -> int:
    """The trust ladder as it currently stands."""
    service, org_id = open_service(args)
    roster = service.roster(org_id)
    now = service.clock.now()
    active = {g.action_class for g in roster.grants if g.is_active(now)}

    for level in ActionClass:
        if level is ActionClass.READ:
            mark, note = paint("on", GREEN), "granted by connecting the roster"
        elif level is ActionClass.REASSIGN_WITHOUT_CONSENT:
            mark, note = paint("never", DIM), "not implemented, and never will be"
        elif level in active:
            mark, note = paint("on", GREEN), "granted"
        else:
            mark, note = paint("off", DIM), "not granted"
        print(f"  {int(level)}  {level.label:28} {mark:16} {muted(note)}")
    return 0


def cmd_grant(args, *, revoke: bool) -> int:
    """Grant or revoke one rung of the ladder."""
    service, org_id = open_service(args)
    level = _parse_level(args.level)
    now = service.clock.now()

    if level in (ActionClass.READ, ActionClass.REASSIGN_WITHOUT_CONSENT):
        print(f"{level.label} is not a grant you can change.")
        return 1

    existing = [
        g for g in service.store.list_grants(org_id) if g.action_class is level and g.is_active(now)
    ]
    if revoke:
        if not existing:
            print(f"{level.label} was not granted.")
            return 0
        from dataclasses import replace

        for grant in existing:
            service.store.put_grant(replace(grant, revoked_at=now))
        print(f"Revoked: {level.label}.")
        return 0

    if existing:
        print(f"{level.label} is already granted.")
        return 0

    from zamu.core.ids import new_id

    service.store.put_grant(
        Grant(
            id=new_id("gr"),
            org_id=org_id,
            action_class=level,
            granted_by=args.by,
            granted_at=now,
            note=args.note,
        )
    )
    print(f"Granted: {level.label}.")
    return 0


def cmd_new_org(args) -> int:
    """Create an organization. The first thing a real coordinator does."""
    from zamu.core.ids import new_id

    store = SqliteStore(args.db)
    org_id = args.id or new_id("org")
    store.put_org(Org(id=org_id, name=args.name, timezone=args.timezone))

    print(heading(f"Created {args.name}"))
    print(f"  org        {org_id}")
    print(f"  timezone   {args.timezone}")
    print()
    print(muted("  Zamu can read this roster and draft asks. It cannot message anybody"))
    print(muted("  or change the roster until you grant those separately:"))
    print(muted(f"    zamu --org {org_id} grant send_ask"))
    print(muted(f"    zamu --org {org_id} grant write_roster"))
    return 0


def cmd_import(args) -> int:
    """Import people or duties from a spreadsheet exported as CSV."""
    from zamu.infra.importer import apply, read_duties, read_people

    store = SqliteStore(args.db)
    org = store.get_org(args.org)
    if org is None:
        raise NotFound(f"No organization {args.org}. Create one with: zamu new-org \"Name\"")

    content = Path(args.file).read_text(encoding="utf-8")

    if args.what == "people":
        report = read_people(content, org.id, timezone_name=org.timezone)
    else:
        report = read_duties(
            content,
            org.id,
            timezone_name=org.timezone,
            people=list(store.list_people(org.id)),
        )

    if not args.dry_run:
        apply(store, report)

    print(heading(report.summary()))
    if args.dry_run:
        print(muted("  Nothing was written. Drop --dry-run to import."))

    for person in report.people[:8]:
        quals = ", ".join(sorted(person.qualifications)) or "no qualifications recorded"
        print(f"  {person.name:24} {muted(quals)}")
    for duty in report.duties[:8]:
        print(f"  {duty.title:24} {muted(format_when(duty, org.timezone))}")
    if len(report.people) + len(report.duties) > 8:
        print(muted("  ..."))

    if report.problems:
        print()
        print(paint("  Could not read:", AMBER))
        for problem in report.problems:
            print(muted(f"    · {problem}"))

    if args.what == "people" and report.people:
        print()
        print(muted("  Nobody is opted in to being messaged by Zamu unless your file said"))
        print(muted("  so. That consent belongs to them, not to the spreadsheet."))

    return 0 if report.ok else 1


def cmd_agent(args) -> int:
    """Hand the roster to the agent and let it decide what to do."""
    from zamu.agent.build import build_agent
    from zamu.agent.planner import PlannedModel

    store = SqliteStore(args.db)
    notifier = OutboxNotifier(directory=Path(args.db).parent / "outbox")
    zamu = build_agent(
        store,
        args.org,
        clock=SystemClock(),
        notifier=notifier,
        base_url=args.base_url,
        model=PlannedModel() if args.planner else None,
    )
    print(muted(f"model: {zamu.model_name}"))
    print()
    result = zamu(args.message)
    print()
    print(heading("Zamu:"))
    print(str(result).strip())
    if zamu.refusals:
        print()
        print(paint("Refused by the policy gate:", AMBER))
        for refusal in zamu.refusals:
            print(f"  {refusal.tool}: [{refusal.rule}] {refusal.reason}")
    return 0


# -- helpers ---------------------------------------------------------------------------


def _report_outcome(outcome) -> None:
    colours = {
        "asked": GREEN,
        "withdrawn": GREEN,
        "already_covered": DIM,
        "waiting": DIM,
        "deferred": BLUE,
        "drafted": AMBER,
        "replayed": DIM,
        "no_candidates": RED,
        "blocked": RED,
        "failed": RED,
    }
    label = outcome.outcome.value.replace("_", " ")
    print(paint(label.upper(), colours.get(outcome.outcome.value, DIM)))
    if outcome.person_name:
        print(f"  {outcome.person_name}")
    if outcome.rationale:
        print(f"  {muted(outcome.rationale)}")
    print(f"  {outcome.detail}")
    if outcome.policy_rule:
        print(muted(f"  rule {outcome.policy_rule}"))
    if outcome.draft_text:
        print()
        print(muted("  --- draft ---"))
        for line in outcome.draft_text.splitlines():
            print(muted(f"  {line}"))
    if outcome.excluded and outcome.outcome.value in ("no_candidates", "blocked"):
        print(muted("  Not asked:"))
        for _name, reason in outcome.excluded:
            print(muted(f"    · {reason}"))
    print()


def _first_gap(service: CoverageService, org_id: str) -> str:
    found = service.find_gaps(org_id)
    if not found:
        raise ZamuError("No gaps to work on.")
    return found[0].duty_id


def _resolve_person(service: CoverageService, org_id: str, query: str):
    roster = service.roster(org_id)
    needle = query.strip().lower()
    matches = [p for p in roster.people if needle in p.name.lower() or needle == p.email.lower()]
    if not matches:
        raise NotFound(f"Nobody on this roster matches {query!r}.")
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        raise ZamuError(f"{query!r} is ambiguous: {names}. Be more specific.")
    return matches[0]


def _parse_level(value: str) -> ActionClass:
    lookup = {a.name.lower(): a for a in ActionClass}
    lookup.update({str(int(a)): a for a in ActionClass})
    key = value.strip().lower().replace("-", "_")
    if key not in lookup:
        raise ZamuError(f"Unknown action class {value!r}.")
    return lookup[key]


# -- argument parsing ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zamu",
        description="Keep a volunteer roster covered.",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite file to use")
    parser.add_argument("--org", default=DEMO_ORG_ID, help="organization id")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="base URL for one-tap links")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo", help="seed the demonstration organization")
    p.add_argument("--reset", action="store_true", help="delete the database first")
    p.add_argument("--no-send", action="store_true", help="seed without the send grant")
    p.add_argument("--no-write", action="store_true", help="seed without the roster-write grant")
    p.set_defaults(func=cmd_demo)

    sub.add_parser("status", help="show the roster and its coverage").set_defaults(func=cmd_status)

    p = sub.add_parser("gaps", help="list duties that need somebody")
    p.add_argument("--horizon", type=int, default=21)
    p.set_defaults(func=cmd_gaps)

    p = sub.add_parser("rank", help="show who Zamu would ask, and why")
    p.add_argument("duty", nargs="?", help="duty id (defaults to the soonest gap)")
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("fill", help="ask the next person about one duty")
    p.add_argument("duty", nargs="?", help="duty id (defaults to the soonest gap)")
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser("sweep", help="expire lapsed asks, then advance every gap")
    p.add_argument("--horizon", type=int, default=21)
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("withdraw", help="record that somebody dropped a duty")
    p.add_argument("person", help="name or email")
    p.add_argument("duty", help="duty id")
    p.add_argument("evidence", help="what they actually said, quoted")
    p.set_defaults(func=cmd_withdraw)

    sub.add_parser("outbox", help="show open asks and their one-tap links").set_defaults(
        func=cmd_outbox
    )

    p = sub.add_parser("accept", help="answer an ask as the volunteer")
    p.add_argument("token")
    p.set_defaults(func=lambda a: cmd_respond(a, accept=True))

    p = sub.add_parser("decline", help="decline an ask as the volunteer")
    p.add_argument("token")
    p.set_defaults(func=lambda a: cmd_respond(a, accept=False))

    p = sub.add_parser("brief", help="the handover brief")
    p.add_argument("--hours", type=int, default=24)
    p.set_defaults(func=cmd_brief)

    sub.add_parser("fairness", help="who has carried what").set_defaults(func=cmd_fairness)

    p = sub.add_parser("receipts", help="the action ledger")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_receipts)

    sub.add_parser("grants", help="show the trust ladder").set_defaults(func=cmd_grants)

    p = sub.add_parser("grant", help="grant one action class")
    p.add_argument("level", help="send_ask, write_roster, draft_ask")
    p.add_argument("--by", default="cli")
    p.add_argument("--note", default="")
    p.set_defaults(func=lambda a: cmd_grant(a, revoke=False))

    p = sub.add_parser("revoke", help="revoke one action class")
    p.add_argument("level")
    p.add_argument("--by", default="cli")
    p.add_argument("--note", default="")
    p.set_defaults(func=lambda a: cmd_grant(a, revoke=True))

    p = sub.add_parser("new-org", help="create an organization")
    p.add_argument("name")
    p.add_argument("--timezone", default="UTC")
    p.add_argument("--id", default=None)
    p.set_defaults(func=cmd_new_org)

    p = sub.add_parser("import", help="import people or duties from a CSV export")
    p.add_argument("what", choices=["people", "duties"])
    p.add_argument("file")
    p.add_argument("--dry-run", action="store_true", help="read the file without writing")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("agent", help="hand the roster to the agent")
    p.add_argument(
        "message",
        nargs="?",
        default="Check the roster and handle whatever needs doing.",
    )
    p.add_argument("--planner", action="store_true", help="force the deterministic planner")
    p.set_defaults(func=cmd_agent)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ZamuError as exc:
        print(paint(f"{type(exc).__name__}: {exc}", RED), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
