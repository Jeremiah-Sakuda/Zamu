"""The gate must not be persuadable.

These are the most important tests in the repository. If the ranking is wrong, a
coordinator sees an odd suggestion. If the gate is wrong, Zamu messages somebody who
never agreed to hear from it.
"""

from __future__ import annotations

from datetime import time, timedelta

import pytest

from tests import factories as f
from zamu.core.authority import (
    Decision,
    ProposedAction,
    authorize,
    granted_levels,
)
from zamu.core.errors import NotAuthorized
from zamu.core.models import ActionClass, AskState, QuietHours


def _send(person_id=f.MARCUS.id, duty_id="dut_thursday") -> ProposedAction:
    return ProposedAction(
        org_id=f.ORG_ID,
        action_class=ActionClass.SEND_ASK,
        summary="Ask about Thursday",
        person_id=person_id,
        duty_id=duty_id,
    )


def _write(ask_id="ask_yes", person_id=f.MARCUS.id, duty_id="dut_thursday", key="idem_1"):
    payload = {}
    if ask_id is not None:
        payload["ask_id"] = ask_id
    if key is not None:
        payload["idempotency_key"] = key
    return ProposedAction(
        org_id=f.ORG_ID,
        action_class=ActionClass.WRITE_ROSTER,
        summary="Assign Thursday",
        person_id=person_id,
        duty_id=duty_id,
        payload=payload,
    )


# -- R0: the rung that is never granted ------------------------------------------------


def test_reassignment_without_consent_is_refused_even_with_a_grant():
    """A grant for the forbidden class must not create the power. This is the
    difference between a policy and a prompt."""
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP,),
        grants=(f.grant(ActionClass.REASSIGN_WITHOUT_CONSENT),),
    )
    action = ProposedAction(
        org_id=f.ORG_ID,
        action_class=ActionClass.REASSIGN_WITHOUT_CONSENT,
        summary="Just move Marcus onto Thursday",
        person_id=f.MARCUS.id,
    )
    decision = authorize(action, roster, f.NOW)
    assert not decision.allowed
    assert decision.rule == "R0-never-implemented"
    assert ActionClass.REASSIGN_WITHOUT_CONSENT not in granted_levels(roster, f.NOW)


# -- R1/R2: org scoping and the default read grant -------------------------------------


def test_reading_is_allowed_as_soon_as_a_roster_exists():
    roster = f.roster(people=(f.MARCUS,))
    action = ProposedAction(f.ORG_ID, ActionClass.READ, "Read the roster")
    assert authorize(action, roster, f.NOW).allowed


def test_an_action_aimed_at_another_org_is_refused():
    roster = f.roster(people=(f.MARCUS,), grants=(f.grant(ActionClass.SEND_ASK),))
    action = ProposedAction(
        "org_somewhere_else", ActionClass.SEND_ASK, "Ask", person_id=f.MARCUS.id
    )
    decision = authorize(action, roster, f.NOW)
    assert not decision.allowed
    assert decision.rule == "R1-org-mismatch"


# -- R3: a grant must exist ------------------------------------------------------------


def test_sending_without_a_grant_is_refused():
    roster = f.roster(people=(f.MARCUS,), duties=(f.THURSDAY_GAP,))
    decision = authorize(_send(), roster, f.NOW)
    assert not decision.allowed
    assert decision.rule == "R3-no-grant"
    assert "send an ask" in decision.reason


def test_a_revoked_grant_stops_working_immediately():
    """The demo's counterfactual: revoke the send grant and rerun the same gap."""
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP,),
        grants=(f.grant(ActionClass.SEND_ASK, revoked_at=f.NOW - timedelta(minutes=1)),),
    )
    decision = authorize(_send(), roster, f.NOW)
    assert not decision.allowed
    assert decision.rule == "R3-no-grant"


def test_a_grant_scoped_to_some_people_does_not_cover_the_rest():
    roster = f.roster(
        people=(f.MARCUS, f.DEVON),
        duties=(f.THURSDAY_GAP,),
        grants=(f.grant(ActionClass.SEND_ASK, person_scope=frozenset({f.DEVON.id})),),
    )
    assert authorize(_send(person_id=f.DEVON.id), roster, f.NOW).allowed
    refused = authorize(_send(person_id=f.MARCUS.id), roster, f.NOW)
    assert not refused.allowed
    assert refused.rule == "R3-outside-grant-scope"


def test_a_grant_is_not_active_before_it_was_granted():
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP,),
        grants=(f.grant(ActionClass.SEND_ASK, granted_at=f.NOW + timedelta(days=1)),),
    )
    assert not authorize(_send(), roster, f.NOW).allowed


def test_a_send_grant_does_not_imply_a_write_grant():
    """Each rung is earned separately. Escalation is never automatic."""
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP,),
        asks=(f.ask("ask_yes", "dut_thursday", f.MARCUS.id, state=AskState.ACCEPTED),),
        grants=(f.grant(ActionClass.SEND_ASK),),
    )
    assert authorize(_send(), roster, f.NOW).allowed
    assert not authorize(_write(), roster, f.NOW).allowed


# -- R4: drafting stays inside ---------------------------------------------------------


def test_drafting_is_permitted_for_someone_who_never_opted_in():
    roster = f.roster(
        people=(f.BEN,),
        duties=(f.THURSDAY_GAP,),
        grants=(f.grant(ActionClass.DRAFT_ASK),),
    )
    action = ProposedAction(f.ORG_ID, ActionClass.DRAFT_ASK, "Draft an ask", person_id=f.BEN.id)
    decision = authorize(action, roster, f.NOW)
    assert decision.allowed
    assert decision.rule == "R4-draft-stays-inside"


# -- R5 to R8: conditions on contacting a human ----------------------------------------


def test_sending_to_someone_not_on_the_roster_is_refused():
    roster = f.roster(
        people=(f.MARCUS,), duties=(f.THURSDAY_GAP,), grants=(f.grant(ActionClass.SEND_ASK),)
    )
    decision = authorize(_send(person_id="per_stranger"), roster, f.NOW)
    assert not decision.allowed
    assert decision.rule == "R5-unknown-person"


def test_sending_to_an_inactive_person_is_refused():
    gone = f.person("per_gone", "Departed", qualifications=("food-safety",), active=False)
    roster = f.roster(
        people=(gone,), duties=(f.THURSDAY_GAP,), grants=(f.grant(ActionClass.SEND_ASK),)
    )
    decision = authorize(_send(person_id=gone.id), roster, f.NOW)
    assert not decision.allowed
    assert decision.rule == "R5-person-inactive"


def test_a_grant_does_not_override_a_missing_opt_in():
    """The coordinator can grant Zamu the power to send. Only Ben can grant the
    right to send to Ben."""
    roster = f.roster(
        people=(f.BEN,), duties=(f.THURSDAY_GAP,), grants=(f.grant(ActionClass.SEND_ASK),)
    )
    decision = authorize(_send(person_id=f.BEN.id), roster, f.NOW)
    assert not decision.allowed
    assert decision.rule == "R6-no-opt-in"


def test_quiet_hours_defer_a_send():
    owl = f.person(
        "per_owl",
        "Night Owl",
        qualifications=("food-safety",),
        quiet_hours=QuietHours(start=time(21, 0), end=time(8, 0)),
    )
    roster = f.roster(
        people=(owl,), duties=(f.THURSDAY_GAP,), grants=(f.grant(ActionClass.SEND_ASK),)
    )
    night = f.local(2026, 9, 3, 23)
    decision = authorize(_send(person_id=owl.id), roster, night)
    assert not decision.allowed
    assert decision.rule == "R7-quiet-hours"
    assert "will wait" in decision.reason


def test_the_ask_budget_is_enforced_at_the_gate_too():
    """Enforced in eligibility and again here, because the gate must hold even if a
    caller skips the ranking."""
    asks = tuple(
        f.ask(f"ask_{i}", "dut_other", f.MARCUS.id, sent_at=f.NOW - timedelta(days=i + 1))
        for i in range(3)
    )
    roster = f.roster(
        people=(f.MARCUS,),
        duties=(f.THURSDAY_GAP,),
        asks=asks,
        grants=(f.grant(ActionClass.SEND_ASK),),
    )
    decision = authorize(_send(), roster, f.NOW)
    assert not decision.allowed
    assert decision.rule == "R8-ask-budget"


def test_a_clean_send_is_allowed():
    roster = f.roster(
        people=(f.MARCUS,), duties=(f.THURSDAY_GAP,), grants=(f.grant(ActionClass.SEND_ASK),)
    )
    decision = authorize(_send(), roster, f.NOW)
    assert decision.allowed
    assert decision.grant_id == "gr_send_ask"


# -- R10 to R12: a roster write needs a real acceptance --------------------------------


def _write_roster(**kwargs):
    asks = kwargs.pop(
        "asks", (f.ask("ask_yes", "dut_thursday", f.MARCUS.id, state=AskState.ACCEPTED),)
    )
    return f.roster(
        people=(f.MARCUS, f.DEVON),
        duties=(f.THURSDAY_GAP, f.duty("dut_other", f.local(2026, 9, 6, 18))),
        asks=asks,
        grants=(f.grant(ActionClass.WRITE_ROSTER),),
        **kwargs,
    )


def test_a_write_backed_by_an_acceptance_is_allowed():
    decision = authorize(_write(), _write_roster(), f.NOW)
    assert decision.allowed
    assert decision.rule == "R10-explicit-acceptance"


def test_a_write_with_no_acceptance_referenced_is_refused():
    decision = authorize(_write(ask_id=None), _write_roster(), f.NOW)
    assert not decision.allowed
    assert decision.rule == "R10-no-acceptance"


def test_a_write_citing_an_unknown_acceptance_is_refused():
    decision = authorize(_write(ask_id="ask_imaginary"), _write_roster(), f.NOW)
    assert not decision.allowed
    assert decision.rule == "R10-unknown-acceptance"


def test_a_write_citing_an_unanswered_ask_is_refused():
    roster = _write_roster(
        asks=(f.ask("ask_yes", "dut_thursday", f.MARCUS.id, state=AskState.SENT),)
    )
    decision = authorize(_write(), roster, f.NOW)
    assert not decision.allowed
    assert decision.rule == "R10-acceptance-not-recorded"


def test_a_write_assigning_a_different_person_than_accepted_is_refused():
    decision = authorize(_write(person_id=f.DEVON.id), _write_roster(), f.NOW)
    assert not decision.allowed
    assert decision.rule == "R11-acceptance-mismatch"


def test_a_write_changing_a_different_duty_than_accepted_is_refused():
    decision = authorize(_write(duty_id="dut_other"), _write_roster(), f.NOW)
    assert not decision.allowed
    assert decision.rule == "R11-acceptance-mismatch"


def test_a_write_without_an_idempotency_key_is_refused():
    decision = authorize(_write(key=None), _write_roster(), f.NOW)
    assert not decision.allowed
    assert decision.rule == "R12-no-idempotency-key"


# -- helpers ---------------------------------------------------------------------------


def test_require_raises_on_refusal_and_returns_on_success():
    refusal = Decision(False, ActionClass.SEND_ASK, "R3-no-grant", "nobody granted it")
    with pytest.raises(NotAuthorized) as excinfo:
        refusal.require()
    assert "R3-no-grant" in str(excinfo.value)

    allowed = Decision(True, ActionClass.READ, "R2-read-is-default", "fine")
    assert allowed.require() is allowed


def test_granted_levels_always_includes_read():
    roster = f.roster(people=(f.MARCUS,))
    assert granted_levels(roster, f.NOW) == frozenset({ActionClass.READ})


def test_granted_levels_reflects_active_grants_only():
    roster = f.roster(
        people=(f.MARCUS,),
        grants=(
            f.grant(ActionClass.SEND_ASK),
            f.grant(
                ActionClass.WRITE_ROSTER,
                grant_id="gr_revoked",
                revoked_at=f.NOW - timedelta(hours=1),
            ),
        ),
    )
    assert granted_levels(roster, f.NOW) == frozenset({ActionClass.READ, ActionClass.SEND_ASK})
