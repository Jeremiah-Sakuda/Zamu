"""One place that decides how Zamu is wired.

Zamu has to run in three shapes without any of them being a special case: on a laptop
with no AWS account at all, on a small server with SQLite and real email, and on AWS
with DynamoDB, SES and Bedrock. Every one of those is the same code with different
adapters, chosen here from the environment.

The defaults are deliberately the local ones. A system whose default configuration
requires credentials cannot be run by somebody evaluating it, and a demo that cannot
be run is a demo that will not be believed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from zamu.core.clock import Clock, SystemClock
from zamu.core.fill import CoverageService
from zamu.core.store import InMemoryStore, Store

StoreKind = Literal["sqlite", "dynamodb", "memory"]
NotifierKind = Literal["outbox", "ses"]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str) -> bool:
    return _env(name).lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything Zamu needs to know about where it is running."""

    store_kind: StoreKind = "sqlite"
    db_path: str = ".zamu/zamu.sqlite"
    dynamo_table: str = "zamu"
    region: str = "us-east-1"

    notifier_kind: NotifierKind = "outbox"
    ses_sender: str = ""
    ses_configuration_set: str = ""
    outbox_dir: str = ""

    base_url: str = "http://localhost:8000"
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    model_id: str = ""
    force_planner: bool = False

    org_id: str = "org_demo_riverside"
    seed_demo: bool = True
    sweep_horizon_days: int = 21

    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def uses_aws(self) -> bool:
        return self.store_kind == "dynamodb" or self.notifier_kind == "ses"

    def describe(self) -> dict[str, Any]:
        """A safe summary for the health endpoint. Carries no secrets."""
        return {
            "store": self.store_kind,
            "notifier": self.notifier_kind,
            "region": self.region if self.uses_aws else None,
            "base_url": self.base_url,
            "planner_forced": self.force_planner,
        }


def load_settings() -> Settings:
    """Read the environment once, at the edge, and pass the result down."""
    store_kind: StoreKind = _env("ZAMU_STORE", "sqlite").lower() or "sqlite"  # type: ignore[assignment]
    if store_kind not in ("sqlite", "dynamodb", "memory"):
        store_kind = "sqlite"  # type: ignore[assignment]

    notifier_kind: NotifierKind = "ses" if _env("ZAMU_SES_SENDER") else "outbox"

    origins = tuple(
        o.strip()
        for o in _env("ZAMU_CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    )

    return Settings(
        store_kind=store_kind,
        db_path=_env("ZAMU_DB", ".zamu/zamu.sqlite"),
        dynamo_table=_env("ZAMU_DYNAMO_TABLE", "zamu"),
        region=_env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or "us-east-1",
        notifier_kind=notifier_kind,
        ses_sender=_env("ZAMU_SES_SENDER"),
        ses_configuration_set=_env("ZAMU_SES_CONFIGURATION_SET"),
        outbox_dir=_env("ZAMU_OUTBOX_DIR"),
        base_url=_env("ZAMU_BASE_URL", "http://localhost:8000"),
        cors_origins=origins or ("http://localhost:3000",),
        model_id=_env("ZAMU_MODEL_ID"),
        force_planner=_flag("ZAMU_FORCE_PLANNER"),
        org_id=_env("ZAMU_ORG_ID", "org_demo_riverside"),
        seed_demo=not _flag("ZAMU_NO_SEED"),
        sweep_horizon_days=int(_env("ZAMU_SWEEP_HORIZON_DAYS", "21") or 21),
    )


def build_store(settings: Settings) -> Store:
    """Pick a backing. All three implement the same protocol and are tested against
    each other, so this choice never changes a decision Zamu makes."""
    if settings.store_kind == "memory":
        return InMemoryStore()

    if settings.store_kind == "dynamodb":
        from zamu.infra.dynamo_store import DynamoStore

        store = DynamoStore(settings.dynamo_table, region=settings.region)
        store.create_table()
        return store

    from zamu.infra.sqlite_store import SqliteStore

    return SqliteStore(settings.db_path)


def build_notifier(settings: Settings):
    """Pick a delivery path.

    Falls back to the outbox rather than failing when SES is not configured, because
    a coordinator part-way through setup should still see exactly what Zamu would have
    sent rather than a stack trace.
    """
    from zamu.infra.notify import OutboxNotifier, SesNotifier

    if settings.notifier_kind == "ses" and settings.ses_sender:
        return SesNotifier(
            settings.ses_sender,
            region=settings.region,
            configuration_set=settings.ses_configuration_set or None,
        )

    directory = (
        Path(settings.outbox_dir)
        if settings.outbox_dir
        else (Path(settings.db_path).parent / "outbox" if settings.store_kind == "sqlite" else None)
    )
    return OutboxNotifier(directory=directory)


def build_service(
    settings: Settings, *, store: Store | None = None, clock: Clock | None = None
) -> CoverageService:
    return CoverageService(
        store or build_store(settings),
        clock or SystemClock(),
        build_notifier(settings),
        base_url=settings.base_url,
    )


def ensure_seeded(store: Store, settings: Settings, clock: Clock | None = None) -> str | None:
    """Seed the demonstration organization if the store is empty.

    Only ever runs against an empty store, so a real deployment that has any data is
    never touched.
    """
    if not settings.seed_demo:
        return None
    if store.list_orgs():
        return None

    from zamu.demo import seed

    return seed(store, (clock or SystemClock()).now())
