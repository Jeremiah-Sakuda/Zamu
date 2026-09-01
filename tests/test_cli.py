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


def test_a_coordinator_can_go_from_a_spreadsheet_to_a_roster(capsys, db, tmp_path):
    """Setup in one sitting: create an org, paste two exports, see coverage."""
    people = tmp_path / "people.csv"
    people.write_text(
        "Full Name,Email Address,Skills,Consent\n"
        'Nadia Ferreira,nadia@example.org,"food-safety, driver",yes\n'
        "Sam Okoro,sam@example.org,food-safety,no\n"
        ",broken@example.org,food-safety,yes\n"
    )
    duties = tmp_path / "duties.csv"
    duties.write_text(
        "Shift,Start,End,Role,Requires,Assigned To\n"
        "Evening distribution,2026-10-01 18:00,2026-10-01 20:00,"
        "Distribution,food-safety,Nadia Ferreira\n"
        "Saturday intake,2026-10-03 08:00,2026-10-03 13:00,Intake,food-safety,\n"
    )

    code, out = run(capsys, db, "new-org", "Northside Pantry", "--id", "org_north")
    assert code == 0
    assert "org_north" in out
    # The new-org output tells them what Zamu still may not do.
    assert "grant send_ask" in out

    code, out = run(capsys, db, "--org", "org_north", "import", "people", str(people))
    assert code == 0
    assert "Imported 2 people. 1 row skipped." in out
    assert "Row 4: no name." in out
    assert "consent belongs to them" in out

    code, out = run(capsys, db, "--org", "org_north", "import", "duties", str(duties))
    assert code == 0
    assert "Imported 2 duties." in out

    _, out = run(capsys, db, "--org", "org_north", "status")
    assert "Someone is assigned but has never confirmed." in out
    assert "Nobody is assigned to this duty." in out


def test_a_dry_run_writes_nothing(capsys, db, tmp_path):
    people = tmp_path / "people.csv"
    people.write_text("Name,Email\nSam Okoro,sam@example.org\n")
    run(capsys, db, "new-org", "Northside", "--id", "org_north")

    _, out = run(capsys, db, "--org", "org_north", "import", "people", str(people), "--dry-run")
    assert "Nothing was written" in out

    store = SqliteStore(db)
    try:
        assert store.list_people("org_north") == ()
    finally:
        store.close()


def test_importing_into_an_org_that_does_not_exist_says_how_to_make_one(capsys, db, tmp_path):
    people = tmp_path / "people.csv"
    people.write_text("Name,Email\nSam,sam@example.org\n")
    code = main(["--db", db, "--org", "org_missing", "import", "people", str(people)])
    assert code == 1
