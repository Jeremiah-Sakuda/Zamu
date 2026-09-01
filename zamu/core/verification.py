"""Checking that what Zamu meant to happen is what actually happened.

A success response is a claim, not a fact. The write may have been applied to a stale
row, superseded by a coordinator editing the sheet at the same moment, or silently
dropped by a provider that returned 200 anyway. So every mutation ends by reading the
target back and comparing observed state against intended state, field by field.

This is the cheapest credibility available to an agent and almost nobody spends it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zamu.core.models import ActionResult


@dataclass(frozen=True, slots=True)
class Verification:
    """The outcome of comparing intent to observation."""

    result: ActionResult
    detail: str
    mismatches: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.result is ActionResult.VERIFIED


def verify(intended: dict[str, Any], observed: dict[str, Any] | None) -> Verification:
    """Compare an intended write against what re-reading the target actually returned.

    Three distinguishable outcomes, because they demand different responses:
      * nothing came back at all — the write FAILED and should be retried;
      * something came back but it disagrees — CONFLICTED, and a human should look;
      * everything matches — VERIFIED, and the receipt can be closed.
    """
    if observed is None:
        return Verification(
            ActionResult.FAILED,
            "Re-reading the roster returned nothing, so the change did not land.",
        )

    mismatches = [
        f"{key}: intended {intended[key]!r}, found {observed.get(key)!r}"
        for key in sorted(intended)
        if observed.get(key) != intended[key]
    ]

    if mismatches:
        return Verification(
            ActionResult.CONFLICTED,
            "The roster changed, but not to what Zamu intended: " + "; ".join(mismatches),
            tuple(mismatches),
        )

    return Verification(
        ActionResult.VERIFIED,
        "Re-read the roster after writing and confirmed the change.",
    )
