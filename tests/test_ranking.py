"""The ranking must be fair, explainable, and identical on every run."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests import factories as f
from zamu.core.models import ActionClass, AskState
from zamu.core.ranking import (
    WEIGHT_FAIRNESS,
    WEIGHT_FIT,
    WEIGHT_NOTICE,
    WEIGHT_RESPONSIVENESS,
    WEIGHT_REST,
    rank,
)


def test_weights_sum_to_one():
    total = WEIGHT_FAIRNESS + WEIGHT_FIT + WEIGHT_RESPONSIVENESS + WEIGHT_NOTICE + WEIGHT_REST
    assert total == pytest.approx(1.0)


def test_the_under_carried_person_is_asked_first():
    """The headline behaviour. Amara has carried two recent shifts; Marcus none."""
    roster = f.roster(
        people=(f.AMARA, f.MARCUS),
        duties=(
            f.THURSDAY_GAP,
            f.duty("dut_p1", f.local(2026, 8, 20, 9), hours=4.0, assigned_person_id=f.AMARA.id),
            f.duty("dut_p2", f.local(2026, 8, 27, 9), hours=4.0, assigned_person_id=f.AMARA.id),
        ),
    )
    order = rank(f.THURSDAY_GAP, roster, f.NOW)
    assert [c.person_id for c in order.candidates] == [f.MARCUS.id, f.AMARA.id]
    assert order.first.debt_hours > 0


def test_ranking_is_deterministic_across_repeated_runs():
    roster = f.roster(people=f.CAST, duties=(f.THURSDAY_GAP,))
    runs = [
        tuple((c.person_id, c.score) for c in rank(f.THURSDAY_GAP, roster, f.NOW).candidates)
        for _ in range(5)
    ]
    assert len(set(runs)) == 1


def test_ties_break_on_who_has_gone_longest_without_being_asked():
    a = f.person("per_aaa", "Aaa Volunteer", qualifications=("food-safety",))
    b = f.person("per_bbb", "Bbb Volunteer", qualifications=("food-safety",))
    roster = f.roster(
        people=(a, b),
        duties=(f.THURSDAY_GAP,),
        asks=(
            f.ask(
                "ask_a",
                "dut_other",
                a.id,
                sent_at=f.NOW - timedelta(days=1),
                state=AskState.ACCEPTED,
            ),
            f.ask(
                "ask_b",
                "dut_other",
                b.id,
                sent_at=f.NOW - timedelta(days=6),
                state=AskState.ACCEPTED,
            ),
        ),
    )
    order = rank(f.THURSDAY_GAP, roster, f.NOW)
    assert [c.person_id for c in order.candidates] == [b.id, a.id]


def test_ineligible_people_appear_in_excluded_with_a_reason():
    roster = f.roster(people=f.CAST, duties=(f.THURSDAY_GAP,))
    order = rank(f.THURSDAY_GAP, roster, f.NOW)
    excluded = {e.person_id: e.explanation for e in order.excluded}
    assert f.SOFIA.id in excluded
    assert "not trained" in excluded[f.SOFIA.id]
    assert f.BEN.id in excluded
    assert "opted in" in excluded[f.BEN.id]


def test_drafting_widens_the_pool_to_people_who_never_opted_in():
    roster = f.roster(people=(f.BEN,), duties=(f.THURSDAY_GAP,))
    sending = rank(f.THURSDAY_GAP, roster, f.NOW, for_action=ActionClass.SEND_ASK)
    drafting = rank(f.THURSDAY_GAP, roster, f.NOW, for_action=ActionClass.DRAFT_ASK)
    assert not sending.has_candidates
    assert [c.person_id for c in drafting.candidates] == [f.BEN.id]


def test_a_never_asked_volunteer_is_not_penalised_for_having_no_history():
    """New volunteers drift away because nothing is ever expected of them. An agent
    that scores 'no history' as 'bad history' makes that worse."""
    newcomer = f.person("per_new", "New Volunteer", qualifications=("food-safety",))
    order = rank(f.THURSDAY_GAP, f.roster(people=(newcomer,), duties=(f.THURSDAY_GAP,)), f.NOW)
    assert order.first.components.responsiveness == 0.5
    assert order.first.components.rest == 1.0


def test_rationale_names_the_qualification_and_the_load():
    roster = f.roster(
        people=(f.AMARA, f.MARCUS),
        duties=(
            f.THURSDAY_GAP,
            f.duty("dut_p1", f.local(2026, 8, 20, 9), hours=6.0, assigned_person_id=f.AMARA.id),
        ),
    )
    order = rank(f.THURSDAY_GAP, roster, f.NOW)
    assert order.first.person_id == f.MARCUS.id
    assert "trained for food-safety" in order.first.rationale
    assert order.first.rationale.startswith("Marcus Tran ")
    assert order.first.rationale.endswith(".")


def test_notice_score_reflects_when_a_person_can_actually_be_reached():
    imminent = f.duty("dut_soon", f.NOW + timedelta(hours=20), min_notice=timedelta(hours=2))
    distant = f.duty("dut_later", f.NOW + timedelta(days=10), min_notice=timedelta(hours=2))
    roster = f.roster(people=(f.MARCUS,), duties=(imminent, distant))
    soon = rank(imminent, roster, f.NOW).first
    later = rank(distant, roster, f.NOW).first
    assert later.components.notice > soon.components.notice


def test_limit_truncates_without_changing_the_order():
    roster = f.roster(people=f.CAST, duties=(f.THURSDAY_GAP,))
    full = rank(f.THURSDAY_GAP, roster, f.NOW)
    capped = rank(f.THURSDAY_GAP, roster, f.NOW, limit=2)
    assert [c.person_id for c in capped.candidates] == [c.person_id for c in full.candidates[:2]]


def test_an_empty_pool_reports_no_candidates_rather_than_guessing():
    roster = f.roster(people=(f.SOFIA,), duties=(f.THURSDAY_GAP,))
    order = rank(f.THURSDAY_GAP, roster, f.NOW)
    assert not order.has_candidates
    assert order.first is None
    assert len(order.excluded) == 1


def test_scores_stay_inside_the_unit_interval():
    roster = f.roster(people=f.CAST, duties=(f.THURSDAY_GAP,))
    for candidate in rank(f.THURSDAY_GAP, roster, f.NOW).candidates:
        assert 0.0 <= candidate.score <= 1.0
        for value in candidate.components.as_dict().values():
            assert 0.0 <= value <= 1.0


def test_familiarity_with_the_role_lifts_fit():
    veteran = f.person("per_vet", "Veteran", qualifications=("food-safety",))
    rookie = f.person("per_rook", "Rookie", qualifications=("food-safety",))
    history = tuple(
        f.duty(
            f"dut_h{i}",
            f.local(2026, 8, 10 + i, 9),
            hours=0.5,
            assigned_person_id=veteran.id,
            role="Distribution",
        )
        for i in range(3)
    )
    roster = f.roster(people=(veteran, rookie), duties=(f.THURSDAY_GAP, *history))
    by_id = {c.person_id: c for c in rank(f.THURSDAY_GAP, roster, f.NOW).candidates}
    assert by_id[veteran.id].components.fit > by_id[rookie.id].components.fit
