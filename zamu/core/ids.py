"""Identifier and token generation.

Ids are prefixed so that a stray id in a log or a receipt is self-describing, and
seeded generation exists so the demo organisation is byte-identical on every run.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from datetime import datetime

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(prefix: str, length: int = 10) -> str:
    """A random, prefixed identifier. `dut_9f2ka0zx1b`."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{prefix}_{body}"


def seeded_id(prefix: str, *parts: str, length: int = 10) -> str:
    """A deterministic identifier derived from stable parts.

    Used for seeded demo data and for idempotency keys, where the same logical thing
    must always produce the same id.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    body = "".join(_ALPHABET[int(digest[i : i + 2], 16) % len(_ALPHABET)] for i in range(0, length * 2, 2))
    return f"{prefix}_{body}"


def new_token(length: int = 32) -> str:
    """A single-use secret for one-tap accept and decline links."""
    return secrets.token_urlsafe(length)


def idempotency_key(action: str, *parts: str) -> str:
    """A stable key for one logical mutation.

    Two attempts to do the same thing must collide here, so a retried tool call
    cannot double-send an ask or double-write a roster row.
    """
    return seeded_id(f"idem_{action}", *parts, length=16)


def ask_idempotency_key(duty_id: str, person_id: str, attempt_window_start: datetime) -> str:
    """Idempotency for a single ask: this duty, this person, this attempt window."""
    return idempotency_key("ask", duty_id, person_id, attempt_window_start.isoformat())


def assignment_idempotency_key(duty_id: str, person_id: str, ask_id: str) -> str:
    """Idempotency for one roster write, anchored to the acceptance that justified it."""
    return idempotency_key("assign", duty_id, person_id, ask_id)
