"""The HTTP surface, including the two pages a volunteer sees."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from zamu.demo import DEMO_ORG_ID


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ZAMU_DB", str(tmp_path / "api.sqlite"))
    monkeypatch.setenv("ZAMU_BASE_URL", "http://testserver")

    from zamu.api import app as app_module

    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        yield client


def gap_id(client) -> str:
    return client.get(f"/api/orgs/{DEMO_ORG_ID}/gaps").json()[0]["id"]


def accept_path(client) -> str:
    outbox = client.get(f"/api/orgs/{DEMO_ORG_ID}/outbox").json()
    return outbox[0]["accept_url"].replace("http://testserver", "")


# -- reading ---------------------------------------------------------------------------


def test_the_api_seeds_itself_on_first_use(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert DEMO_ORG_ID in body["orgs"]


def test_the_console_screen_arrives_in_one_round_trip(client):
    body = client.get(f"/api/orgs/{DEMO_ORG_ID}").json()
    assert set(body) == {"org", "summary", "duties", "people", "grants", "brief"}
    assert body["summary"]["uncovered"] >= 1
    assert body["people"][0]["name"] == "Amara Okonkwo"  # heaviest load sorts first


def test_an_unknown_org_is_a_404(client):
    assert client.get("/api/orgs/org_nope").status_code == 404


def test_coverage_states_reach_the_console_with_their_reasons(client):
    duties = client.get(f"/api/orgs/{DEMO_ORG_ID}/duties").json()
    at_risk = [d for d in duties if d["state"] == "at_risk"]
    assert at_risk
    assert "has not confirmed since" in at_risk[0]["reason"]


def test_candidates_carry_their_rationale_and_their_exclusions(client):
    body = client.get(f"/api/orgs/{DEMO_ORG_ID}/duties/{gap_id(client)}/candidates").json()
    assert body["candidates"][0]["name"] == "Marcus Tran"
    assert "trained for food-safety" in body["candidates"][0]["rationale"]
    assert set(body["candidates"][0]["components"]) == {
        "fairness", "fit", "responsiveness", "notice", "rest"
    }
    reasons = {e["name"]: e["reason"] for e in body["excluded"]}
    assert "not trained" in reasons["Sofia Marchetti"]
    assert "opted in" in reasons["Ben Whitfield"]


def test_the_grants_screen_shows_every_rung_including_the_impossible_one(client):
    grants = {g["key"]: g for g in client.get(f"/api/orgs/{DEMO_ORG_ID}/grants").json()}
    assert grants["read"]["granted"] and not grants["read"]["changeable"]
    assert grants["reassign_without_consent"]["forbidden"]
    assert not grants["reassign_without_consent"]["changeable"]
    assert "never do this" in grants["reassign_without_consent"]["description"]


# -- acting ----------------------------------------------------------------------------


def test_a_full_fill_over_http(client):
    duty = gap_id(client)
    asked = client.post(f"/api/orgs/{DEMO_ORG_ID}/duties/{duty}/ask").json()
    assert asked["outcome"] == "asked"
    assert asked["person_name"] == "Marcus Tran"
    assert asked["policy_rule"] == "R6-opted-in-and-in-hours"

    path = accept_path(client)
    assert client.post(path).status_code == 200

    duties = {d["id"]: d for d in client.get(f"/api/orgs/{DEMO_ORG_ID}/duties").json()}
    assert duties[duty]["state"] == "covered"
    assert duties[duty]["assigned"]["name"] == "Marcus Tran"

    receipts = client.get(f"/api/orgs/{DEMO_ORG_ID}/receipts?limit=5").json()
    write = next(r for r in receipts if r["policy_rule"] == "R10-explicit-acceptance")
    assert write["result"] == "verified"
    assert write["intended"]["assigned_person_id"] == write["observed"]["assigned_person_id"]


def test_a_get_on_an_accept_link_commits_nothing(client):
    """Mail clients follow links. A spam filter must not volunteer somebody for Saturday."""
    duty = gap_id(client)
    client.post(f"/api/orgs/{DEMO_ORG_ID}/duties/{duty}/ask")
    path = accept_path(client)

    page = client.get(path)
    assert page.status_code == 200
    assert "Nothing has changed yet" in page.text

    duties = {d["id"]: d for d in client.get(f"/api/orgs/{DEMO_ORG_ID}/duties").json()}
    assert duties[duty]["state"] == "uncovered"
    assert duties[duty]["assigned"] is None


def test_the_confirmation_page_repeats_the_shift_details(client):
    """It is often opened days later, from a link somebody tapped without remembering."""
    client.post(f"/api/orgs/{DEMO_ORG_ID}/duties/{gap_id(client)}/ask")
    page = client.get(accept_path(client)).text
    assert "Evening distribution" in page
    assert "Role: Distribution" in page
    assert "Riverside Community Food Bank" in page


def test_declining_over_http_advances_to_the_next_person(client):
    duty = gap_id(client)
    first = client.post(f"/api/orgs/{DEMO_ORG_ID}/duties/{duty}/ask").json()
    decline = accept_path(client).replace("/yes", "/no")
    client.post(decline)

    second = client.post(f"/api/orgs/{DEMO_ORG_ID}/duties/{duty}/ask").json()
    assert second["outcome"] == "asked"
    assert second["person_name"] != first["person_name"]


def test_an_unrecognised_token_is_a_404_page_not_a_crash(client):
    page = client.get("/r/not-a-token/yes")
    assert page.status_code == 404
    assert "not one we recognise" in page.text


def test_a_nonsense_answer_is_rejected(client):
    assert client.get("/r/whatever/maybe").status_code == 400


def test_a_second_post_is_idempotent(client):
    client.post(f"/api/orgs/{DEMO_ORG_ID}/duties/{gap_id(client)}/ask")
    path = accept_path(client)
    assert client.post(path).status_code == 200
    again = client.post(path)
    assert again.status_code == 200
    assert "already answered" in again.text.lower()


# -- authority over http ---------------------------------------------------------------


def test_revoking_the_send_grant_changes_what_the_api_does(client):
    client.post(f"/api/orgs/{DEMO_ORG_ID}/grants/send_ask", json={"granted": False})
    grants = {g["key"]: g for g in client.get(f"/api/orgs/{DEMO_ORG_ID}/grants").json()}
    assert not grants["send_ask"]["granted"]

    result = client.post(f"/api/orgs/{DEMO_ORG_ID}/duties/{gap_id(client)}/ask").json()
    assert result["outcome"] == "drafted"
    assert result["needs_coordinator"]
    assert "Can you cover" in result["draft_text"]
    assert client.get(f"/api/orgs/{DEMO_ORG_ID}/outbox").json() == []


def test_the_never_granted_rung_cannot_be_granted_over_http(client):
    response = client.post(
        f"/api/orgs/{DEMO_ORG_ID}/grants/reassign_without_consent", json={"granted": True}
    )
    assert response.status_code == 400
    grants = {g["key"]: g for g in client.get(f"/api/orgs/{DEMO_ORG_ID}/grants").json()}
    assert not grants["reassign_without_consent"]["granted"]


def test_read_cannot_be_revoked(client):
    response = client.post(f"/api/orgs/{DEMO_ORG_ID}/grants/read", json={"granted": False})
    assert response.status_code == 400


def test_regranting_is_idempotent(client):
    for _ in range(3):
        client.post(f"/api/orgs/{DEMO_ORG_ID}/grants/send_ask", json={"granted": True})
    all_grants = client.get(f"/api/orgs/{DEMO_ORG_ID}/grants").json()
    grants = [g for g in all_grants if g["key"] == "send_ask"]
    assert len(grants) == 1 and grants[0]["granted"]


# -- the agent and the sandbox ---------------------------------------------------------


def test_the_agent_endpoint_reports_what_it_called(client, monkeypatch):
    monkeypatch.setenv("ZAMU_FORCE_PLANNER", "1")
    body = client.post(f"/api/orgs/{DEMO_ORG_ID}/agent", json={"message": "Handle it."}).json()
    assert "planner" in body["model"]
    assert "list_gaps" in body["tools_called"]
    assert body["refusals"] == []


def test_the_agent_endpoint_surfaces_refusals(client, monkeypatch):
    monkeypatch.setenv("ZAMU_FORCE_PLANNER", "1")
    client.post(f"/api/orgs/{DEMO_ORG_ID}/grants/send_ask", json={"granted": False})
    client.post(f"/api/orgs/{DEMO_ORG_ID}/grants/draft_ask", json={"granted": False})
    body = client.post(f"/api/orgs/{DEMO_ORG_ID}/agent", json={}).json()
    assert body["refusals"]
    assert body["refusals"][0]["rule"] == "R3-no-grant"


def test_resetting_the_sandbox_puts_the_story_back(client):
    duty = gap_id(client)
    client.post(f"/api/orgs/{DEMO_ORG_ID}/duties/{duty}/ask")
    client.post(accept_path(client))
    assert client.get(f"/api/orgs/{DEMO_ORG_ID}").json()["summary"]["uncovered"] == 1

    client.post("/api/demo/reset")
    assert client.get(f"/api/orgs/{DEMO_ORG_ID}").json()["summary"]["uncovered"] == 2
    assert client.get(f"/api/orgs/{DEMO_ORG_ID}/receipts").json() == []
