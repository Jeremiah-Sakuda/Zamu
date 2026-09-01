"""The authority gate, wired into the agent's tool-invocation path.

This is the most load-bearing use of the Strands SDK in Zamu, and the reason the
safety property does not depend on the model behaving. `BeforeToolCallEvent` exposes
`cancel_tool`; setting it stops the call before the tool body runs and hands the model
back an error result instead. So a mutating tool with no grant behind it is not
discouraged by a prompt — it is unreachable.

Two layers enforce the same rule on purpose. The hook checks the class-level grant
before the call happens; `CoverageService` re-checks the specific person and duty
inside the call. Either one alone would be enough on a good day. Together they mean a
mistake in one is not a breach.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from strands.hooks import BeforeToolCallEvent, HookRegistry

from zamu.agent.tools import TOOL_AUTHORITY, ToolAuthority
from zamu.core.authority import ProposedAction, authorize, granted_levels
from zamu.core.clock import Clock
from zamu.core.ids import idempotency_key
from zamu.core.ledger import Ledger
from zamu.core.models import FORBIDDEN_ACTION_CLASSES, ActionClass
from zamu.core.store import Store

#: The authority assumed for a tool nobody declared: the rung that is never granted.
_UNKNOWN = ToolAuthority(ActionClass.REASSIGN_WITHOUT_CONSENT)


@dataclass
class Refusal:
    """One blocked call, kept so tests and the console can see what the gate stopped."""

    tool: str
    rule: str
    reason: str


@dataclass
class AuthorityHook:
    """Blocks tool calls the organisation has not granted."""

    store: Store
    clock: Clock
    org_id: str
    refusals: list[Refusal] = field(default_factory=list)
    record_to_ledger: bool = True

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)

    # -- the gate ----------------------------------------------------------------------

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        name = tool_use.get("name", "")
        authority = TOOL_AUTHORITY.get(name)

        if authority is None:
            # A tool nobody classified is a tool nobody reasoned about.
            self._refuse(
                event,
                name,
                "R14-unclassified-tool",
                f"Zamu will not run '{name}' because no authority level is declared for it.",
                tool_use,
            )
            return

        if authority.required in FORBIDDEN_ACTION_CLASSES:
            self._refuse(
                event,
                name,
                "R0-never-implemented",
                f"'{name}' is in an action class Zamu never performs.",
                tool_use,
            )
            return

        if authority.required is ActionClass.READ:
            return

        roster = self.store.load_roster(self.org_id)
        now = self.clock.now()
        held = granted_levels(roster, now)

        if any(level in held for level in authority.acceptable):
            return

        payload = tool_use.get("input") or {}
        action = ProposedAction(
            org_id=self.org_id,
            action_class=authority.required,
            summary=f"Agent called {name}",
            person_id=payload.get("person_id"),
            duty_id=payload.get("duty_id"),
            payload=dict(payload),
        )
        decision = authorize(action, roster, now)
        self._refuse(event, name, decision.rule, decision.reason, tool_use, action)

    def _refuse(
        self,
        event: BeforeToolCallEvent,
        name: str,
        rule: str,
        reason: str,
        tool_use: dict,
        action: ProposedAction | None = None,
    ) -> None:
        """Cancel the call and leave a trace the coordinator can act on."""
        message = (
            f"[{rule}] {reason} Zamu did not run this tool. "
            "Do not try another route to the same effect; tell the coordinator instead."
        )
        event.cancel_tool = message
        self.refusals.append(Refusal(name, rule, reason))

        if not self.record_to_ledger:
            return

        proposed = action or ProposedAction(
            org_id=self.org_id,
            action_class=TOOL_AUTHORITY.get(name, _UNKNOWN).required,
            summary=f"Agent called {name}",
            payload=dict(tool_use.get("input") or {}),
        )
        from zamu.core.authority import Decision

        Ledger(self.store, self.clock).record_blocked(
            proposed,
            Decision(False, proposed.action_class, rule, reason),
            idempotency_key("hook_block", self.org_id, name, rule, tool_use.get("toolUseId", "")),
        )

    # -- introspection -----------------------------------------------------------------

    @property
    def blocked_anything(self) -> bool:
        return bool(self.refusals)

    def rules_hit(self) -> tuple[str, ...]:
        return tuple(r.rule for r in self.refusals)
