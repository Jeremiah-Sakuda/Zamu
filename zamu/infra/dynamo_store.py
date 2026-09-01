"""A Store backed by a single DynamoDB table.

Single-table design, because the access patterns are small and fixed: read one
organization's whole roster, look up an ask by its one-tap token, and look up a ledger
entry by its idempotency key. Two global secondary indexes cover the last two.

Two properties are worth calling out, because they are the ones that would quietly
break if this were written casually.

Idempotency is enforced by the database, not by a prior read. Appending a ledger entry
writes a claim item under a conditional expression, so two processes racing on the
same idempotency key cannot both proceed — the loser gets a real error rather than a
duplicate ask landing in somebody's inbox.

Ledger ordering uses the creation timestamp in the sort key rather than a counter.
A counter would need a read-modify-write on every append, which is the classic
DynamoDB hot-partition mistake and, worse, is not atomic without a transaction.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from zamu.core.errors import Conflict, NotFound
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

ORG = "ORG"
PERSON = "PERSON"
DUTY = "DUTY"
ASK = "ASK"
GRANT = "GRANT"
ACTION = "ACTION"
IDEM = "IDEM"

TOKEN_INDEX = "token-index"
IDEMPOTENCY_INDEX = "idempotency-index"


def _pk(org_id: str) -> str:
    return f"ORG#{org_id}"


def _clean(value: Any) -> Any:
    """DynamoDB has no float type and rejects empty strings in key positions.

    Floats become Decimals on the way in and come back as Decimals, so the reverse
    conversion happens on read. Doing this in one place keeps every caller from having
    to remember that a duty's hours came back as a Decimal.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


def _restore(value: Any) -> Any:
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if as_int == value else float(value)
    if isinstance(value, dict):
        return {k: _restore(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore(v) for v in value]
    return value


class DynamoStore:
    """Durable Store over one DynamoDB table."""

    def __init__(self, table_name: str, *, region: str | None = None, table=None) -> None:
        self.table_name = table_name
        self.region = region
        self._table = table

    @property
    def table(self):
        if self._table is None:
            import boto3

            self._table = boto3.resource("dynamodb", region_name=self.region).Table(
                self.table_name
            )
        return self._table

    # -- schema ------------------------------------------------------------------------

    @staticmethod
    def table_definition(table_name: str) -> dict[str, Any]:
        """The CreateTable arguments, kept next to the code that depends on them."""
        return {
            "TableName": table_name,
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "token", "AttributeType": "S"},
                {"AttributeName": "idem", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": TOKEN_INDEX,
                    "KeySchema": [{"AttributeName": "token", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": IDEMPOTENCY_INDEX,
                    "KeySchema": [{"AttributeName": "idem", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        }

    def create_table(self) -> None:
        """Create the table if it does not exist. Safe to call on every cold start."""
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client("dynamodb", region_name=self.region)
        try:
            client.create_table(**self.table_definition(self.table_name))
            client.get_waiter("table_exists").wait(TableName=self.table_name)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceInUseException":
                raise

    # -- primitives --------------------------------------------------------------------

    def _put(self, org_id: str, sort_key: str, doc: dict[str, Any], **extra: Any) -> None:
        item = {"pk": _pk(org_id), "sk": sort_key, "doc": _clean(doc)}
        item.update({k: v for k, v in extra.items() if v})
        self.table.put_item(Item=item)

    def _get(self, org_id: str, sort_key: str) -> dict[str, Any] | None:
        response = self.table.get_item(Key={"pk": _pk(org_id), "sk": sort_key})
        item = response.get("Item")
        return _restore(item["doc"]) if item else None

    def _query_prefix(self, org_id: str, prefix: str, *, forward: bool = True, limit=None):
        from boto3.dynamodb.conditions import Key

        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(_pk(org_id))
            & Key("sk").begins_with(f"{prefix}#"),
            "ScanIndexForward": forward,
        }
        if limit is not None:
            kwargs["Limit"] = limit

        items: list[dict[str, Any]] = []
        while True:
            response = self.table.query(**kwargs)
            items.extend(response.get("Items", []))
            token = response.get("LastEvaluatedKey")
            if not token or (limit is not None and len(items) >= limit):
                break
            kwargs["ExclusiveStartKey"] = token
        if limit is not None:
            items = items[:limit]
        return [_restore(item["doc"]) for item in items]

    # -- organisations -----------------------------------------------------------------

    def put_org(self, org: Org) -> Org:
        self._put(org.id, ORG, serde.dump_org(org))
        return org

    def get_org(self, org_id: str) -> Org | None:
        doc = self._get(org_id, ORG)
        return serde.load_org(doc) if doc else None

    def list_orgs(self) -> tuple[Org, ...]:
        """A scan, deliberately. Listing every organisation is an admin operation that
        happens on a health check, not on the hot path."""
        from boto3.dynamodb.conditions import Attr

        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"FilterExpression": Attr("sk").eq(ORG)}
        while True:
            response = self.table.scan(**kwargs)
            items.extend(response.get("Items", []))
            token = response.get("LastEvaluatedKey")
            if not token:
                break
            kwargs["ExclusiveStartKey"] = token
        orgs = [serde.load_org(_restore(item["doc"])) for item in items]
        return tuple(sorted(orgs, key=lambda o: o.id))

    # -- people ------------------------------------------------------------------------

    def put_person(self, person: Person) -> Person:
        self._put(person.org_id, f"{PERSON}#{person.id}", serde.dump_person(person))
        return person

    def get_person(self, org_id: str, person_id: str) -> Person | None:
        doc = self._get(org_id, f"{PERSON}#{person_id}")
        return serde.load_person(doc) if doc else None

    def list_people(self, org_id: str) -> tuple[Person, ...]:
        return tuple(serde.load_person(d) for d in self._query_prefix(org_id, PERSON))

    # -- duties ------------------------------------------------------------------------

    def put_duty(self, duty: Duty) -> Duty:
        self._put(duty.org_id, f"{DUTY}#{duty.id}", serde.dump_duty(duty))
        return duty

    def get_duty(self, org_id: str, duty_id: str) -> Duty | None:
        doc = self._get(org_id, f"{DUTY}#{duty_id}")
        return serde.load_duty(doc) if doc else None

    def list_duties(self, org_id: str) -> tuple[Duty, ...]:
        duties = [serde.load_duty(d) for d in self._query_prefix(org_id, DUTY)]
        return tuple(sorted(duties, key=lambda d: (d.window.start, d.id)))

    # -- asks --------------------------------------------------------------------------

    def put_ask(self, ask: Ask) -> Ask:
        self._put(ask.org_id, f"{ASK}#{ask.id}", serde.dump_ask(ask), token=ask.token)
        return ask

    def get_ask(self, org_id: str, ask_id: str) -> Ask | None:
        doc = self._get(org_id, f"{ASK}#{ask_id}")
        return serde.load_ask(doc) if doc else None

    def get_ask_by_token(self, token: str) -> Ask | None:
        from boto3.dynamodb.conditions import Key

        if not token:
            return None
        response = self.table.query(
            IndexName=TOKEN_INDEX, KeyConditionExpression=Key("token").eq(token)
        )
        items = response.get("Items", [])
        return serde.load_ask(_restore(items[0]["doc"])) if items else None

    def list_asks(self, org_id: str) -> tuple[Ask, ...]:
        asks = [serde.load_ask(d) for d in self._query_prefix(org_id, ASK)]
        return tuple(sorted(asks, key=lambda a: (a.sent_at, a.id)))

    # -- grants ------------------------------------------------------------------------

    def put_grant(self, grant: Grant) -> Grant:
        self._put(grant.org_id, f"{GRANT}#{grant.id}", serde.dump_grant(grant))
        return grant

    def get_grant(self, org_id: str, grant_id: str) -> Grant | None:
        doc = self._get(org_id, f"{GRANT}#{grant_id}")
        return serde.load_grant(doc) if doc else None

    def list_grants(self, org_id: str) -> tuple[Grant, ...]:
        return tuple(serde.load_grant(d) for d in self._query_prefix(org_id, GRANT))

    # -- ledger ------------------------------------------------------------------------

    @staticmethod
    def _action_sk(record: ActionRecord) -> str:
        """Timestamp first so the range key sorts chronologically, id last so it is
        unique when two entries land in the same microsecond."""
        return f"{ACTION}#{record.created_at.isoformat()}#{record.id}"

    def append_action(self, record: ActionRecord) -> ActionRecord:
        """Claim the idempotency key first, then write the entry.

        The conditional put is what makes this genuinely idempotent under concurrency.
        Without it, two workers handling the same duplicated webhook would both read
        'no existing entry' and both send an ask.
        """
        from botocore.exceptions import ClientError

        sort_key = self._action_sk(record)
        try:
            self.table.put_item(
                Item={
                    "pk": _pk(record.org_id),
                    "sk": f"{IDEM}#{record.idempotency_key}",
                    "idem": f"{record.org_id}#{record.idempotency_key}",
                    "action_sk": sort_key,
                },
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise Conflict(
                    f"idempotency key {record.idempotency_key} already used in {record.org_id}"
                ) from exc
            raise

        self._put(record.org_id, sort_key, serde.dump_action(record))
        return record

    def update_action(self, record: ActionRecord) -> ActionRecord:
        sort_key = self._action_sk(record)
        if self._get(record.org_id, sort_key) is None:
            raise NotFound(f"no action {record.id} in {record.org_id}")
        self._put(record.org_id, sort_key, serde.dump_action(record))
        return record

    def get_action(self, org_id: str, action_id: str) -> ActionRecord | None:
        for doc in self._query_prefix(org_id, ACTION, forward=False):
            if doc.get("id") == action_id:
                return serde.load_action(doc)
        return None

    def find_action_by_key(self, org_id: str, idempotency_key: str) -> ActionRecord | None:
        response = self.table.get_item(
            Key={"pk": _pk(org_id), "sk": f"{IDEM}#{idempotency_key}"}
        )
        claim = response.get("Item")
        if not claim:
            return None
        doc = self._get(org_id, claim["action_sk"])
        return serde.load_action(doc) if doc else None

    def list_actions(self, org_id: str, limit: int | None = None) -> tuple[ActionRecord, ...]:
        docs = self._query_prefix(org_id, ACTION, forward=False, limit=limit)
        return tuple(serde.load_action(d) for d in docs)

    # -- composition -------------------------------------------------------------------

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

    def delete_org(self, org_id: str) -> None:
        """Remove one organization and everything under its partition."""
        from boto3.dynamodb.conditions import Key

        kwargs: dict[str, Any] = {"KeyConditionExpression": Key("pk").eq(_pk(org_id))}
        with self.table.batch_writer() as batch:
            while True:
                response = self.table.query(**kwargs)
                for item in response.get("Items", []):
                    batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
                token = response.get("LastEvaluatedKey")
                if not token:
                    break
                kwargs["ExclusiveStartKey"] = token
