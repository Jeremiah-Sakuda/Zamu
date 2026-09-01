"""Time is an injected dependency, never a global.

Every decision Zamu makes is a function of "now". If "now" is ambient, decisions
are untestable and the demo is unreproducible. So the clock is a parameter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Source of the current instant, always timezone-aware UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock time. Used in production."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """A clock that does not move unless told to. Used in tests and the seeded demo."""

    def __init__(self, at: datetime) -> None:
        self._at = _require_aware(at)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = _require_aware(at)

    def advance(self, seconds: float) -> datetime:
        from datetime import timedelta

        self._at = self._at + timedelta(seconds=seconds)
        return self._at


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Zamu refuses naive datetimes; pass a timezone-aware value")
    return value.astimezone(timezone.utc)


def utc(value: datetime) -> datetime:
    """Normalise any aware datetime to UTC. Rejects naive input."""
    return _require_aware(value)
