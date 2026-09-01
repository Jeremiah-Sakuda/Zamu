"""Assembling the agent.

Model selection is deliberately explicit and falls back rather than failing: Zamu on a
laptop with no AWS credentials must still run its loop, or the thing cannot be
demonstrated, developed against, or tested.

Model tiering follows the same principle as everything else here — spend capability
where judgement is actually required. Routine work (reading a structured roster,
following the canonical loop) goes to a small fast model. Interpretation of a
free-text message from a human, where getting the wrong Priya is a real failure, is
escalated to a stronger one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from strands import Agent

from zamu.agent.authority_hook import AuthorityHook
from zamu.agent.planner import PlannedModel
from zamu.agent.prompt import SYSTEM_PROMPT
from zamu.agent.tools import build_tools
from zamu.core.clock import Clock, SystemClock
from zamu.core.fill import CoverageService
from zamu.core.store import Store
from zamu.infra.notify import Notifier, OutboxNotifier

#: Routine interpretation of already-structured data.
FAST_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
#: Genuinely ambiguous human text, where a wrong resolution has a real cost.
CAREFUL_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


@dataclass
class ZamuAgent:
    """A Strands agent bound to one organisation, with its gate attached."""

    agent: Agent
    service: CoverageService
    hook: AuthorityHook
    org_id: str
    model_name: str

    def __call__(self, message: str) -> Any:
        return self.agent(message)

    @property
    def refusals(self):
        return tuple(self.hook.refusals)


def resolve_model(prefer: str | None = None, *, careful: bool = False) -> tuple[Any, str]:
    """Pick a model, falling back to the deterministic planner when Bedrock is absent.

    Returns the model and a human-readable name for logs and the console, so a judge
    can always tell which path produced what they are looking at.
    """
    if prefer == "planner":
        return PlannedModel(), "deterministic-planner"

    model_id = prefer or os.environ.get(
        "ZAMU_MODEL_ID", CAREFUL_MODEL if careful else FAST_MODEL
    )

    if os.environ.get("ZAMU_FORCE_PLANNER") == "1":
        return PlannedModel(), "deterministic-planner (forced)"

    if not bedrock_available():
        return PlannedModel(), "deterministic-planner (no Bedrock credentials)"

    try:
        from strands.models import BedrockModel

        model = BedrockModel(
            model_id=model_id,
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            temperature=0.2,
        )
    except Exception:
        return PlannedModel(), "deterministic-planner (Bedrock unavailable)"

    return model, model_id


def bedrock_available() -> bool:
    """Whether there are credentials to call Bedrock with.

    Checked up front rather than discovered on the first invocation, because a model
    that constructs happily and then fails mid-loop produces a half-finished fill and
    a confusing receipt. Better to know before the loop starts which path we are on.
    """
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def build_agent(
    store: Store,
    org_id: str,
    *,
    clock: Clock | None = None,
    notifier: Notifier | None = None,
    base_url: str = "http://localhost:8000",
    model: Any | None = None,
    careful: bool = False,
) -> ZamuAgent:
    """Wire the service, tools, hook and model into one agent."""
    clock = clock or SystemClock()
    notifier = notifier or OutboxNotifier()
    service = CoverageService(store, clock, notifier, base_url=base_url)

    resolved, name = (model, getattr(model, "model_name", type(model).__name__)) if model else (
        resolve_model(careful=careful)
    )

    hook = AuthorityHook(store=store, clock=clock, org_id=org_id)
    agent = Agent(
        model=resolved,
        tools=build_tools(service, org_id),
        system_prompt=SYSTEM_PROMPT,
        hooks=[hook],
        name="zamu",
        description="Keeps a volunteer roster covered.",
    )
    return ZamuAgent(agent=agent, service=service, hook=hook, org_id=org_id, model_name=name)
