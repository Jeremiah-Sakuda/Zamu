"""Composing what a volunteer actually reads.

Every message Zamu sends must be worth a human's attention, or it should not be
sent. That is a design constraint with teeth: it rules out digests, nudges, status
updates, and anything phrased as a broadcast. A volunteer gets exactly one kind of
message from Zamu — a specific question about a specific shift, answerable in one tap.

Text composition lives here, in core, so it is a pure function of the roster and can
be tested without a mail provider anywhere in sight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zamu.core.models import Duty, Org, Person


@dataclass(frozen=True, slots=True)
class Composed:
    """A rendered message, in both the plain and rich forms."""

    subject: str
    text: str
    html: str


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def format_when(duty: Duty, timezone_name: str) -> str:
    """A duty's time as a person would say it out loud: 'Thursday 4 Sep, 6:00-8:00pm'."""
    tz = _zone(timezone_name)
    start = duty.start.astimezone(tz)
    end = duty.end.astimezone(tz)
    day = start.strftime("%A %-d %b")
    same_meridiem = start.strftime("%p") == end.strftime("%p")
    start_str = start.strftime("%-I:%M") if same_meridiem else start.strftime("%-I:%M%p").lower()
    end_str = end.strftime("%-I:%M%p").lower()
    return f"{day}, {start_str}-{end_str}"


def relative_notice(duty: Duty, now: datetime) -> str:
    """How far off the duty is, in words. 'tomorrow', 'in 3 days', 'in 4 hours'."""
    delta = duty.start - now
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "very soon"
    if hours < 24:
        whole = int(round(hours))
        return f"in {whole} hour{'s' if whole != 1 else ''}"
    days = int(delta.days)
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def compose_ask(
    person: Person,
    duty: Duty,
    org: Org,
    now: datetime,
    accept_url: str,
    decline_url: str,
    expires_at: datetime,
) -> Composed:
    """The one message a volunteer ever receives from Zamu.

    Written to be answerable without opening anything: the shift, the deadline, and
    two links. No guilt, no urgency theatre, and an explicit 'no' that costs nothing —
    a decline is a useful answer, and a message that makes declining feel expensive
    will get silence instead, which helps nobody.
    """
    when = format_when(duty, org.timezone)
    soon = relative_notice(duty, now)
    reply_by = format_when_moment(expires_at, org.timezone)

    subject = f"Can you cover {duty.title}? {when}"

    text = f"""Hi {person.name.split()[0]},

{org.name} has an uncovered shift and you are the fairest person to ask.

  {duty.title}
  {when} ({soon})
  Role: {duty.role}

Yes, I can cover it:  {accept_url}
No, not this time:    {decline_url}

Either answer helps. If you say no, Zamu asks the next person straight away and
you will not be asked about this shift again.

If you have not answered by {reply_by}, Zamu moves on automatically.

— Zamu, on behalf of {org.name}
You are receiving this because you opted in to shift requests. Reply STOP to opt out.
"""

    html = f"""<div style="font-family:'Atkinson Hyperlegible',system-ui,-apple-system,sans-serif;
 font-size:16px;line-height:1.5;color:#020617;max-width:520px">
  <p>Hi {person.name.split()[0]},</p>
  <p>{org.name} has an uncovered shift and you are the fairest person to ask.</p>
  <div style="border:1px solid #E2E8F0;border-radius:12px;padding:16px;
   background:#F8FAFC;margin:16px 0">
    <div style="font-weight:700;font-size:18px">{duty.title}</div>
    <div style="color:#475569;margin-top:4px">{when} ({soon})</div>
    <div style="color:#475569">Role: {duty.role}</div>
  </div>
  <p>
    <a href="{accept_url}" style="display:inline-block;background:#0369A1;color:#FFFFFF;
     text-decoration:none;padding:14px 28px;border-radius:8px;font-weight:700;
     min-height:44px;box-sizing:border-box">Yes, I can cover it</a>
    <a href="{decline_url}" style="display:inline-block;color:#0F172A;text-decoration:none;
     padding:14px 28px;border:2px solid #0F172A;border-radius:8px;font-weight:700;
     margin-left:8px;min-height:44px;box-sizing:border-box">No, not this time</a>
  </p>
  <p style="color:#475569">Either answer helps. If you say no, Zamu asks the next person
  straight away and you will not be asked about this shift again.</p>
  <p style="color:#475569">If you have not answered by {reply_by}, Zamu moves on
  automatically.</p>
  <p style="color:#475569;font-size:14px;border-top:1px solid #E2E8F0;padding-top:12px">
  Zamu, on behalf of {org.name}. You are receiving this because you opted in to shift
  requests. Reply STOP to opt out.</p>
</div>"""

    return Composed(subject=subject, text=text, html=html)


def format_when_moment(moment: datetime, timezone_name: str) -> str:
    """A single instant in local words: 'Thursday 4 Sep, 3:00pm'."""
    local = moment.astimezone(_zone(timezone_name))
    return local.strftime("%A %-d %b, %-I:%M%p").replace("AM", "am").replace("PM", "pm")


def compose_draft_note(person: Person, duty: Duty, org: Org, reason: str) -> Composed:
    """What the coordinator sees when Zamu was not allowed to send the ask itself.

    Deliberately ready to paste: the point of stopping at DRAFT_ASK is that the work
    is still done, just by a human hand.
    """
    when = format_when(duty, org.timezone)
    subject = f"Draft ready: ask {person.name} about {duty.title}"
    text = f"""Zamu did not send this. {reason}

Send to: {person.name} <{person.email}>
Subject: Can you cover {duty.title}? {when}

Hi {person.name.split()[0]},

{org.name} has an uncovered shift and you are the fairest person to ask.

  {duty.title}
  {when}
  Role: {duty.role}

Can you cover it? Just reply yes or no — either answer helps.

— {org.name}
"""
    html = f"""<div style="font-family:'Atkinson Hyperlegible',system-ui,sans-serif;font-size:16px">
  <p><strong>Zamu did not send this.</strong> {reason}</p>
  <pre style="white-space:pre-wrap;background:#F8FAFC;border:1px solid #E2E8F0;
   border-radius:12px;padding:16px">{text}</pre>
</div>"""
    return Composed(subject=subject, text=text, html=html)
