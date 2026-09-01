"""The HTTP surface: a JSON API for the coordinator console, and two pages for volunteers.

The volunteer pages are plain server-rendered HTML on purpose. They are opened from an
email, on an unknown device, possibly on a bus, by somebody who has installed nothing
and has no account. That is not a place to ship a JavaScript bundle.

One decision worth naming. The accept and decline links in an email are GETs, and mail
clients and link scanners fetch GETs without a human involved. So a GET here renders a
confirmation page and only a POST commits. For a product whose entire thesis is that
promises must not be created accidentally, having a spam filter accept a shift on
somebody's behalf would be the single most embarrassing possible bug.
"""

from __future__ import annotations

import html
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from zamu.api import views
from zamu.api.pages import render_answered, render_confirm, render_notice
from zamu.core.brief import build_brief
from zamu.core.clock import SystemClock
from zamu.core.errors import NotFound, ZamuError
from zamu.core.fill import CoverageService
from zamu.core.ids import new_id
from zamu.core.models import ActionClass, Grant
from zamu.demo import DEMO_ORG_ID, seed
from zamu.infra.notify import OutboxNotifier
from zamu.infra.sqlite_store import SqliteStore

DB_PATH = os.environ.get("ZAMU_DB", ".zamu/zamu.sqlite")
BASE_URL = os.environ.get("ZAMU_BASE_URL", "http://localhost:8000")
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ZAMU_CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app = FastAPI(
    title="Zamu",
    description="An agent that keeps a volunteer roster covered.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

_store: SqliteStore | None = None
_notifier: OutboxNotifier | None = None


def get_store() -> SqliteStore:
    global _store
    if _store is None:
        _store = SqliteStore(DB_PATH)
        if not _store.list_orgs():
            seed(_store, SystemClock().now())
    return _store


def get_notifier() -> OutboxNotifier:
    global _notifier
    if _notifier is None:
        _notifier = OutboxNotifier(directory=Path(DB_PATH).parent / "outbox")
    return _notifier


def get_service() -> CoverageService:
    return CoverageService(get_store(), SystemClock(), get_notifier(), base_url=BASE_URL)


def _roster(org_id: str):
    try:
        return get_store().load_roster(org_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.exception_handler(ZamuError)
async def _zamu_error(_request, exc: ZamuError) -> JSONResponse:
    status = 404 if isinstance(exc, NotFound) else 400
    return JSONResponse(status_code=status, content={"detail": str(exc)})


# -- health and discovery --------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    store = get_store()
    return {
        "status": "ok",
        "orgs": [o.id for o in store.list_orgs()],
        "demo_org": DEMO_ORG_ID,
    }


@app.get("/api/orgs")
def list_orgs() -> list[dict[str, Any]]:
    now = SystemClock().now()
    return [views.org_view(get_store().load_roster(o.id), now) for o in get_store().list_orgs()]


# -- the console -----------------------------------------------------------------------


@app.get("/api/orgs/{org_id}")
def read_org(org_id: str) -> dict[str, Any]:
    """Everything the console's main screen needs, in one round trip."""
    service = get_service()
    roster = _roster(org_id)
    now = service.clock.now()
    return {
        "org": views.org_view(roster, now),
        "summary": views.timeline_view(roster, now),
        "duties": views.duties_view(roster, now),
        "people": views.people_view(roster, now),
        "grants": views.grants_view(roster, now),
        "brief": build_brief(service.store, org_id, now).as_dict(),
    }


@app.get("/api/orgs/{org_id}/duties")
def read_duties(org_id: str) -> list[dict[str, Any]]:
    return views.duties_view(_roster(org_id), SystemClock().now())


@app.get("/api/orgs/{org_id}/gaps")
def read_gaps(org_id: str, horizon_days: int = Query(21, ge=1, le=365)) -> list[dict[str, Any]]:
    service = get_service()
    roster = _roster(org_id)
    ids = {g.duty_id for g in service.find_gaps(org_id, horizon_days=horizon_days)}
    return [d for d in views.duties_view(roster, service.clock.now()) if d["id"] in ids]


@app.get("/api/orgs/{org_id}/duties/{duty_id}/candidates")
def read_candidates(org_id: str, duty_id: str) -> dict[str, Any]:
    service = get_service()
    roster = _roster(org_id)
    if roster.duty(duty_id) is None:
        raise HTTPException(status_code=404, detail=f"no duty {duty_id}")
    return views.candidates_view(service.rank_for(org_id, duty_id), roster)


@app.get("/api/orgs/{org_id}/people")
def read_people(org_id: str) -> list[dict[str, Any]]:
    return views.people_view(_roster(org_id), SystemClock().now())


@app.get("/api/orgs/{org_id}/receipts")
def read_receipts(org_id: str, limit: int = Query(40, ge=1, le=200)) -> list[dict[str, Any]]:
    _roster(org_id)
    return [views.receipt_view(r) for r in get_service().ledger.recent(org_id, limit=limit)]


@app.get("/api/orgs/{org_id}/brief")
def read_brief(org_id: str, hours: int = Query(24, ge=1, le=720)) -> dict[str, Any]:
    service = get_service()
    _roster(org_id)
    now = service.clock.now()
    brief = build_brief(service.store, org_id, now, since=now - timedelta(hours=hours))
    payload = brief.as_dict()
    payload["text"] = brief.to_text()
    return payload


@app.get("/api/orgs/{org_id}/outbox")
def read_outbox(org_id: str) -> list[dict[str, Any]]:
    """The volunteer's inbox, so one person can drive the whole loop in the sandbox."""
    return views.outbox_view(get_notifier(), _roster(org_id), BASE_URL)


# -- acting ----------------------------------------------------------------------------


@app.post("/api/orgs/{org_id}/duties/{duty_id}/ask")
def ask_next(org_id: str, duty_id: str) -> dict[str, Any]:
    _roster(org_id)
    return get_service().ask_next(org_id, duty_id).as_dict()


@app.post("/api/orgs/{org_id}/duties/{duty_id}/withdraw")
def withdraw(org_id: str, duty_id: str, payload: dict = Body(...)) -> dict[str, Any]:
    _roster(org_id)
    person_id = payload.get("person_id")
    evidence = payload.get("evidence", "")
    if not person_id:
        raise HTTPException(status_code=400, detail="person_id is required")
    return get_service().record_withdrawal(org_id, duty_id, person_id, evidence).as_dict()


@app.post("/api/orgs/{org_id}/sweep")
def sweep(org_id: str, horizon_days: int = Query(21, ge=1, le=365)) -> dict[str, Any]:
    _roster(org_id)
    return get_service().sweep(org_id, horizon_days=horizon_days).as_dict()


@app.post("/api/orgs/{org_id}/agent")
def run_agent(org_id: str, payload: dict = Body(default={})) -> dict[str, Any]:
    """Hand the roster to the agent and report what it did, refusals included."""
    from zamu.agent.build import build_agent

    _roster(org_id)
    message = payload.get("message") or "Check the roster and handle whatever needs doing."
    zamu = build_agent(
        get_store(),
        org_id,
        clock=SystemClock(),
        notifier=get_notifier(),
        base_url=BASE_URL,
    )
    result = zamu(message)
    return {
        "model": zamu.model_name,
        "message": message,
        "reply": str(result).strip(),
        "tools_called": [
            block["toolUse"]["name"]
            for m in zamu.agent.messages
            for block in (m.get("content") or [])
            if "toolUse" in block
        ],
        "refusals": [
            {"tool": r.tool, "rule": r.rule, "reason": r.reason} for r in zamu.refusals
        ],
    }


# -- grants ----------------------------------------------------------------------------


@app.get("/api/orgs/{org_id}/grants")
def read_grants(org_id: str) -> list[dict[str, Any]]:
    return views.grants_view(_roster(org_id), SystemClock().now())


@app.post("/api/orgs/{org_id}/grants/{level}")
def set_grant(org_id: str, level: str, payload: dict = Body(default={})) -> dict[str, Any]:
    """Grant or revoke one rung. The only way any authority ever changes."""
    _roster(org_id)
    action_class = _parse_level(level)
    granted = bool(payload.get("granted", True))
    now = SystemClock().now()
    store = get_store()

    if action_class in (ActionClass.READ,) or action_class.name == "REASSIGN_WITHOUT_CONSENT":
        raise HTTPException(
            status_code=400, detail=f"{action_class.label} is not a grant that can be changed"
        )

    existing = [
        g for g in store.list_grants(org_id) if g.action_class is action_class and g.is_active(now)
    ]
    if granted and not existing:
        store.put_grant(
            Grant(
                id=new_id("gr"),
                org_id=org_id,
                action_class=action_class,
                granted_by=payload.get("granted_by", "coordinator"),
                granted_at=now,
                note=payload.get("note", ""),
            )
        )
    elif not granted:
        for grant in existing:
            store.put_grant(replace(grant, revoked_at=now))

    return {"grants": views.grants_view(store.load_roster(org_id), now)}


def _parse_level(value: str) -> ActionClass:
    lookup = {a.name.lower(): a for a in ActionClass}
    lookup.update({str(int(a)): a for a in ActionClass})
    key = value.strip().lower().replace("-", "_")
    if key not in lookup:
        raise HTTPException(status_code=400, detail=f"unknown action class {value!r}")
    return lookup[key]


# -- the volunteer's two pages ---------------------------------------------------------


@app.get("/r/{token}/{answer}", response_class=HTMLResponse)
def confirm_page(token: str, answer: str) -> HTMLResponse:
    """Show what is being agreed to. Commits nothing.

    Mail clients and security scanners follow links in email without a human. A GET
    that accepted a shift would let a spam filter volunteer somebody for Saturday.
    """
    accept = _parse_answer(answer)
    service = get_service()
    ask = get_store().get_ask_by_token(token)
    if ask is None:
        return HTMLResponse(
            render_notice("That link is not one we recognise.", ""), status_code=404
        )

    roster = get_store().load_roster(ask.org_id)
    duty = roster.duty(ask.duty_id)
    person = roster.person(ask.person_id)
    now = service.clock.now()

    if not ask.state.is_open or ask.is_expired(now):
        return HTMLResponse(
            render_answered(
                ask, duty, person, roster.org, service.record_response(token, accept=accept)
            )
        )

    return HTMLResponse(render_confirm(ask, duty, person, roster.org, accept=accept, now=now))


@app.post("/r/{token}/{answer}", response_class=HTMLResponse)
def commit_answer(token: str, answer: str) -> HTMLResponse:
    """Record the answer. This is the only thing that changes anything."""
    accept = _parse_answer(answer)
    service = get_service()
    ask = get_store().get_ask_by_token(token)
    if ask is None:
        return HTMLResponse(
            render_notice("That link is not one we recognise.", ""), status_code=404
        )

    response = service.record_response(token, accept=accept)
    roster = get_store().load_roster(ask.org_id)
    return HTMLResponse(
        render_answered(
            ask, roster.duty(ask.duty_id), roster.person(ask.person_id), roster.org, response
        )
    )


def _parse_answer(answer: str) -> bool:
    key = html.escape(answer.strip().lower())
    if key in ("yes", "accept", "y"):
        return True
    if key in ("no", "decline", "n"):
        return False
    raise HTTPException(status_code=400, detail="answer must be yes or no")


# -- sandbox convenience ---------------------------------------------------------------


@app.post("/api/demo/reset")
def reset_demo() -> dict[str, Any]:
    """Put the sandbox back to its opening state, so the next visitor sees the story.

    Only ever touches the demo organisation.
    """
    store = get_store()
    store.delete_org(DEMO_ORG_ID)
    get_notifier().clear()
    org_id = seed(store, SystemClock().now())
    return {"reset": True, "org_id": org_id}
