"""The append-only record of everything Zamu did, tried, or was refused.

Two properties make this a receipt rather than a log.

First, entries are written *before* execution. If the process dies mid-flight, the
ledger still shows that an attempt was in progress and what it intended, so the next
run can reconcile instead of guessing.

Second, entries are closed only after verification, with the observed state stored
next to the intended state. An entry that says 'sent' and nothing else is a claim.
An entry that says 'intended X, re-read the roster, observed X' is evidence.

Blocked actions are recorded too. The times Zamu did not act are exactly the times a
coordinator most needs to know about.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from zamu.core.authority import Decision, ProposedAction
from zamu.core.clock import Clock, utc
from zamu.core.ids import new_id
from zamu.core.models import ActionRecord, ActionResult
from zamu.core.store import Store
from zamu.core.verification import Verification, verify


@dataclass(frozen=True, slots=True)
class Entry:
    """A ledger entry plus whether it was replayed from a previous identical attempt."""

    record: ActionRecord
    replayed: bool

    @property
    def id(self) -> str:
        return self.record.id


class Ledger:
    """Write-ahead receipts over any Store implementation."""

    def __init__(self, store: Store, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    # -- opening ---------------------------------------------------------------------

    def begin(
        self,
        action: ProposedAction,
        decision: Decision,
        intended: dict[str, Any],
        idempotency_key: str,
    ) -> Entry:
        """Open a receipt before doing anything.

        If a receipt already exists for this idempotency key, the caller is repeating
        itself — a retried tool call, a duplicated webhook, a resumed session. The
        existing entry is returned untouched and the caller must not execute again.
        """
        existing = self._store.find_action_by_key(action.org_id, idempotency_key)
        if existing is not None:
            return Entry(existing, replayed=True)

        record = ActionRecord(
            id=new_id("act"),
            org_id=action.org_id,
            idempotency_key=idempotency_key,
            action_class=action.action_class,
            summary=action.summary,
            intended=dict(intended),
            policy_rule=decision.rule,
            created_at=self._clock.now(),
            duty_id=action.duty_id,
            person_id=action.person_id,
        )
        return Entry(self._store.append_action(record), replayed=False)

    def record_blocked(
        self, action: ProposedAction, decision: Decision, idempotency_key: str
    ) -> ActionRecord:
        """Record a refusal by the policy gate. No execution is attempted.

        These entries are the raw material of the 'what Zamu was not allowed to do'
        section of the handover brief.
        """
        existing = self._store.find_action_by_key(action.org_id, idempotency_key)
        if existing is not None:
            return existing

        now = self._clock.now()
        record = ActionRecord(
            id=new_id("act"),
            org_id=action.org_id,
            idempotency_key=idempotency_key,
            action_class=action.action_class,
            summary=action.summary,
            intended=dict(action.payload),
            policy_rule=decision.rule,
            created_at=now,
            executed_at=None,
            verified_at=now,
            observed=None,
            result=ActionResult.BLOCKED,
            detail=decision.reason,
            duty_id=action.duty_id,
            person_id=action.person_id,
        )
        return self._store.append_action(record)

    # -- progressing -----------------------------------------------------------------

    def mark_executed(self, record: ActionRecord) -> ActionRecord:
        """Stamp the moment the side effect was actually attempted."""
        return self._store.update_action(replace(record, executed_at=self._clock.now()))

    def close(
        self,
        record: ActionRecord,
        observed: dict[str, Any] | None,
        *,
        result: ActionResult | None = None,
        detail: str | None = None,
        target: str = "the roster",
    ) -> ActionRecord:
        """Close a receipt by comparing observation to intent.

        Passing `result` explicitly is for outcomes verification cannot see, such as a
        delivery provider raising. Otherwise the comparison decides.
        """
        if result is None:
            outcome: Verification = verify(record.intended, observed, target=target)
            result = outcome.result
            detail = detail or outcome.detail

        return self._store.update_action(
            replace(
                record,
                observed=dict(observed) if observed is not None else None,
                result=result,
                detail=detail or "",
                verified_at=self._clock.now(),
                executed_at=record.executed_at or self._clock.now(),
            )
        )

    def fail(self, record: ActionRecord, detail: str) -> ActionRecord:
        """Close a receipt as failed, with the reason the attempt did not complete."""
        return self.close(record, None, result=ActionResult.FAILED, detail=detail)

    # -- reading ---------------------------------------------------------------------

    def recent(self, org_id: str, limit: int = 50) -> tuple[ActionRecord, ...]:
        return self._store.list_actions(org_id, limit=limit)

    def since(self, org_id: str, moment: datetime) -> tuple[ActionRecord, ...]:
        cutoff = utc(moment)
        return tuple(r for r in self._store.list_actions(org_id) if utc(r.created_at) >= cutoff)

    def blocked_since(self, org_id: str, moment: datetime) -> tuple[ActionRecord, ...]:
        return tuple(r for r in self.since(org_id, moment) if r.result is ActionResult.BLOCKED)

    def open_entries(self, org_id: str) -> tuple[ActionRecord, ...]:
        """Receipts that were opened and never closed. Each one is a reconciliation job."""
        return tuple(r for r in self._store.list_actions(org_id) if not r.is_closed)
