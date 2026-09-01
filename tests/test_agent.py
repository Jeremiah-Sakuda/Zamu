"""The agent, and the hook that constrains it.

The tests that matter here are the ones that prove the authority gate is real: not a
sentence in a system prompt, but a callback that cancels the tool invocation before
the tool body runs. Every one of these drives an actual `strands.Agent` over an actual
event loop, because a hook that is only unit-tested is a hook nobody has watched fire.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests import factories as f
from zamu.agent.authority_hook import AuthorityHook
from zamu.agent.build import build_agent
from zamu.agent.planner import Call, PlannedModel, Say, Use, canonical_plan
from zamu.agent.tools import TOOL_AUTHORITY, build_tools
from zamu.core.clock import FixedClock
from zamu.core.fill import CoverageService
from zamu.core.models import ActionClass, ActionResult, AskState
from zamu.core.store import InMemoryStore
from zamu.demo import demo_gap_id, seed
from zamu.infra.notify import OutboxNotifier


def build(grants=(ActionClass.SEND_ASK, ActionClass.WRITE_ROSTER), plan=None):
    roster = f.roster(
        people=(f.MARCUS, f.AMARA, f.DEVON, f.SOFIA, f.BEN),
        duties=(f.THURSDAY_GAP,),
        grants=tuple(f.grant(g) for g in grants),
    )
    store = f.store_with(roster)
    clock = FixedClock(f.NOW)
    notifier = OutboxNotifier()
    model = PlannedModel(plan) if plan else PlannedModel()
    zamu = build_agent(
        store, f.ORG_ID, clock=clock, notifier=notifier, model=model, base_url="https://t"
    )
    return zamu, store, notifier


def _tool_results(zamu) -> str:
    """Every tool result the model was shown, flattened for assertions."""
    chunks: list[str] = []
    for message in zamu.agent.messages:
        for block in message.get("content") or []:
            if "toolResult" in block:
                chunks.append(str(block["toolResult"]))
    return "\n".join(chunks)


def fixed_plan(*steps):
    """A planner that walks a scripted list of steps and then stops."""
    sequence = list(steps)

    def plan(history: list[Call]):
        index = len(history)
        return sequence[index] if index < len(sequence) else Say("done")

    return plan


# -- the loop --------------------------------------------------------------------------


def test_the_agent_completes_a_fill_end_to_end():
    zamu, store, notifier = build()
    zamu("A gap opened. Handle it.")

    called = [s.name for s in zamu.agent.model.steps if isinstance(s, Use)]
    assert called == ["list_gaps", "rank_candidates", "ask_next_person", "write_handover_brief"]
    assert len(notifier.sent) == 1

    ask = next(a for a in store.list_asks(f.ORG_ID) if a.state is AskState.SENT)
    sent = [r for r in store.list_actions(f.ORG_ID) if r.action_class is ActionClass.SEND_ASK]
    assert sent[0].result is ActionResult.VERIFIED
    assert ask.rationale


def test_the_agent_asks_exactly_one_person_per_gap():
    """Broadcast is the failure mode. It must not be reachable by looping."""
    zamu, _, notifier = build(
        plan=fixed_plan(
            Use("list_gaps", {}),
            Use("ask_next_person", {"duty_id": "dut_thursday"}),
            Use("ask_next_person", {"duty_id": "dut_thursday"}),
            Use("ask_next_person", {"duty_id": "dut_thursday"}),
        )
    )
    zamu("Fill it.")
    assert len(notifier.sent) == 1


# -- the gate --------------------------------------------------------------------------


def test_the_hook_cancels_a_send_when_no_grant_exists():
    """The whole safety argument in one test: the tool body never runs."""
    zamu, store, notifier = build(
        grants=(),
        plan=fixed_plan(Use("list_gaps", {}), Use("ask_next_person", {"duty_id": "dut_thursday"})),
    )
    zamu("Fill it.")

    assert notifier.sent == []
    assert store.list_asks(f.ORG_ID) == ()
    assert zamu.hook.rules_hit() == ("R3-no-grant",)
    assert zamu.hook.refusals[0].tool == "ask_next_person"


def test_a_cancelled_call_is_recorded_as_blocked_on_the_ledger():
    zamu, store, _ = build(
        grants=(),
        plan=fixed_plan(Use("list_gaps", {}), Use("ask_next_person", {"duty_id": "dut_thursday"})),
    )
    zamu("Fill it.")

    blocked = [r for r in store.list_actions(f.ORG_ID) if r.result is ActionResult.BLOCKED]
    assert len(blocked) == 1
    assert blocked[0].policy_rule == "R3-no-grant"
    assert blocked[0].executed_at is None


def test_the_hook_tells_the_model_not_to_route_around_the_refusal():
    zamu, _, _ = build(
        grants=(),
        plan=fixed_plan(Use("list_gaps", {}), Use("ask_next_person", {"duty_id": "dut_thursday"})),
    )
    zamu("Fill it.")
    # The refusal reaches the model as the tool's result, not as the final answer.
    transcript = _tool_results(zamu)
    assert "R3-no-grant" in transcript
    assert "Do not try another route" in transcript


def test_the_hook_lets_the_draft_fallback_through():
    """Revoking send must exercise the fallback, not cancel the call."""
    zamu, store, notifier = build(
        grants=(ActionClass.DRAFT_ASK,),
        plan=fixed_plan(Use("list_gaps", {}), Use("ask_next_person", {"duty_id": "dut_thursday"})),
    )
    zamu("Fill it.")

    assert zamu.hook.refusals == []  # the hook allowed it through
    assert notifier.sent == []  # and nothing was sent
    drafted = [a for a in store.list_asks(f.ORG_ID) if a.drafted_only]
    assert len(drafted) == 1


def test_a_withdrawal_without_a_write_grant_is_cancelled():
    zamu, store, _ = build(
        grants=(ActionClass.SEND_ASK,),
        plan=fixed_plan(
            Use(
                "record_withdrawal",
                {"person_id": f.MARCUS.id, "duty_id": "dut_thursday", "evidence": "can't make it"},
            )
        ),
    )
    zamu("Marcus dropped out.")
    assert zamu.hook.rules_hit() == ("R3-no-grant",)


def test_an_unclassified_tool_is_refused_rather_than_allowed():
    """Omission from the authority table must fail closed."""
    hook = AuthorityHook(store=InMemoryStore(), clock=FixedClock(f.NOW), org_id=f.ORG_ID)
    hook.record_to_ledger = False

    class Event:
        tool_use = {"name": "definitely_not_a_zamu_tool", "input": {}, "toolUseId": "t1"}
        cancel_tool = False

    event = Event()
    hook.before_tool_call(event)
    assert isinstance(event.cancel_tool, str)
    assert "R14-unclassified-tool" in event.cancel_tool


def test_read_tools_are_never_gated():
    """Reading is granted by connecting a roster; gating it would be theatre."""
    zamu, _, _ = build(
        grants=(),
        plan=fixed_plan(
            Use("list_gaps", {}),
            Use("read_fairness_ledger", {}),
            Use("read_receipts", {"limit": 5}),
            Use("rank_candidates", {"duty_id": "dut_thursday"}),
        ),
    )
    zamu("What is going on?")
    assert zamu.hook.refusals == []


def test_every_tool_is_declared_in_the_authority_table():
    """A tool that exists but is unclassified would be refused at runtime. Catch it here."""
    service = CoverageService(InMemoryStore(), FixedClock(f.NOW), OutboxNotifier())
    names = {t.tool_name for t in build_tools(service, f.ORG_ID)}
    assert names == set(TOOL_AUTHORITY)


# -- the planner -----------------------------------------------------------------------


def test_the_canonical_plan_starts_by_looking():
    assert canonical_plan([]) == Use("list_gaps", {"horizon_days": 21})


def test_the_canonical_plan_ranks_before_asking():
    history = [Call("list_gaps", {}, {"gaps": [{"duty_id": "dut_1"}]})]
    assert canonical_plan(history) == Use("rank_candidates", {"duty_id": "dut_1"})

    history.append(Call("rank_candidates", {"duty_id": "dut_1"}, {"candidates": []}))
    assert canonical_plan(history) == Use("ask_next_person", {"duty_id": "dut_1"})


def test_the_canonical_plan_finishes_with_a_brief_when_there_is_nothing_to_do():
    history = [Call("list_gaps", {}, {"gaps": []})]
    assert canonical_plan(history) == Use("write_handover_brief", {"hours": 24})


def test_the_planner_reports_the_brief_verbatim_rather_than_paraphrasing():
    history = [
        Call("list_gaps", {}, {"gaps": []}),
        Call("write_handover_brief", {}, {"text": "Nothing needed you."}),
    ]
    assert canonical_plan(history) == Say("Nothing needed you.")


def test_the_planner_refuses_to_fake_structured_output():
    """Better to fail loudly than to invent an interpretation of a human's message."""
    import asyncio

    with pytest.raises(NotImplementedError):
        asyncio.run(PlannedModel().structured_output(dict, []))


# -- against the seeded demo -----------------------------------------------------------


def test_the_demo_org_fills_its_thursday_gap_with_marcus():
    """The narrative in the PRD, asserted rather than described."""
    store = InMemoryStore()
    now = datetime.now(UTC).replace(hour=15, minute=0, second=0, microsecond=0)
    org = seed(store, now)
    notifier = OutboxNotifier()
    zamu = build_agent(
        store, org, clock=FixedClock(now), notifier=notifier, model=PlannedModel()
    )
    zamu("A gap opened on the roster. Handle it.")

    gap_asks = [a for a in store.list_asks(org) if a.duty_id == demo_gap_id()]
    asked = [a for a in gap_asks if a.state is AskState.SENT]
    assert len(asked) == 1

    marcus = next(p for p in store.list_people(org) if p.name.startswith("Marcus"))
    assert asked[0].person_id == marcus.id
    assert "trained for food-safety" in asked[0].rationale
    assert "carried" in asked[0].rationale

    priya = next(p for p in store.list_people(org) if p.name.startswith("Priya"))
    assert not any(a.person_id == priya.id and a.state is AskState.SENT for a in gap_asks)


def test_the_demo_never_asks_the_person_who_just_withdrew():
    store = InMemoryStore()
    now = datetime.now(UTC)
    org = seed(store, now)
    service = CoverageService(store, FixedClock(now), OutboxNotifier())
    order = service.rank_for(org, demo_gap_id())

    priya = next(p for p in store.list_people(org) if p.name.startswith("Priya"))
    assert priya.id not in {c.person_id for c in order.candidates}
    assert any(e.person_id == priya.id and "declined" in e.explanation for e in order.excluded)


def test_the_demo_seed_is_idempotent():
    store = InMemoryStore()
    now = datetime.now(UTC)
    seed(store, now)
    first = len(store.list_duties(seed(store, now)))
    seed(store, now)
    assert len(store.list_duties(seed(store, now))) == first


def test_seeding_without_grants_produces_a_read_only_zamu():
    store = InMemoryStore()
    now = datetime.now(UTC)
    org = seed(store, now, send=False, write=False)
    service = CoverageService(store, FixedClock(now), OutboxNotifier())
    result = service.ask_next(org, demo_gap_id())

    assert result.outcome.value == "drafted"
    assert result.needs_coordinator
    assert "Can you cover" in result.draft_text


def test_the_ask_window_never_outlives_the_duty():
    """A response window that closes after the shift starts is useless."""
    store = InMemoryStore()
    now = datetime.now(UTC)
    org = seed(store, now)
    service = CoverageService(store, FixedClock(now), OutboxNotifier())
    result = service.ask_next(org, demo_gap_id())
    duty = store.get_duty(org, demo_gap_id())
    assert result.expires_at is not None
    assert result.expires_at < duty.start + timedelta(seconds=1)
