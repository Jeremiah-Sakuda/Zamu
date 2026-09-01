"""Getting an existing roster into Zamu.

A coordinator did not ask for software. They have a spreadsheet, they are busy, and
they will abandon anything that needs setup they cannot finish in one sitting. So this
module is built around one assumption: whatever they paste will be messy, and the
right response to messy is to import what is unambiguous and report the rest clearly
rather than to reject the file.

Every reader here is total. It never raises on a bad row; it returns what it
understood alongside a numbered list of what it could not, so the coordinator can fix
four lines rather than start again.

Column names are matched loosely, because real spreadsheets say "Name", "Full Name",
"Volunteer", and "name " with a trailing space, and none of those is worth an error
message.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zamu.core.ids import seeded_id
from zamu.core.models import (
    ActionClass,
    Duty,
    Person,
    QuietHours,
    TimeWindow,
)

#: Accepted spellings for each field, lowercased and stripped of punctuation.
PERSON_COLUMNS = {
    "name": ("name", "fullname", "volunteer", "person", "who"),
    "email": ("email", "emailaddress", "mail", "contact"),
    "qualifications": (
        "qualifications", "qualification", "skills", "skill", "trained", "training",
        "roles", "certifications",
    ),
    "active": ("active", "status", "current"),
    "opted_in": ("optedin", "optin", "consent", "contactable", "emailok"),
    "timezone": ("timezone", "tz"),
}

DUTY_COLUMNS = {
    "title": ("title", "shift", "duty", "name", "event"),
    "start": ("start", "starts", "starttime", "startsat", "when", "datetime", "date"),
    "end": ("end", "ends", "endtime", "endsat", "finish"),
    "hours": ("hours", "duration", "length"),
    "role": ("role", "position", "job"),
    "qualification": ("qualification", "requires", "required", "requiredqualification", "skill"),
    "assigned": ("assigned", "assignedto", "who", "volunteer", "person", "covered by"),
    "notice": ("notice", "minnotice", "minimumnotice"),
}

TRUTHY = {"y", "yes", "true", "1", "active", "on", "ok", "current", "opted in", "opted-in"}
FALSY = {"n", "no", "false", "0", "inactive", "off", "left", "former"}

#: Formats tried in order, from most explicit to most forgiving.
DATE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M",
    "%d-%m-%Y %H:%M",
    "%Y/%m/%d %H:%M",
    "%d %b %Y %H:%M",
    "%d %B %Y %H:%M",
    "%b %d %Y %H:%M",
)


@dataclass
class ImportReport:
    """What was understood, and what was not, from one file."""

    people: list[Person] = field(default_factory=list)
    duties: list[Duty] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.people or self.duties)

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported_people": len(self.people),
            "imported_duties": len(self.duties),
            "skipped_rows": self.skipped,
            "problems": self.problems,
        }

    def summary(self) -> str:
        parts = []
        if self.people:
            parts.append(f"{len(self.people)} {'person' if len(self.people) == 1 else 'people'}")
        if self.duties:
            parts.append(f"{len(self.duties)} {'duty' if len(self.duties) == 1 else 'duties'}")
        if not parts:
            return "Nothing could be read from that file."
        text = f"Imported {' and '.join(parts)}."
        if self.skipped:
            rows = "row" if self.skipped == 1 else "rows"
            text += f" {self.skipped} {rows} skipped."
        return text


# -- column matching -------------------------------------------------------------------


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _resolve(headers: list[str], spec: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Map each field we care about to whichever column the spreadsheet used for it."""
    found: dict[str, str] = {}
    normalised = {_key(h): h for h in headers}
    for field_name, aliases in spec.items():
        for alias in aliases:
            if alias in normalised:
                found[field_name] = normalised[alias]
                break
    return found


def _cell(row: dict[str, str], columns: dict[str, str], field_name: str) -> str:
    column = columns.get(field_name)
    return (row.get(column) or "").strip() if column else ""


def _boolish(value: str, default: bool) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return default
    if lowered in TRUTHY:
        return True
    if lowered in FALSY:
        return False
    return default


def _split_list(value: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in re.split(r"[,;|/]", value) if part.strip())


# -- times -----------------------------------------------------------------------------


def parse_moment(value: str, timezone_name: str) -> datetime | None:
    """Read a date and time the way a person wrote it, in the org's local timezone.

    Ambiguity between day-first and month-first is unavoidable in a bare `03/04/2026`,
    and guessing wrong puts a shift a month away. ISO is tried first for exactly that
    reason, and everything is interpreted in the organization's own timezone so a
    coordinator's "6pm" means six in the evening where they are.
    """
    text = (value or "").strip()
    if not text:
        return None

    try:
        tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        tz = ZoneInfo("UTC")

    # A value with no clock in it is a date, not midnight. `fromisoformat` happily
    # reads "2026-09-04" as 00:00, which would put a shift at twelve at night without
    # anybody being told — so date-only input takes the explicit morning default below
    # rather than falling through to a silently wrong answer.
    has_clock = ":" in text

    if has_clock:
        try:
            parsed = datetime.fromisoformat(text)
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)).astimezone(
                ZoneInfo("UTC")
            )
        except ValueError:
            pass

    if has_clock:
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))
            except ValueError:
                continue

    # A bare date is a shift starting in the morning, which is a better guess than
    # midnight and a better outcome than refusing the row.
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            day = datetime.strptime(text, fmt)
            return day.replace(hour=9, tzinfo=tz).astimezone(ZoneInfo("UTC"))
        except ValueError:
            continue

    return None


# -- readers ---------------------------------------------------------------------------


def read_people(content: str, org_id: str, *, timezone_name: str = "UTC") -> ImportReport:
    """Read volunteers from CSV.

    Opting in to direct contact defaults to **off**. A coordinator uploading a
    spreadsheet has not obtained anybody's consent to be messaged by software, and
    inferring that consent from a column that happens to hold an email address would
    make the whole authority model a formality.
    """
    report = ImportReport()
    rows = _rows(content, report)
    if rows is None:
        return report

    headers, records = rows
    columns = _resolve(headers, PERSON_COLUMNS)

    if "name" not in columns:
        report.problems.append(
            "No name column found. Zamu looked for: name, full name, volunteer, person."
        )
        return report
    if "email" not in columns:
        report.problems.append(
            "No email column found. Zamu looked for: email, email address, contact."
        )
        return report

    seen: set[str] = set()
    for number, row in enumerate(records, start=2):
        name = _cell(row, columns, "name")
        email = _cell(row, columns, "email").lower()

        if not name and not email:
            continue  # a blank line, not a problem
        if not name:
            report.problems.append(f"Row {number}: no name.")
            report.skipped += 1
            continue
        if "@" not in email:
            report.problems.append(f"Row {number}: {name} has no usable email address.")
            report.skipped += 1
            continue
        if email in seen:
            report.problems.append(f"Row {number}: {email} appears more than once.")
            report.skipped += 1
            continue
        seen.add(email)

        report.people.append(
            Person(
                id=seeded_id("per", org_id, email),
                org_id=org_id,
                name=name,
                email=email,
                qualifications=_split_list(_cell(row, columns, "qualifications")),
                quiet_hours=QuietHours(),
                timezone=_cell(row, columns, "timezone") or timezone_name,
                opt_ins=(
                    frozenset({ActionClass.SEND_ASK})
                    if _boolish(_cell(row, columns, "opted_in"), False)
                    else frozenset()
                ),
                active=_boolish(_cell(row, columns, "active"), True),
            )
        )

    return report


def read_duties(
    content: str,
    org_id: str,
    *,
    timezone_name: str = "UTC",
    people: list[Person] | None = None,
) -> ImportReport:
    """Read shifts from CSV, resolving assignees against people already imported."""
    report = ImportReport()
    rows = _rows(content, report)
    if rows is None:
        return report

    headers, records = rows
    columns = _resolve(headers, DUTY_COLUMNS)

    if "start" not in columns:
        report.problems.append(
            "No start column found. Zamu looked for: start, starts at, when, date."
        )
        return report

    by_name = {p.name.strip().lower(): p for p in (people or [])}
    by_email = {p.email.strip().lower(): p for p in (people or [])}

    for number, row in enumerate(records, start=2):
        raw_start = _cell(row, columns, "start")
        if not raw_start and not any(row.values()):
            continue

        start = parse_moment(raw_start, timezone_name)
        if start is None:
            report.problems.append(
                f"Row {number}: could not read a date and time from {raw_start!r}. "
                "Try 2026-09-04 18:00."
            )
            report.skipped += 1
            continue

        end = parse_moment(_cell(row, columns, "end"), timezone_name)
        if end is None:
            hours = _float(_cell(row, columns, "hours")) or 2.0
            end = start + timedelta(hours=hours)
        if end <= start:
            report.problems.append(f"Row {number}: the shift ends before it starts.")
            report.skipped += 1
            continue

        title = _cell(row, columns, "title") or "Shift"
        role = _cell(row, columns, "role") or title

        assignee = _cell(row, columns, "assigned").strip().lower()
        holder = by_name.get(assignee) or by_email.get(assignee)
        if assignee and holder is None:
            report.problems.append(
                f"Row {number}: nobody on the roster matches {assignee!r}, so this "
                "shift was imported as uncovered."
            )

        notice = _float(_cell(row, columns, "notice"))

        report.duties.append(
            Duty(
                id=seeded_id("dut", org_id, title, start.isoformat()),
                org_id=org_id,
                title=title,
                window=TimeWindow(start, end),
                role=role,
                required_qualification=_cell(row, columns, "qualification").lower() or None,
                min_notice=timedelta(hours=notice if notice else 12.0),
                assigned_person_id=holder.id if holder else None,
                # Imported assignments have never been confirmed *to Zamu*, so they are
                # honestly unknown rather than covered until somebody says otherwise.
                assigned_at=None,
                confirmed_at=None,
                source="import",
            )
        )

    return report


def _rows(content: str, report: ImportReport) -> tuple[list[str], list[dict[str, str]]] | None:
    text = (content or "").strip()
    if not text:
        report.problems.append("That file is empty.")
        return None

    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # a single column is not an error

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        report.problems.append("That file has no header row.")
        return None

    return list(reader.fieldnames), list(reader)


def _float(value: str) -> float | None:
    try:
        return float(re.sub(r"[^0-9.\-]", "", value)) if value else None
    except ValueError:
        return None


# -- writing -------------------------------------------------------------------------


def apply(store, report: ImportReport) -> ImportReport:
    """Persist an import. Deliberately separate, so a coordinator can look first."""
    for person in report.people:
        store.put_person(person)
    for duty in report.duties:
        store.put_duty(duty)
    return report
