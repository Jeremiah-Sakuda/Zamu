"""Zamu as an AgentCore runtime.

AgentCore gives Zamu three things it would otherwise have to build: a managed
container runtime with session isolation, memory that survives across the days a
single fill can take, and traces a judge or an operator can follow end to end.

The contract is small — `POST /invocations`, `GET /ping`, port 8080 — and the
entrypoint below is deliberately thin. It resolves a payload to an operation and calls
the same `CoverageService` the CLI and the HTTP API call. Nothing about how Zamu makes
decisions lives here, which is the point: the runtime is a deployment choice, not part
of the product.

Payload shape:

    {"action": "agent",  "org_id": "...", "prompt": "..."}   # let the model drive
    {"action": "sweep",  "org_id": "..."}                    # deterministic pass
    {"action": "brief",  "org_id": "..."}                    # what needs a human
    {"action": "status", "org_id": "..."}                    # coverage summary

`action` defaults to "agent", and a bare `{"prompt": "..."}` works, because that is
what the AgentCore console sends when somebody types into the test panel.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from zamu.config import build_notifier, build_store, ensure_seeded, load_settings
from zamu.core.brief import build_brief
from zamu.core.clock import SystemClock
from zamu.core.coverage import coverage_summary
from zamu.core.errors import ZamuError
from zamu.core.fill import CoverageService
from zamu.core.messages import format_when

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
    '"message":"%(message)s"}',
)
log = logging.getLogger("zamu.agentcore")

SETTINGS = load_settings()
CLOCK = SystemClock()

_store = None
_notifier = None


def store():
    global _store
    if _store is None:
        _store = build_store(SETTINGS)
        seeded = ensure_seeded(_store, SETTINGS, CLOCK)
        if seeded:
            log.info("seeded demonstration organization %s", seeded)
    return _store


def notifier():
    global _notifier
    if _notifier is None:
        _notifier = build_notifier(SETTINGS)
    return _notifier


def service() -> CoverageService:
    return CoverageService(store(), CLOCK, notifier(), base_url=SETTINGS.base_url)


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Route one invocation. Pure enough to test without the runtime attached."""
    payload = payload or {}
    action = str(payload.get("action") or "agent").lower()
    org_id = str(payload.get("org_id") or SETTINGS.org_id)

    try:
        if action == "sweep":
            return _sweep(org_id, payload)
        if action == "brief":
            return _brief(org_id, payload)
        if action == "status":
            return _status(org_id)
        if action == "agent":
            return _agent(org_id, payload)
        return {"ok": False, "error": f"unknown action {action!r}"}
    except ZamuError as exc:
        # Domain errors are answers, not crashes. A refusal that arrives as a 500 tells
        # the caller nothing about what to do next.
        log.warning("refused: %s", exc)
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


def _agent(org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from zamu.agent.build import build_agent
    from zamu.agent.planner import PlannedModel

    prompt = (
        payload.get("prompt")
        or payload.get("message")
        or payload.get("input")
        or "Check the roster and handle whatever needs doing."
    )
    zamu = build_agent(
        store(),
        org_id,
        clock=CLOCK,
        notifier=notifier(),
        base_url=SETTINGS.base_url,
        model=PlannedModel() if SETTINGS.force_planner else None,
    )
    result = zamu(str(prompt))

    tools = [
        block["toolUse"]["name"]
        for message in zamu.agent.messages
        for block in (message.get("content") or [])
        if "toolUse" in block
    ]
    log.info("agent run org=%s model=%s tools=%s", org_id, zamu.model_name, tools)

    return {
        "ok": True,
        "action": "agent",
        "org_id": org_id,
        "model": zamu.model_name,
        "reply": str(result).strip(),
        "tools_called": tools,
        "refusals": [
            {"tool": r.tool, "rule": r.rule, "reason": r.reason} for r in zamu.refusals
        ],
    }


def _sweep(org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    horizon = int(payload.get("horizon_days") or SETTINGS.sweep_horizon_days)
    result = service().sweep(org_id, horizon_days=horizon)
    log.info(
        "sweep org=%s expired=%d asked=%d needs_human=%d",
        org_id,
        len(result.expired),
        len(result.asked),
        len(result.needing_coordinator),
    )
    return {"ok": True, "action": "sweep", **result.as_dict()}


def _brief(org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    hours = int(payload.get("hours") or 24)
    now = CLOCK.now()
    brief = build_brief(store(), org_id, now, since=now - timedelta(hours=hours))
    body = brief.as_dict()
    body["text"] = brief.to_text()
    return {"ok": True, "action": "brief", **body}


def _status(org_id: str) -> dict[str, Any]:
    roster = store().load_roster(org_id)
    now = CLOCK.now()
    return {
        "ok": True,
        "action": "status",
        "org_id": org_id,
        "org_name": roster.org.name,
        "coverage": coverage_summary(roster, now),
        "gaps": [
            {
                "duty_id": g.duty_id,
                "title": roster.duty(g.duty_id).title,
                "when": format_when(roster.duty(g.duty_id), roster.org.timezone),
                "why": g.reason,
            }
            for g in service().find_gaps(org_id)
        ],
    }


def build_app():
    """Construct the AgentCore application.

    Imported lazily so the rest of Zamu — the tests, the CLI, the local API — never
    needs the AgentCore SDK installed.
    """
    from bedrock_agentcore import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return handle(payload)

    @app.ping
    def ping():
        from bedrock_agentcore import PingStatus

        # Healthy means "can read the roster". A runtime that reports healthy while
        # its store is unreachable is worse than one that reports unhealthy.
        try:
            store().list_orgs()
            return PingStatus.HEALTHY
        except Exception:  # noqa: BLE001 - any failure here means not ready
            log.exception("ping failed: the store is unreachable")
            return PingStatus.HEALTHY_BUSY

    return app


def main() -> None:
    build_app().run(port=8080, host="0.0.0.0")  # noqa: S104 - required inside a container


if __name__ == "__main__":
    main()
