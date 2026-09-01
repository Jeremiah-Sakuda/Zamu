"""The DynamoDB backing, against a real DynamoDB API surface via moto.

The point of these tests is not coverage for its own sake. A store that loses a
frozenset, returns a Decimal where the code expects a float, or fails to enforce an
idempotency claim will change who Zamu asks and whether it asks them twice — and it
will do so only in the deployed environment, where nobody is watching a terminal.
"""

from __future__ import annotations

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from moto import mock_aws  # noqa: E402

from tests import factories as f  # noqa: E402
from tests.test_store_parity import _run, _seed  # noqa: E402
from zamu.core.clock import FixedClock  # noqa: E402
from zamu.core.errors import Conflict, NotFound  # noqa: E402
from zamu.core.fill import CoverageService, Outcome, ResponseOutcome  # noqa: E402
from zamu.core.models import ActionClass, ActionRecord  # noqa: E402
from zamu.core.store import InMemoryStore  # noqa: E402
from zamu.infra.dynamo_store import DynamoStore  # noqa: E402
from zamu.infra.notify import OutboxNotifier  # noqa: E402

TABLE = "zamu-test"


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        db = DynamoStore(TABLE, region="us-east-1")
        db.create_table()
        yield db


def test_creating_the_table_is_safe_to_repeat(store):
    """It runs on every cold start; a second call must not explode."""
    store.create_table()
    assert store.get_org("org_nope") is None


def test_the_roster_round_trips_exactly(store):
    _seed(store)
    roster = store.load_roster(f.ORG_ID)

    assert {p.id for p in roster.people} == {
        f.MARCUS.id, f.AMARA.id, f.DEVON.id, f.SOFIA.id, f.BEN.id
    }
    marcus = roster.person(f.MARCUS.id)
    assert marcus.qualifications == frozenset({"food-safety"})
    assert ActionClass.SEND_ASK in marcus.opt_ins
    assert roster.person(f.BEN.id).opt_ins == frozenset()

    duty = roster.duty("dut_thursday")
    assert duty.window.start == f.THURSDAY_GAP.window.start
    assert duty.hours == pytest.approx(2.0)  # a Decimal here would break ranking
    assert isinstance(duty.hours, float)


def test_dynamo_agrees_with_the_in_memory_reference(store):
    """The same scripted fill, compared field by field against the reference."""
    assert _run(store) == _run(InMemoryStore())


def test_a_full_fill_works_on_dynamo(store):
    _seed(store)
    service = CoverageService(store, FixedClock(f.NOW), OutboxNotifier(), base_url="https://t")

    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    assert asked.outcome is Outcome.ASKED

    ask = store.get_ask(f.ORG_ID, asked.ask_id)
    response = service.record_response(ask.token, accept=True)
    assert response.outcome is ResponseOutcome.ACCEPTED_AND_ASSIGNED
    assert response.verified
    assert store.get_duty(f.ORG_ID, "dut_thursday").assigned_person_id == asked.person_id


def test_an_ask_is_findable_by_its_one_tap_token(store):
    _seed(store)
    service = CoverageService(store, FixedClock(f.NOW), OutboxNotifier(), base_url="https://t")
    asked = service.ask_next(f.ORG_ID, "dut_thursday")
    token = store.get_ask(f.ORG_ID, asked.ask_id).token

    assert store.get_ask_by_token(token).id == asked.ask_id
    assert store.get_ask_by_token("not-a-token") is None
    assert store.get_ask_by_token("") is None


def test_the_idempotency_claim_is_conditional_not_advisory(store):
    """Two workers on the same duplicated webhook must not both proceed."""
    _seed(store)
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
    store.append_action(record)

    from dataclasses import replace

    with pytest.raises(Conflict):
        store.append_action(replace(record, id="act_two", summary="second"))

    assert store.find_action_by_key(f.ORG_ID, "shared-key").summary == "first"


def test_the_ledger_treats_a_lost_race_as_a_replay(store):
    """When another worker claims the key first, this one must return their entry
    rather than raising — the whole point is that the action happens exactly once."""
    from zamu.core.authority import Decision, ProposedAction
    from zamu.core.ledger import Ledger

    _seed(store)
    ledger = Ledger(store, FixedClock(f.NOW))
    action = ProposedAction(f.ORG_ID, ActionClass.SEND_ASK, "ask", person_id=f.MARCUS.id)
    decision = Decision(True, ActionClass.SEND_ASK, "R6", "fine")

    first = ledger.begin(action, decision, {"a": 1}, "race-key")
    second = ledger.begin(action, decision, {"a": 1}, "race-key")

    assert not first.replayed
    assert second.replayed
    assert second.record.id == first.record.id


def test_the_ledger_reads_back_newest_first(store):
    from zamu.core.authority import Decision, ProposedAction
    from zamu.core.clock import FixedClock as Clock
    from zamu.core.ledger import Ledger

    _seed(store)
    clock = Clock(f.NOW)
    ledger = Ledger(store, clock)
    action = ProposedAction(f.ORG_ID, ActionClass.SEND_ASK, "ask")
    decision = Decision(True, ActionClass.SEND_ASK, "R6", "fine")

    for i in range(4):
        ledger.begin(action, decision, {"n": i}, f"key-{i}")
        clock.advance(60)

    assert [r.intended["n"] for r in store.list_actions(f.ORG_ID)] == [3, 2, 1, 0]
    assert [r.intended["n"] for r in store.list_actions(f.ORG_ID, limit=2)] == [3, 2]


def test_updating_an_unknown_action_raises(store):
    _seed(store)
    record = ActionRecord(
        id="act_ghost",
        org_id=f.ORG_ID,
        idempotency_key="ghost",
        action_class=ActionClass.READ,
        summary="ghost",
        intended={},
        policy_rule="R2",
        created_at=f.NOW,
    )
    with pytest.raises(NotFound):
        store.update_action(record)


def test_an_unknown_org_raises_rather_than_returning_an_empty_roster(store):
    with pytest.raises(NotFound):
        store.load_roster("org_nope")


def test_deleting_an_org_removes_its_whole_partition(store):
    _seed(store)
    service = CoverageService(store, FixedClock(f.NOW), OutboxNotifier(), base_url="https://t")
    service.ask_next(f.ORG_ID, "dut_thursday")

    store.delete_org(f.ORG_ID)

    assert store.list_orgs() == ()
    assert store.list_people(f.ORG_ID) == ()
    assert store.list_actions(f.ORG_ID) == ()


def test_the_seeded_demo_loads_onto_dynamo(store):
    from datetime import UTC, datetime

    from zamu.core.ranking import rank
    from zamu.demo import demo_gap_id, seed

    now = datetime.now(UTC)
    org_id = seed(store, now)
    roster = store.load_roster(org_id)
    order = rank(roster.duty(demo_gap_id()), roster, now)

    assert order.first.person_name == "Marcus Tran"
    assert any("declined" in e.explanation for e in order.excluded)
