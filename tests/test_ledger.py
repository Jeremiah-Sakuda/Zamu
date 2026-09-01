"""Receipts, idempotency, and the refusal to treat a success response as truth."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests import factories as f
from zamu.core.authority import Decision, ProposedAction
from zamu.core.clock import FixedClock
from zamu.core.errors import NotFound
from zamu.core.ids import ask_idempotency_key, assignment_idempotency_key, seeded_id
from zamu.core.ledger import Ledger
from zamu.core.models import ActionClass, ActionResult
from zamu.core.store import InMemoryStore
from zamu.core.verification import verify


@pytest.fixture
def ledger():
    return Ledger(InMemoryStore(), FixedClock(f.NOW))


def _action(action_class=ActionClass.WRITE_ROSTER):
    return ProposedAction(
        org_id=f.ORG_ID,
        action_class=action_class,
        summary="Assign Marcus to Thursday",
        person_id=f.MARCUS.id,
        duty_id="dut_thursday",
        payload={"assigned_person_id": f.MARCUS.id},
    )


def _allowed():
    return Decision(
        True, ActionClass.WRITE_ROSTER, "R10-explicit-acceptance", "backed by acceptance"
    )


def _refused():
    return Decision(
        False, ActionClass.SEND_ASK, "R3-no-grant", "Nobody has granted permission to send an ask."
    )


# -- verification ----------------------------------------------------------------------


def test_matching_observation_verifies():
    result = verify({"assigned_person_id": "per_marcus"}, {"assigned_person_id": "per_marcus"})
    assert result.result is ActionResult.VERIFIED
    assert result.ok


def test_a_missing_observation_is_a_failure_not_a_success():
    result = verify({"assigned_person_id": "per_marcus"}, None)
    assert result.result is ActionResult.FAILED
    assert not result.ok


def test_a_disagreeing_observation_is_a_conflict():
    """The case a success response hides: the write landed on the wrong value."""
    result = verify({"assigned_person_id": "per_marcus"}, {"assigned_person_id": "per_devon"})
    assert result.result is ActionResult.CONFLICTED
    assert result.mismatches == ("assigned_person_id: intended 'per_marcus', found 'per_devon'",)


def test_verification_ignores_extra_fields_in_the_observation():
    result = verify({"a": 1}, {"a": 1, "updated_at": "whenever"})
    assert result.ok


# -- the ledger ------------------------------------------------------------------------


def test_an_entry_is_written_before_execution():
    store = InMemoryStore()
    led = Ledger(store, FixedClock(f.NOW))
    entry = led.begin(_action(), _allowed(), {"assigned_person_id": f.MARCUS.id}, "idem_1")

    assert not entry.replayed
    assert entry.record.executed_at is None
    assert entry.record.result is None
    assert entry.record.policy_rule == "R10-explicit-acceptance"
    assert led.open_entries(f.ORG_ID) == (entry.record,)


def test_the_same_idempotency_key_replays_instead_of_repeating():
    """A retried tool call must not send a second message or write a second row."""
    store = InMemoryStore()
    led = Ledger(store, FixedClock(f.NOW))
    first = led.begin(_action(), _allowed(), {"a": 1}, "idem_same")
    second = led.begin(_action(), _allowed(), {"a": 1}, "idem_same")

    assert second.replayed
    assert second.record.id == first.record.id
    assert len(store.list_actions(f.ORG_ID)) == 1


def test_closing_with_a_matching_read_verifies_the_entry():
    store = InMemoryStore()
    clock = FixedClock(f.NOW)
    led = Ledger(store, clock)
    entry = led.begin(_action(), _allowed(), {"assigned_person_id": f.MARCUS.id}, "idem_1")

    executed = led.mark_executed(entry.record)
    clock.advance(2)
    closed = led.close(executed, {"assigned_person_id": f.MARCUS.id})

    assert closed.result is ActionResult.VERIFIED
    assert closed.observed == {"assigned_person_id": f.MARCUS.id}
    assert closed.executed_at is not None
    assert closed.verified_at > closed.executed_at
    assert led.open_entries(f.ORG_ID) == ()


def test_closing_with_a_disagreeing_read_conflicts():
    store = InMemoryStore()
    led = Ledger(store, FixedClock(f.NOW))
    entry = led.begin(_action(), _allowed(), {"assigned_person_id": f.MARCUS.id}, "idem_1")
    closed = led.close(entry.record, {"assigned_person_id": f.DEVON.id})

    assert closed.result is ActionResult.CONFLICTED
    assert "intended" in closed.detail


def test_failing_an_entry_records_the_reason():
    store = InMemoryStore()
    led = Ledger(store, FixedClock(f.NOW))
    entry = led.begin(_action(), _allowed(), {"a": 1}, "idem_1")
    closed = led.fail(entry.record, "The email provider rejected the recipient.")

    assert closed.result is ActionResult.FAILED
    assert closed.observed is None
    assert "rejected" in closed.detail


def test_a_blocked_action_is_recorded_without_being_executed():
    """The times Zamu did not act are exactly the times the coordinator needs told."""
    store = InMemoryStore()
    led = Ledger(store, FixedClock(f.NOW))
    record = led.record_blocked(_action(ActionClass.SEND_ASK), _refused(), "idem_blocked")

    assert record.result is ActionResult.BLOCKED
    assert record.executed_at is None
    assert record.policy_rule == "R3-no-grant"
    assert led.blocked_since(f.ORG_ID, f.NOW - timedelta(hours=1)) == (record,)


def test_blocking_is_idempotent_too():
    store = InMemoryStore()
    led = Ledger(store, FixedClock(f.NOW))
    first = led.record_blocked(_action(ActionClass.SEND_ASK), _refused(), "idem_blocked")
    second = led.record_blocked(_action(ActionClass.SEND_ASK), _refused(), "idem_blocked")
    assert first.id == second.id
    assert len(store.list_actions(f.ORG_ID)) == 1


def test_recent_entries_come_back_newest_first():
    store = InMemoryStore()
    clock = FixedClock(f.NOW)
    led = Ledger(store, clock)
    for i in range(3):
        led.begin(_action(), _allowed(), {"n": i}, f"idem_{i}")
        clock.advance(60)

    recent = led.recent(f.ORG_ID)
    assert [r.intended["n"] for r in recent] == [2, 1, 0]
    assert [r.intended["n"] for r in led.recent(f.ORG_ID, limit=2)] == [2, 1]


def test_since_filters_by_creation_time():
    store = InMemoryStore()
    clock = FixedClock(f.NOW)
    led = Ledger(store, clock)
    led.begin(_action(), _allowed(), {"n": 0}, "idem_0")
    clock.advance(3600)
    cutoff = clock.now()
    led.begin(_action(), _allowed(), {"n": 1}, "idem_1")

    assert [r.intended["n"] for r in led.since(f.ORG_ID, cutoff)] == [1]


def test_a_stored_receipt_cannot_be_edited_from_outside():
    """Receipts are evidence. Handing out a live reference would make them a draft."""
    store = InMemoryStore()
    led = Ledger(store, FixedClock(f.NOW))
    payload = {"assigned_person_id": f.MARCUS.id}
    entry = led.begin(_action(), _allowed(), payload, "idem_1")

    payload["assigned_person_id"] = "per_tampered"
    entry.record.intended["assigned_person_id"] = "per_tampered"

    stored = store.get_action(f.ORG_ID, entry.record.id)
    assert stored.intended["assigned_person_id"] == f.MARCUS.id


def test_updating_an_unknown_action_raises():
    store = InMemoryStore()
    led = Ledger(store, FixedClock(f.NOW))
    entry = led.begin(_action(), _allowed(), {"a": 1}, "idem_1")
    other_store = InMemoryStore()
    with pytest.raises(NotFound):
        Ledger(other_store, FixedClock(f.NOW)).close(entry.record, {"a": 1})


# -- identifiers -----------------------------------------------------------------------


def test_seeded_ids_are_stable_and_prefixed():
    assert seeded_id("dut", "riverside", "thursday") == seeded_id("dut", "riverside", "thursday")
    assert seeded_id("dut", "a").startswith("dut_")
    assert seeded_id("dut", "a") != seeded_id("dut", "b")


def test_idempotency_keys_collide_only_for_the_same_logical_action():
    a = assignment_idempotency_key("dut_1", "per_1", "ask_1")
    assert a == assignment_idempotency_key("dut_1", "per_1", "ask_1")
    assert a != assignment_idempotency_key("dut_1", "per_2", "ask_1")

    # An ask key is not time-scoped: one person is asked about one duty at most once,
    # so a retry an hour later must collide rather than produce a second question.
    b = ask_idempotency_key("dut_1", "per_1")
    assert b == ask_idempotency_key("dut_1", "per_1")
    assert b != ask_idempotency_key("dut_1", "per_2")
    assert b != ask_idempotency_key("dut_2", "per_1")
