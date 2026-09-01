"""Swapping the backing must not change a single decision.

The claim "storage is an adapter" is easy to make and easy to get wrong: a store that
loses a frozenset, drops a timezone, or reorders a list will quietly change who gets
asked. So the same scripted fill runs against every implementation and the outcomes
are compared field by field.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests import factories as f
from zamu.core.clock import FixedClock
from zamu.core.fill import CoverageService, Outcome, ResponseOutcome
from zamu.core.models import ActionClass, ActionRecord
from zamu.core.store import InMemoryStore
from zamu.infra.notify import OutboxNotifier
from zamu.infra.sqlite_store import SqliteStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path: Path):
    if request.param == "memory":
        yield InMemoryStore()
    else:
        db = SqliteStore(tmp_path / "parity.sqlite")
        yield db
        db.close()


def _seed(store):
    roster = f.roster(
        people=(f.MARCUS, f.AMARA, f.DEVON, f.SOFIA, f.BEN),
        duties=(
            f.THURSDAY_GAP,
            f.duty("dut_hist", f.local(2026, 8, 20, 9), hours=6.0, assigned_person_id=f.AMARA.id),
        ),
        grants=(f.grant(ActionClass.SEND_ASK), f.grant(ActionClass.WRITE_ROSTER)),
    )
    store.put_org(roster.org)
    for p in roster.people:
        store.put_person(p)
    for d in roster.duties:
        store.put_duty(d)
    for g in roster.grants:
        store.put_grant(g)


def _scrub(payload: dict | None) -> dict | None:
    """Drop generated identifiers so two independent runs are comparable."""
    if payload is None:
        return None
    return {k: v for k, v in payload.items() if k != "ask_id"}


def _run(store) -> dict:
    _seed(store)
    service = CoverageService(store, FixedClock(f.NOW), OutboxNotifier(), base_url="https://t")

    order = service.rank_for(f.ORG_ID, "dut_thursday")
    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    token = store.get_ask(f.ORG_ID, asked.ask_id).token
    accepted = service.record_response(token, accept=True)
    duty = store.get_duty(f.ORG_ID, "dut_thursday")

    return {
        "ranking": [(c.person_id, round(c.score, 6)) for c in order.candidates],
        "excluded": sorted((e.person_id, e.explanation) for e in order.excluded),
        "asked": (asked.outcome, asked.person_id, asked.policy_rule, asked.rationale),
        "accepted": (accepted.outcome, accepted.verified, accepted.person_id),
        "assigned": duty.assigned_person_id,
        # Randomly generated ask ids cannot match across two independent runs, so
        # compare the shape of each receipt rather than its identifiers.
        "receipts": [
            (r.action_class, r.result, r.policy_rule, _scrub(r.intended), _scrub(r.observed))
            for r in reversed(service.ledger.recent(f.ORG_ID))
        ],
    }


def test_the_same_script_produces_the_same_outcome_on_every_backing(store):
    result = _run(store)
    assert result["asked"][0] is Outcome.ASKED
    assert result["accepted"][0] is ResponseOutcome.ACCEPTED_AND_ASSIGNED
    assert result["accepted"][1] is True
    assert result["assigned"] == result["asked"][1]

    # Pin the expected outcome so a divergence in either backing fails loudly.
    # Marcus and Devon are indistinguishable on every component here, so the order
    # comes from the final tie-break on person id — which is the point: it is stable
    # rather than arbitrary. Amara is last because she has carried six hours.
    assert [pid for pid, _ in result["ranking"]] == [f.DEVON.id, f.MARCUS.id, f.AMARA.id]
    assert result["asked"][1] == f.DEVON.id


def test_memory_and_sqlite_agree_exactly():
    with tempfile.TemporaryDirectory() as tmp:
        sqlite = SqliteStore(Path(tmp) / "agree.sqlite")
        try:
            assert _run(InMemoryStore()) == _run(sqlite)
        finally:
            sqlite.close()


def test_sqlite_survives_a_reopen(tmp_path: Path):
    """A coordinator closing the laptop must not lose the ledger."""
    path = tmp_path / "durable.sqlite"
    first = SqliteStore(path)
    before = _run(first)
    first.close()

    second = SqliteStore(path)
    try:
        roster = second.load_roster(f.ORG_ID)
        assert roster.duty("dut_thursday").assigned_person_id == before["asked"][1]
        assert len(second.list_actions(f.ORG_ID)) == len(before["receipts"])
    finally:
        second.close()


def test_the_idempotency_index_is_enforced_by_the_database(tmp_path: Path):
    """Not merely by a prior read: two writers racing must still collide."""
    import sqlite3

    from zamu.core.models import ActionRecord

    db = SqliteStore(tmp_path / "idem.sqlite")
    try:
        _seed(db)
        record = ActionRecord(
            id="act_one",
            org_id=f.ORG_ID,
            idempotency_key="shared-key",
            action_class=ActionClass.SEND_ASK,
            summary="first",
            intended={},
            policy_rule="R6",
            created_at=f.NOW,
        )
        db.append_action(record)
        from dataclasses import replace

        with pytest.raises(sqlite3.IntegrityError):
            db.append_action(replace(record, id="act_two", summary="second"))
    finally:
        db.close()


def test_sqlite_survives_concurrent_readers_and_writers(tmp_path: Path):
    """Found in the browser, not in a test: a shared connection interleaved statements
    and a roster that plainly existed came back as a 404 on one of three parallel
    requests. `check_same_thread=False` disables sqlite3's check, not its thread
    safety, so every operation now takes a lock."""
    import threading

    db = SqliteStore(tmp_path / "threads.sqlite")
    try:
        _seed(db)
        service = CoverageService(db, FixedClock(f.NOW), OutboxNotifier(), base_url="https://t")
        errors: list[BaseException] = []
        seen: list[int] = []

        def reader() -> None:
            try:
                for _ in range(40):
                    roster = db.load_roster(f.ORG_ID)
                    seen.append(len(roster.people))
                    service.rank_for(f.ORG_ID, "dut_thursday")
                    db.list_actions(f.ORG_ID)
            except BaseException as exc:  # noqa: BLE001 - the point is to catch anything
                errors.append(exc)

        def writer() -> None:
            try:
                for i in range(20):
                    duty = f.duty(f"dut_w{i}", f.local(2026, 9, 10, 9))
                    db.put_duty(duty)
                    db.append_action(
                        ActionRecord(
                            id=f"act_w{i}",
                            org_id=f.ORG_ID,
                            idempotency_key=f"key_w{i}",
                            action_class=ActionClass.SEND_ASK,
                            summary="concurrent",
                            intended={},
                            policy_rule="R6",
                            created_at=f.NOW,
                        )
                    )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert set(seen) == {5}  # every read saw the whole roster, every time
        assert len({a.id for a in db.list_actions(f.ORG_ID)}) == 20
    finally:
        db.close()
