"""Errors that mean something specific. Nothing here is a generic Exception."""

from __future__ import annotations


class ZamuError(Exception):
    """Base for every error Zamu raises deliberately."""


class NotAuthorized(ZamuError):
    """A mutating action was attempted with no grant covering it.

    This is not a failure to be retried. It is the policy gate doing its job.
    """

    def __init__(self, action_class: str, reason: str) -> None:
        super().__init__(f"not authorized for {action_class}: {reason}")
        self.action_class = action_class
        self.reason = reason


class NotFound(ZamuError):
    """A referenced entity does not exist in the store."""


class Conflict(ZamuError):
    """The world changed underneath an intended write."""


class VerificationFailed(ZamuError):
    """A write reported success but re-reading the target did not confirm it."""
