"""Where the roster lives.

One protocol, several backings. The in-memory store here is the reference
implementation and the one the tests run against; SQLite and DynamoDB in
`zamu.infra` implement the same surface so that swapping storage never changes a
decision Zamu makes.

The store is deliberately dumb. It holds rows and hands them back. Every rule about
what may be written lives above it, in authority.py, so that a new backing cannot
accidentally acquire an opinion.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Protocol, runtime_checkable

from zamu.core.errors import NotFound
from zamu.core.models import (
    ActionRecord,
    Ask,
    Duty,
    Grant,
    Org,
    Person,
    Roster,
)


@runtime_checkable
class Store(Protocol):
    """Everything Zamu needs to persist. Implementations must be independently swappable."""

    # -- organisations ---------------------------------------------------------------
    def put_org(self, org: Org) -> Org: ...
    def get_org(self, org_id: str) -> Org | None: ...
    def list_orgs(self) -> tuple[Org, ...]: ...

    # -- people ----------------------------------------------------------------------
    def put_person(self, person: Person) -> Person: ...
    def get_person(self, org_id: str, person_id: str) -> Person | None: ...
    def list_people(self, org_id: str) -> tuple[Person, ...]: ...

    # -- duties ----------------------------------------------------------------------
    def put_duty(self, duty: Duty) -> Duty: ...
    def get_duty(self, org_id: str, duty_id: str) -> Duty | None: ...
    def list_duties(self, org_id: str) -> tuple[Duty, ...]: ...

    # -- asks ------------------------------------------------------------------------
    def put_ask(self, ask: Ask) -> Ask: ...
    def get_ask(self, org_id: str, ask_id: str) -> Ask | None: ...
    def get_ask_by_token(self, token: str) -> Ask | None: ...
    def list_asks(self, org_id: str) -> tuple[Ask, ...]: ...

    # -- grants ----------------------------------------------------------------------
    def put_grant(self, grant: Grant) -> Grant: ...
    def get_grant(self, org_id: str, grant_id: str) -> Grant | None: ...
    def list_grants(self, org_id: str) -> tuple[Grant, ...]: ...

    # -- ledger ----------------------------------------------------------------------
    def append_action(self, record: ActionRecord) -> ActionRecord: ...
    def update_action(self, record: ActionRecord) -> ActionRecord: ...
    def get_action(self, org_id: str, action_id: str) -> ActionRecord | None: ...
    def find_action_by_key(self, org_id: str, idempotency_key: str) -> ActionRecord | None: ...
    def list_actions(self, org_id: str, limit: int | None = None) -> tuple[ActionRecord, ...]: ...

    # -- composition -----------------------------------------------------------------
    def load_roster(self, org_id: str) -> Roster: ...


class InMemoryStore:
    """Reference implementation. Fast, deterministic, and used by every unit test.

    Entities are deep-copied on the way in and out. Zamu's models are frozen, but the
    dicts inside `ActionRecord` are not, and a store that hands out live references
    would let a caller silently rewrite a receipt after the fact.
    """

    def __init__(self) -> None:
        self._orgs: dict[str, Org] = {}
        self._people: dict[tuple[str, str], Person] = {}
        self._duties: dict[tuple[str, str], Duty] = {}
        self._asks: dict[tuple[str, str], Ask] = {}
        self._tokens: dict[str, tuple[str, str]] = {}
        self._grants: dict[tuple[str, str], Grant] = {}
        self._actions: dict[tuple[str, str], ActionRecord] = {}
        self._action_order: list[tuple[str, str]] = []
        self._keys: dict[tuple[str, str], str] = {}

    # -- organisations ---------------------------------------------------------------
    def put_org(self, org: Org) -> Org:
        self._orgs[org.id] = org
        return org

    def get_org(self, org_id: str) -> Org | None:
        return self._orgs.get(org_id)

    def list_orgs(self) -> tuple[Org, ...]:
        return tuple(sorted(self._orgs.values(), key=lambda o: o.id))

    # -- people ----------------------------------------------------------------------
    def put_person(self, person: Person) -> Person:
        self._people[(person.org_id, person.id)] = person
        return person

    def get_person(self, org_id: str, person_id: str) -> Person | None:
        return self._people.get((org_id, person_id))

    def list_people(self, org_id: str) -> tuple[Person, ...]:
        return tuple(
            sorted((p for (o, _), p in self._people.items() if o == org_id), key=lambda p: p.id)
        )

    # -- duties ----------------------------------------------------------------------
    def put_duty(self, duty: Duty) -> Duty:
        self._duties[(duty.org_id, duty.id)] = duty
        return duty

    def get_duty(self, org_id: str, duty_id: str) -> Duty | None:
        return self._duties.get((org_id, duty_id))

    def list_duties(self, org_id: str) -> tuple[Duty, ...]:
        return tuple(
            sorted(
                (d for (o, _), d in self._duties.items() if o == org_id),
                key=lambda d: (d.window.start, d.id),
            )
        )

    # -- asks ------------------------------------------------------------------------
    def put_ask(self, ask: Ask) -> Ask:
        self._asks[(ask.org_id, ask.id)] = ask
        if ask.token:
            self._tokens[ask.token] = (ask.org_id, ask.id)
        return ask

    def get_ask(self, org_id: str, ask_id: str) -> Ask | None:
        return self._asks.get((org_id, ask_id))

    def get_ask_by_token(self, token: str) -> Ask | None:
        key = self._tokens.get(token)
        return self._asks.get(key) if key else None

    def list_asks(self, org_id: str) -> tuple[Ask, ...]:
        return tuple(
            sorted(
                (a for (o, _), a in self._asks.items() if o == org_id),
                key=lambda a: (a.sent_at, a.id),
            )
        )

    # -- grants ----------------------------------------------------------------------
    def put_grant(self, grant: Grant) -> Grant:
        self._grants[(grant.org_id, grant.id)] = grant
        return grant

    def get_grant(self, org_id: str, grant_id: str) -> Grant | None:
        return self._grants.get((org_id, grant_id))

    def list_grants(self, org_id: str) -> tuple[Grant, ...]:
        return tuple(
            sorted((g for (o, _), g in self._grants.items() if o == org_id), key=lambda g: g.id)
        )

    # -- ledger ----------------------------------------------------------------------
    def append_action(self, record: ActionRecord) -> ActionRecord:
        stored = _clone_action(record)
        key = (stored.org_id, stored.id)
        self._actions[key] = stored
        self._action_order.append(key)
        self._keys[(stored.org_id, stored.idempotency_key)] = stored.id
        return stored

    def update_action(self, record: ActionRecord) -> ActionRecord:
        key = (record.org_id, record.id)
        if key not in self._actions:
            raise NotFound(f"no action {record.id} in {record.org_id}")
        stored = _clone_action(record)
        self._actions[key] = stored
        return stored

    def get_action(self, org_id: str, action_id: str) -> ActionRecord | None:
        return self._actions.get((org_id, action_id))

    def find_action_by_key(self, org_id: str, idempotency_key: str) -> ActionRecord | None:
        action_id = self._keys.get((org_id, idempotency_key))
        return self._actions.get((org_id, action_id)) if action_id else None

    def list_actions(self, org_id: str, limit: int | None = None) -> tuple[ActionRecord, ...]:
        rows = [self._actions[k] for k in self._action_order if k[0] == org_id]
        rows.reverse()
        return tuple(rows[:limit] if limit is not None else rows)

    # -- composition -----------------------------------------------------------------
    def load_roster(self, org_id: str) -> Roster:
        org = self.get_org(org_id)
        if org is None:
            raise NotFound(f"no organization {org_id}")
        return Roster(
            org=org,
            people=self.list_people(org_id),
            duties=self.list_duties(org_id),
            asks=self.list_asks(org_id),
            grants=self.list_grants(org_id),
        )


def _clone_action(record: ActionRecord) -> ActionRecord:
    """Copy the mutable payloads so a stored receipt cannot be edited from outside."""
    from dataclasses import replace

    return replace(
        record,
        intended=deepcopy(record.intended),
        observed=deepcopy(record.observed) if record.observed is not None else None,
    )


def latest_action_at(store: Store, org_id: str) -> datetime | None:
    """Timestamp of the most recent ledger entry, for 'last active' displays."""
    rows = store.list_actions(org_id, limit=1)
    return rows[0].created_at if rows else None
