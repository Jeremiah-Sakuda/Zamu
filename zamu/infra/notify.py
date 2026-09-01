"""Getting a message to a person.

Two implementations behind one protocol. `OutboxNotifier` keeps everything in memory
(and optionally on disk) and is what the local demo, the tests, and the judge-facing
sandbox use — it needs no provider, no verified domain, and no waiting on a sandbox
exit. `SesNotifier` sends real email through Amazon SES.

The split exists because email deliverability is the single most likely thing to
break a live demo, and a product whose core loop cannot be demonstrated without a
warmed sending domain is a product that cannot be demonstrated.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Message:
    """One outbound message to one person."""

    to_email: str
    to_name: str
    subject: str
    text: str
    html: str = ""
    kind: str = "ask"
    org_id: str = ""
    duty_id: str | None = None
    person_id: str | None = None


@dataclass(frozen=True, slots=True)
class Delivery:
    """What the provider said. Never treated as proof that anything landed."""

    ok: bool
    provider: str
    provider_id: str | None = None
    detail: str = ""


@runtime_checkable
class Notifier(Protocol):
    def send(self, message: Message) -> Delivery: ...


@dataclass
class OutboxNotifier:
    """Captures messages instead of sending them.

    This is not a stub. It is the delivery path for the demo organisation and for
    any coordinator who has not connected a provider yet: the coordinator can read
    exactly what Zamu would have said, and the one-tap links in it still work,
    because the links are ordinary URLs backed by real tokens.
    """

    directory: Path | None = None
    sent: list[tuple[Message, Delivery]] = field(default_factory=list)
    fail_next: str | None = None
    """Set to a reason to make the next send fail. Used to exercise the failure path."""

    provider_name: str = "outbox"

    def send(self, message: Message) -> Delivery:
        if self.fail_next is not None:
            reason, self.fail_next = self.fail_next, None
            delivery = Delivery(False, self.provider_name, None, reason)
            self.sent.append((message, delivery))
            return delivery

        delivery = Delivery(True, self.provider_name, f"outbox-{uuid.uuid4().hex[:12]}", "captured")
        self.sent.append((message, delivery))
        if self.directory is not None:
            self._write(message, delivery)
        return delivery

    def _write(self, message: Message, delivery: Delivery) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = self.directory / f"{stamp}-{delivery.provider_id}.json"
        path.write_text(
            json.dumps({"message": asdict(message), "delivery": asdict(delivery)}, indent=2),
            encoding="utf-8",
        )

    def messages_for(self, person_id: str) -> list[Message]:
        return [m for m, d in self.sent if m.person_id == person_id and d.ok]

    def clear(self) -> None:
        self.sent.clear()


class SesNotifier:
    """Real email via Amazon SES.

    Constructed lazily so that importing Zamu never requires boto3 or credentials;
    the local path must stay runnable on a laptop with no AWS account attached.
    """

    provider_name = "ses"

    def __init__(
        self,
        sender: str,
        *,
        region: str | None = None,
        configuration_set: str | None = None,
        client=None,
    ) -> None:
        self.sender = sender
        self.region = region
        self.configuration_set = configuration_set
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3  # imported here so the local path never needs it

            self._client = boto3.client("sesv2", region_name=self.region)
        return self._client

    def send(self, message: Message) -> Delivery:
        body: dict = {"Text": {"Data": message.text, "Charset": "UTF-8"}}
        if message.html:
            body["Html"] = {"Data": message.html, "Charset": "UTF-8"}

        request = {
            "FromEmailAddress": self.sender,
            "Destination": {"ToAddresses": [message.to_email]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                    "Body": body,
                }
            },
        }
        if self.configuration_set:
            request["ConfigurationSetName"] = self.configuration_set

        try:
            response = self.client.send_email(**request)
        except Exception as exc:  # provider errors are data, not crashes
            return Delivery(False, self.provider_name, None, f"{type(exc).__name__}: {exc}")

        return Delivery(True, self.provider_name, response.get("MessageId"), "accepted by SES")
