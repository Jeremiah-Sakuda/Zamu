"""The scheduled jobs, and the AgentCore entrypoint's routing.

Both are thin, and both are the layer where an operational mistake is expensive and
invisible: a sweep that silently covers no organizations, or a daily brief that emails
a coordinator to tell them nothing happened.
"""

from __future__ import annotations

import pytest

from zamu.agentcore import app as agentcore
from zamu.config import Settings, load_settings
from zamu.core.store import InMemoryStore
from zamu.demo import DEMO_ORG_ID, demo_gap_id
from zamu.infra import scheduled
from zamu.infra.notify import OutboxNotifier


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """One in-memory store and one outbox, shared by every entrypoint under test."""
    store = InMemoryStore()
    notifier = OutboxNotifier()
    settings = Settings(store_kind="memory", base_url="https://zamu.test", org_id=DEMO_ORG_ID)

    monkeypatch.setattr(scheduled, "build_store", lambda _s: store)
    monkeypatch.setattr(scheduled, "build_notifier", lambda _s: notifier)
    monkeypatch.setattr(scheduled, "load_settings", lambda: settings)
    monkeypatch.setattr(agentcore, "SETTINGS", settings)
    monkeypatch.setattr(agentcore, "_store", None)
    monkeypatch.setattr(agentcore, "_notifier", None)
    monkeypatch.setattr(agentcore, "build_store", lambda _s: store)
    monkeypatch.setattr(agentcore, "build_notifier", lambda _s: notifier)
    return store, notifier, settings


# -- configuration ---------------------------------------------------------------------


def test_the_defaults_need_no_aws(monkeypatch):
    for key in ("ZAMU_STORE", "ZAMU_SES_SENDER", "ZAMU_DYNAMO_TABLE", "ZAMU_FORCE_PLANNER"):
        monkeypatch.delenv(key, raising=False)
    settings = load_settings()
    assert settings.store_kind == "sqlite"
    assert settings.notifier_kind == "outbox"
    assert not settings.uses_aws


def test_ses_is_chosen_by_the_presence_of_a_sender(monkeypatch):
    monkeypatch.setenv("ZAMU_SES_SENDER", "zamu@example.org")
    settings = load_settings()
    assert settings.notifier_kind == "ses"
    assert settings.uses_aws


def test_an_unknown_store_falls_back_rather_than_failing(monkeypatch):
    monkeypatch.setenv("ZAMU_STORE", "postgres-please")
    assert load_settings().store_kind == "sqlite"


def test_the_health_summary_carries_no_secrets(monkeypatch):
    monkeypatch.setenv("ZAMU_SES_SENDER", "secret@example.org")
    described = load_settings().describe()
    assert "secret@example.org" not in str(described)


# -- the sweep -------------------------------------------------------------------------


def test_the_sweep_seeds_and_then_fills(wired):
    store, notifier, _ = wired
    result = scheduled.run_sweep({})

    assert result["ok"]
    assert store.list_orgs()  # seeded on first run
    outcomes = result["results"][0]["outcomes"]
    assert any(o["outcome"] == "asked" for o in outcomes)
    assert notifier.sent


def test_the_sweep_covers_every_org_when_none_is_named(wired):
    store, _, _ = wired
    scheduled.run_sweep({})
    result = scheduled.run_sweep({})
    assert len(result["results"]) == len(store.list_orgs())


def test_a_named_org_narrows_the_sweep(wired):
    scheduled.run_sweep({"org_id": DEMO_ORG_ID})
    result = scheduled.run_sweep({"org_id": DEMO_ORG_ID})
    assert [r["org_id"] for r in result["results"]] == [DEMO_ORG_ID]


def test_a_second_sweep_does_not_ask_the_same_person_again(wired):
    _store, notifier, _ = wired
    scheduled.run_sweep({})
    first = len(notifier.sent)
    scheduled.run_sweep({})
    assert len(notifier.sent) == first


# -- the daily brief -------------------------------------------------------------------


def test_an_open_ask_alone_does_not_justify_an_email(wired):
    """After a sweep the only news is 'waiting on an answer'. Nothing has happened,
    the coordinator can do nothing about it, and Zamu will tell them when it resolves.
    Sending here turns the daily handover back into the notification stream it
    replaced."""
    _store, notifier, _ = wired
    scheduled.run_sweep({})
    notifier.clear()

    result = scheduled.run_brief({"to": "nadia@example.org"})
    assert result["sent"] == []
    assert notifier.sent == []


def test_a_brief_with_content_is_sent_and_says_so_in_the_subject(wired):
    _store, notifier, _ = wired
    scheduled.run_sweep({})
    _accept_the_open_ask(_store)
    notifier.clear()

    result = scheduled.run_brief({"to": "nadia@example.org"})
    assert result["sent"] and result["sent"][0]["delivered"]
    message = notifier.sent[0][0]
    assert message.kind == "brief"
    assert "Riverside" in message.subject
    assert "shift" in message.subject or "need you" in message.subject


def test_a_brief_with_no_recipient_is_skipped_rather_than_guessed(wired):
    _store, notifier, _ = wired
    scheduled.run_sweep({})
    _accept_the_open_ask(_store)
    notifier.clear()
    assert scheduled.run_brief({})["sent"] == []
    assert notifier.sent == []


def test_force_sends_even_a_brief_with_nothing_in_it(wired):
    _store, notifier, _ = wired
    result = scheduled.run_brief({"to": "nadia@example.org", "hours": 0, "force": True})
    assert result["sent"]


def _accept_the_open_ask(store) -> None:
    """Answer whatever Zamu just asked, so the brief has a completed fill in it."""
    from zamu.core.clock import SystemClock
    from zamu.core.fill import CoverageService
    from zamu.infra.notify import OutboxNotifier

    service = CoverageService(store, SystemClock(), OutboxNotifier(), base_url="https://t")
    for ask in store.list_asks(DEMO_ORG_ID):
        if ask.state.is_open and ask.token:
            service.record_response(ask.token, accept=True)
            return


# -- the risk check --------------------------------------------------------------------


def test_the_risk_check_finds_the_stale_confirmation(wired):
    scheduled.run_sweep({})
    result = scheduled.run_risk_check({})
    assert result["ok"]
    titles = {row["title"] for row in result["at_risk"]}
    assert "Morning delivery run" in titles
    assert all(row["state"] in ("at_risk", "unknown") for row in result["at_risk"])


# -- the Lambda entrypoint -------------------------------------------------------------


def test_the_handler_defaults_to_the_sweep(wired):
    assert scheduled.handler({})["job"] == "sweep"


def test_the_handler_routes_by_job(wired):
    scheduled.run_sweep({})
    assert scheduled.handler({"job": "risk"})["job"] == "risk"
    assert scheduled.handler({"job": "brief", "to": "n@example.org"})["job"] == "brief"


def test_an_unknown_job_is_reported_rather_than_run(wired):
    result = scheduled.handler({"job": "delete-everything"})
    assert not result["ok"]
    assert "sweep" in result["known"]


# -- the AgentCore entrypoint ----------------------------------------------------------


def test_agentcore_status_reports_the_gaps(wired):
    body = agentcore.handle({"action": "status"})
    assert body["ok"]
    assert body["org_name"] == "Riverside Community Food Bank"
    assert demo_gap_id() in {g["duty_id"] for g in body["gaps"]}


def test_agentcore_sweep_fills(wired):
    body = agentcore.handle({"action": "sweep"})
    assert body["ok"]
    assert any(o["outcome"] == "asked" for o in body["outcomes"])


def test_agentcore_brief_carries_the_readable_text(wired):
    agentcore.handle({"action": "sweep"})
    body = agentcore.handle({"action": "brief"})
    assert body["ok"]
    assert "Riverside Community Food Bank" in body["text"]


def test_agentcore_defaults_to_running_the_agent(wired, monkeypatch):
    monkeypatch.setattr(
        agentcore, "SETTINGS", agentcore.SETTINGS.__class__(store_kind="memory", force_planner=True)
    )
    body = agentcore.handle({"prompt": "Handle whatever needs doing."})
    assert body["ok"]
    assert body["action"] == "agent"
    assert "list_gaps" in body["tools_called"]
    assert body["refusals"] == []


def test_agentcore_rejects_an_unknown_action(wired):
    body = agentcore.handle({"action": "delete-the-roster"})
    assert not body["ok"]
    assert "unknown action" in body["error"]


def test_agentcore_turns_a_domain_error_into_an_answer(wired):
    """A refusal that arrives as a 500 tells the caller nothing about what to do."""
    body = agentcore.handle({"action": "status", "org_id": "org_nope"})
    assert not body["ok"]
    assert body["error_type"] == "NotFound"


def test_agentcore_seeds_an_empty_store_once(wired):
    store, _, _ = wired
    agentcore.handle({"action": "status"})
    first = len(store.list_duties(DEMO_ORG_ID))
    agentcore.handle({"action": "status"})
    assert len(store.list_duties(DEMO_ORG_ID)) == first


def test_the_coordinator_email_can_come_from_the_environment(wired, monkeypatch):
    """So an operator sets the address once rather than in three schedule inputs."""
    _store, notifier, _ = wired
    scheduled.run_sweep({})
    _accept_the_open_ask(_store)
    notifier.clear()

    monkeypatch.setenv("ZAMU_COORDINATOR_EMAIL", "nadia@example.org")
    result = scheduled.run_brief({})
    assert result["sent"]
    assert notifier.sent[0][0].to_email == "nadia@example.org"
