"""A durable Store backed by SQLite.

This is the default for local runs and for the judge-facing sandbox: a single file,
no server, no credentials, and identical behaviour to the in-memory reference the
tests pin down. DynamoDB implements the same protocol for the deployed path.

Rows are JSON documents with the few columns that need indexing lifted out, which
keeps the schema stable as the domain grows and keeps `serde` the single place that
knows how a duty is spelled.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

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
from zamu.infra import serde

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id TEXT PRIMARY KEY,
    doc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
    org_id TEXT NOT NULL,
    id TEXT NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (org_id, id)
);
CREATE TABLE IF NOT EXISTS duties (
    org_id TEXT NOT NULL,
    id TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (org_id, id)
);
CREATE INDEX IF NOT EXISTS duties_by_start ON duties (org_id, starts_at);
CREATE TABLE IF NOT EXISTS asks (
    org_id TEXT NOT NULL,
    id TEXT NOT NULL,
    token TEXT,
    sent_at TEXT NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (org_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS asks_by_token ON asks (token) WHERE token IS NOT NULL;
CREATE TABLE IF NOT EXISTS grants (
    org_id TEXT NOT NULL,
    id TEXT NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (org_id, id)
);
CREATE TABLE IF NOT EXISTS actions (
    org_id TEXT NOT NULL,
    id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (org_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS actions_by_key ON actions (org_id, idempotency_key);
CREATE INDEX IF NOT EXISTS actions_by_seq ON actions (org_id, seq);
"""


class SqliteStore:
    """Durable Store over a single SQLite file. Safe to share across threads."""

    def __init__(self, path: str | Path = "zamu.sqlite") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- helpers ---------------------------------------------------------------------

    def _write(self, sql: str, params: tuple) -> None:
        with self._conn:
            self._conn.execute(sql, params)

    def _rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    @staticmethod
    def _doc(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return json.loads(row["doc"]) if row is not None else None

    # -- organisations ---------------------------------------------------------------

    def put_org(self, org: Org) -> Org:
        self._write(
            "INSERT INTO orgs (id, doc) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc",
            (org.id, json.dumps(serde.dump_org(org))),
        )
        return org

    def get_org(self, org_id: str) -> Org | None:
        doc = self._doc(self._one("SELECT doc FROM orgs WHERE id = ?", (org_id,)))
        return serde.load_org(doc) if doc else None

    def list_orgs(self) -> tuple[Org, ...]:
        return tuple(
            serde.load_org(json.loads(r["doc"]))
            for r in self._rows("SELECT doc FROM orgs ORDER BY id")
        )

    # -- people ----------------------------------------------------------------------

    def put_person(self, person: Person) -> Person:
        self._write(
            "INSERT INTO people (org_id, id, doc) VALUES (?, ?, ?) "
            "ON CONFLICT(org_id, id) DO UPDATE SET doc=excluded.doc",
            (person.org_id, person.id, json.dumps(serde.dump_person(person))),
        )
        return person

    def get_person(self, org_id: str, person_id: str) -> Person | None:
        doc = self._doc(
            self._one("SELECT doc FROM people WHERE org_id = ? AND id = ?", (org_id, person_id))
        )
        return serde.load_person(doc) if doc else None

    def list_people(self, org_id: str) -> tuple[Person, ...]:
        return tuple(
            serde.load_person(json.loads(r["doc"]))
            for r in self._rows("SELECT doc FROM people WHERE org_id = ? ORDER BY id", (org_id,))
        )

    # -- duties ----------------------------------------------------------------------

    def put_duty(self, duty: Duty) -> Duty:
        self._write(
            "INSERT INTO duties (org_id, id, starts_at, doc) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(org_id, id) DO UPDATE SET doc=excluded.doc, starts_at=excluded.starts_at",
            (
                duty.org_id,
                duty.id,
                duty.window.start.isoformat(),
                json.dumps(serde.dump_duty(duty)),
            ),
        )
        return duty

    def get_duty(self, org_id: str, duty_id: str) -> Duty | None:
        doc = self._doc(
            self._one("SELECT doc FROM duties WHERE org_id = ? AND id = ?", (org_id, duty_id))
        )
        return serde.load_duty(doc) if doc else None

    def list_duties(self, org_id: str) -> tuple[Duty, ...]:
        return tuple(
            serde.load_duty(json.loads(r["doc"]))
            for r in self._rows(
                "SELECT doc FROM duties WHERE org_id = ? ORDER BY starts_at, id", (org_id,)
            )
        )

    # -- asks ------------------------------------------------------------------------

    def put_ask(self, ask: Ask) -> Ask:
        self._write(
            "INSERT INTO asks (org_id, id, token, sent_at, doc) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(org_id, id) DO UPDATE SET doc=excluded.doc, token=excluded.token",
            (
                ask.org_id,
                ask.id,
                ask.token or None,
                ask.sent_at.isoformat(),
                json.dumps(serde.dump_ask(ask)),
            ),
        )
        return ask

    def get_ask(self, org_id: str, ask_id: str) -> Ask | None:
        doc = self._doc(
            self._one("SELECT doc FROM asks WHERE org_id = ? AND id = ?", (org_id, ask_id))
        )
        return serde.load_ask(doc) if doc else None

    def get_ask_by_token(self, token: str) -> Ask | None:
        doc = self._doc(self._one("SELECT doc FROM asks WHERE token = ?", (token,)))
        return serde.load_ask(doc) if doc else None

    def list_asks(self, org_id: str) -> tuple[Ask, ...]:
        return tuple(
            serde.load_ask(json.loads(r["doc"]))
            for r in self._rows(
                "SELECT doc FROM asks WHERE org_id = ? ORDER BY sent_at, id", (org_id,)
            )
        )

    # -- grants ----------------------------------------------------------------------

    def put_grant(self, grant: Grant) -> Grant:
        self._write(
            "INSERT INTO grants (org_id, id, doc) VALUES (?, ?, ?) "
            "ON CONFLICT(org_id, id) DO UPDATE SET doc=excluded.doc",
            (grant.org_id, grant.id, json.dumps(serde.dump_grant(grant))),
        )
        return grant

    def get_grant(self, org_id: str, grant_id: str) -> Grant | None:
        doc = self._doc(
            self._one("SELECT doc FROM grants WHERE org_id = ? AND id = ?", (org_id, grant_id))
        )
        return serde.load_grant(doc) if doc else None

    def list_grants(self, org_id: str) -> tuple[Grant, ...]:
        return tuple(
            serde.load_grant(json.loads(r["doc"]))
            for r in self._rows("SELECT doc FROM grants WHERE org_id = ? ORDER BY id", (org_id,))
        )

    # -- ledger ----------------------------------------------------------------------

    def append_action(self, record: ActionRecord) -> ActionRecord:
        """Append-only by construction: the unique index on the idempotency key means a
        repeated attempt collides at the database rather than relying on a prior read."""
        row = self._one(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM actions WHERE org_id = ?",
            (record.org_id,),
        )
        self._write(
            "INSERT INTO actions (org_id, id, seq, idempotency_key, doc) VALUES (?, ?, ?, ?, ?)",
            (
                record.org_id,
                record.id,
                int(row["next"]),
                record.idempotency_key,
                json.dumps(serde.dump_action(record)),
            ),
        )
        return record

    def update_action(self, record: ActionRecord) -> ActionRecord:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE actions SET doc = ? WHERE org_id = ? AND id = ?",
                (json.dumps(serde.dump_action(record)), record.org_id, record.id),
            )
        if cursor.rowcount == 0:
            raise NotFound(f"no action {record.id} in {record.org_id}")
        return record

    def get_action(self, org_id: str, action_id: str) -> ActionRecord | None:
        doc = self._doc(
            self._one("SELECT doc FROM actions WHERE org_id = ? AND id = ?", (org_id, action_id))
        )
        return serde.load_action(doc) if doc else None

    def find_action_by_key(self, org_id: str, idempotency_key: str) -> ActionRecord | None:
        doc = self._doc(
            self._one(
                "SELECT doc FROM actions WHERE org_id = ? AND idempotency_key = ?",
                (org_id, idempotency_key),
            )
        )
        return serde.load_action(doc) if doc else None

    def list_actions(self, org_id: str, limit: int | None = None) -> tuple[ActionRecord, ...]:
        sql = "SELECT doc FROM actions WHERE org_id = ? ORDER BY seq DESC"
        params: tuple = (org_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (org_id, limit)
        return tuple(serde.load_action(json.loads(r["doc"])) for r in self._rows(sql, params))

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

    def latest_action_at(self, org_id: str) -> datetime | None:
        rows = self.list_actions(org_id, limit=1)
        return rows[0].created_at if rows else None
