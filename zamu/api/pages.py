"""The two pages a volunteer ever sees.

Server-rendered, self-contained, no JavaScript. These open from an email on an unknown
device belonging to somebody who installed nothing, and they have to work on a bad
connection in a car park. Every design decision here follows from that.

The palette, type scale and focus treatment come from the project's design system
(Accessible & Ethical: high-contrast navy, Atkinson Hyperlegible, 44px touch targets,
visible focus rings, reduced-motion respected). It is duplicated here rather than
shared with the console because these pages must not depend on a build step.
"""

from __future__ import annotations

from datetime import datetime

from zamu.core.messages import format_when, format_when_moment, relative_notice
from zamu.core.models import Ask, Duty, Org, Person

_CSS = """
:root {
  --bg:#F8FAFC; --fg:#020617; --card:#FFFFFF; --muted:#475569; --border:#E2E8F0;
  --accent:#0369A1; --on-accent:#FFFFFF; --ink:#0F172A; --danger:#DC2626;
  --radius:12px;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#020617; --fg:#F8FAFC; --card:#0F172A; --muted:#94A3B8; --border:#1E293B;
          --accent:#38BDF8; --on-accent:#020617; --ink:#E2E8F0; }
}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--fg);
  font-family:'Atkinson Hyperlegible',system-ui,-apple-system,'Segoe UI',sans-serif;
  font-size:17px;line-height:1.55;-webkit-text-size-adjust:100%}
main{max-width:34rem;margin:0 auto}
.brand{font-size:14px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  margin-bottom:20px}
h1{font-size:26px;line-height:1.25;margin:0 0 8px}
p{margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
  padding:20px;margin:20px 0}
.shift{font-size:20px;font-weight:700;margin-bottom:4px}
.meta{color:var(--muted);font-size:16px}
.actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:24px}
button,.btn{font:inherit;font-weight:700;border-radius:8px;padding:14px 26px;
  min-height:44px;min-width:44px;cursor:pointer;border:2px solid transparent;
  transition:background-color 180ms ease,color 180ms ease,box-shadow 180ms ease;
  text-decoration:none;display:inline-flex;align-items:center;justify-content:center}
.primary{background:var(--accent);color:var(--on-accent);border-color:var(--accent)}
.primary:hover{box-shadow:0 4px 6px rgba(0,0,0,.12)}
.secondary{background:transparent;color:var(--ink);border-color:var(--ink)}
.secondary:hover{background:var(--ink);color:var(--bg)}
a:focus-visible,button:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
.note{color:var(--muted);font-size:15px;border-top:1px solid var(--border);
  padding-top:16px;margin-top:28px}
.good{color:var(--accent)} .bad{color:var(--danger)}
@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
"""


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{_esc(title)} · Zamu</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap"
 rel="stylesheet">
<style>{_CSS}</style>
</head><body><main>
<div class="brand">Zamu</div>
{body}
</main></body></html>"""


def _esc(value: object) -> str:
    import html

    return html.escape(str(value), quote=True)


def render_confirm(
    ask: Ask, duty: Duty | None, person: Person | None, org: Org, *, accept: bool, now: datetime
) -> str:
    """Ask the human to confirm, because a mail client may have opened this link.

    The shift details are repeated here rather than assumed known: this page is often
    reached days after the email was read, from a link somebody tapped without
    remembering exactly what it was about.
    """
    name = person.name.split()[0] if person else "there"
    title = duty.title if duty else "this shift"
    when = format_when(duty, org.timezone) if duty else ""
    soon = relative_notice(duty, now) if duty else ""
    closes = format_when_moment(ask.expires_at, org.timezone)

    if accept:
        heading = f"Confirm you can cover this, {_esc(name)}?"
        lead = (
            f"{_esc(org.name)} needs somebody for this shift. Confirming puts your name "
            "on the roster and stops anyone else being asked."
        )
        label, cls, other = "Yes, put me down", "primary", ("no", "Actually, I can't")
    else:
        heading = f"Let {_esc(org.name)} know you can't make this?"
        lead = (
            "Nobody minds. Zamu will ask the next person straight away and you will not "
            "be asked about this shift again."
        )
        label, cls, other = "Yes, I can't make it", "secondary", ("yes", "Actually, I can")

    return _shell(
        title,
        f"""
<h1>{heading}</h1>
<p>{lead}</p>
<div class="card">
  <div class="shift">{_esc(title)}</div>
  <div class="meta">{_esc(when)}{f' ({_esc(soon)})' if soon else ''}</div>
  <div class="meta">Role: {_esc(duty.role) if duty else ''}</div>
</div>
<div class="actions">
  <form method="post" action="/r/{_esc(ask.token)}/{'yes' if accept else 'no'}">
    <button class="{cls}" type="submit">{label}</button>
  </form>
  <a class="btn secondary" href="/r/{_esc(ask.token)}/{other[0]}">{other[1]}</a>
</div>
<p class="note">Nothing has changed yet. This page only records your answer when you
choose above. If you do nothing, Zamu asks somebody else after {_esc(closes)}.</p>
""",
    )


def render_answered(ask: Ask, duty: Duty | None, person: Person | None, org: Org, response) -> str:
    """Confirm what was recorded, in the volunteer's terms rather than the system's."""
    from zamu.core.fill import ResponseOutcome

    title = duty.title if duty else "this shift"
    when = format_when(duty, org.timezone) if duty else ""
    name = person.name.split()[0] if person else "there"

    headings = {
        ResponseOutcome.ACCEPTED_AND_ASSIGNED: f"You're on, {name}.",
        ResponseOutcome.ACCEPTED_PENDING_COORDINATOR: f"Thanks, {name}. Passing this on.",
        ResponseOutcome.DECLINED: "Thanks for letting us know.",
        ResponseOutcome.ALREADY_ANSWERED: "You have already answered this one.",
        ResponseOutcome.EXPIRED: "This one has moved on.",
        ResponseOutcome.SUPERSEDED: "Somebody else has it covered.",
        ResponseOutcome.UNKNOWN: "We don't recognise that link.",
        ResponseOutcome.WRITE_FAILED: "Something went wrong at our end.",
    }
    good = response.outcome in (
        ResponseOutcome.ACCEPTED_AND_ASSIGNED,
        ResponseOutcome.ACCEPTED_PENDING_COORDINATOR,
        ResponseOutcome.DECLINED,
    )

    extra = ""
    if response.outcome is ResponseOutcome.ACCEPTED_AND_ASSIGNED:
        extra = (
            "<p class='note'>You will not get a reminder about this from Zamu unless "
            "something changes. If your plans change, reply to the original email.</p>"
        )
    elif response.outcome is ResponseOutcome.DECLINED:
        extra = (
            "<p class='note'>Zamu is asking the next person now. You will not be asked "
            "about this shift again.</p>"
        )

    return _shell(
        title,
        f"""
<h1 class="{'good' if good else ''}">{_esc(headings.get(response.outcome, 'Thanks.'))}</h1>
<div class="card">
  <div class="shift">{_esc(title)}</div>
  <div class="meta">{_esc(when)}</div>
  <div class="meta">{_esc(org.name)}</div>
</div>
<p>{_esc(response.detail)}</p>
{extra}
""",
    )


def render_notice(heading: str, detail: str) -> str:
    return _shell(
        heading,
        f"<h1>{_esc(heading)}</h1>{f'<p>{_esc(detail)}</p>' if detail else ''}",
    )
