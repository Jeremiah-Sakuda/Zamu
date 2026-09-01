"""The terminal surface. Smoke-level, but it covers the demo path end to end."""

from __future__ import annotations

import pytest

from zamu.cli import main
from zamu.infra.sqlite_store import SqliteStore


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "cli.sqlite")


def run(capsys, db, *argv) -> tuple[int, str]:
    code = main(["--db", db, *argv])
    return code, capsys.readouterr().out


def test_the_demo_seeds_and_the_whole_loop_runs_from_the_terminal(capsys, db):
    code, out = run(capsys, db, "demo")
    assert code == 0
    assert "Riverside Community Food Bank" in out

    code, out = run(capsys, db, "status")
    assert code == 0
    assert "uncovered" in out

    code, out = run(capsys, db, "rank")
    assert "Marcus Tran" in out
    assert "Not asked:" in out
    assert "Priya Nair already declined this duty." in out

    code, out = run(capsys, db, "fill")
    assert "ASKED" in out
    assert "Marcus Tran" in out

    _, out = run(capsys, db, "outbox")
    token = next(
        line.split()[-1] for line in out.splitlines() if "zamu accept" in line
    )

    code, out = run(capsys, db, "accept", token)
    assert "accepted and assigned" in out

    _, out = run(capsys, db, "receipts", "--limit", "2")
    assert "VERIFIED" in out
    assert "R10-explicit-acceptance" in out

    _, out = run(capsys, db, "brief")
    assert "Filled:" in out


def test_revoking_the_send_grant_changes_what_zamu_does(capsys, db):
    run(capsys, db, "demo")
    code, out = run(capsys, db, "revoke", "send_ask")
    assert code == 0

    _, out = run(capsys, db, "grants")
    assert "send an ask" in out

    _, out = run(capsys, db, "fill")
    assert "DRAFTED" in out
    assert "Can you cover" in out

    store = SqliteStore(db)
    try:
        org = store.list_orgs()[0].id
        assert all(a.drafted_only for a in store.list_asks(org) if a.state.value == "sent")
    finally:
        store.close()


def test_revoking_every_grant_stops_zamu_without_it_pretending_otherwise(capsys, db):
    run(capsys, db, "demo")
    run(capsys, db, "revoke", "send_ask")
    run(capsys, db, "revoke", "draft_ask")
    _, out = run(capsys, db, "fill")
    assert "BLOCKED" in out
    assert "R3-no-grant" in out


def test_the_never_granted_rung_cannot_be_granted_from_the_cli(capsys, db):
    run(capsys, db, "demo")
    code, out = run(capsys, db, "grant", "reassign_without_consent")
    assert code == 1
    assert "not a grant you can change" in out


def test_a_withdrawal_reopens_a_duty_and_excludes_the_person_who_left(capsys, db):
    run(capsys, db, "demo")
    store = SqliteStore(db)
    try:
        org = store.list_orgs()[0].id
        covered = next(
            d
            for d in store.list_duties(org)
            if d.assigned_person_id and d.start > store.list_duties(org)[0].start
            and d.title == "Closing shift"
        )
        holder = store.get_person(org, covered.assigned_person_id)
    finally:
        store.close()

    code, out = run(
        capsys, db, "withdraw", holder.name, covered.id, "Can't make it, sorry."
    )
    assert code == 0
    assert "WITHDRAWN" in out

    _, out = run(capsys, db, "rank", covered.id)
    assert f"{holder.name} already declined this duty." in out


def test_an_ambiguous_name_is_refused_rather_than_guessed(capsys, db):
    run(capsys, db, "demo")
    store = SqliteStore(db)
    try:
        org = store.list_orgs()[0].id
        duty = store.list_duties(org)[0].id
    finally:
        store.close()
    code = main(["--db", db, "withdraw", "a", duty, "nope"])
    assert code == 1


def test_the_agent_command_runs_the_planner(capsys, db):
    run(capsys, db, "demo")
    code, out = run(capsys, db, "agent", "--planner", "Handle whatever needs doing.")
    assert code == 0
    assert "deterministic-planner" in out
    assert "Zamu:" in out
