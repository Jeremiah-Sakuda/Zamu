"""A deterministic stand-in for the model, for when there are no Bedrock credentials.

Zamu has to be demonstrable on a laptop with no AWS account attached, and the tests
have to be able to prove that the authority hook really cancels a real tool call
inside a real Strands agent. Both need something on the other end of the event loop.

`PlannedModel` is that something. It implements the `Model` interface and emits tool
calls by following the canonical loop — observe, evaluate, rank, ask, report — reading
the previous tool results to decide the next step. It is not pretending to be a
language model and cannot improvise: given the same roster it always does the same
thing, which is exactly what you want underneath a demo.

With Bedrock configured, this file is not used and the model chooses for itself.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass, field
from typing import Any

from strands.models.model import Model


@dataclass(frozen=True, slots=True)
class Say:
    """Finish the turn with a sentence for the coordinator."""

    text: str


@dataclass(frozen=True, slots=True)
class Use:
    """Call one tool with one set of arguments."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


Step = Say | Use


@dataclass(frozen=True, slots=True)
class Call:
    """One tool call already made this run, with whatever it returned."""

    name: str
    arguments: dict[str, Any]
    result: Any


def canonical_plan(history: list[Call]) -> Step:
    """The loop Zamu follows when nothing smarter is available.

    Read the gaps, rank the first one, ask one person about it, move to the next gap,
    and finish with a handover brief. Every branch is driven by what the tools
    actually returned, so a blocked ask ends the loop rather than being retried.
    """
    if not history:
        return Use("list_gaps", {"horizon_days": 21})

    gaps = _gaps_from(history)
    if not gaps:
        return _finish(history)

    handled = {c.arguments.get("duty_id") for c in history if c.name == "ask_next_person"}
    ranked = {c.arguments.get("duty_id") for c in history if c.name == "rank_candidates"}

    for duty_id in gaps:
        if duty_id not in ranked:
            return Use("rank_candidates", {"duty_id": duty_id})
        if duty_id not in handled:
            return Use("ask_next_person", {"duty_id": duty_id})

    return _finish(history)


def _finish(history: list[Call]) -> Step:
    if not any(c.name == "write_handover_brief" for c in history):
        return Use("write_handover_brief", {"hours": 24})

    brief = next(c for c in reversed(history) if c.name == "write_handover_brief").result
    text = brief.get("text") if isinstance(brief, dict) else None
    return Say(text or "Nothing needed you. Coverage is holding.")


def _gaps_from(history: list[Call]) -> list[str]:
    for call in history:
        if call.name != "list_gaps" or not isinstance(call.result, dict):
            continue
        return [g["duty_id"] for g in call.result.get("gaps", [])]
    return []


class PlannedModel(Model):
    """A `Model` that follows a plan instead of thinking.

    Deliberately not called a "mock": it is the production offline path, and the demo
    organisation runs on it when no Bedrock model is configured.
    """

    def __init__(self, planner=canonical_plan, config: dict | None = None) -> None:
        self._planner = planner
        self._config = config or {"model_id": "zamu-deterministic-planner"}
        self.steps: list[Step] = []

    # -- Model interface ---------------------------------------------------------------

    def get_config(self) -> Any:
        return self._config

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    async def stream(
        self,
        messages: list[dict],
        tool_specs: list[dict] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[dict]:
        step = self._planner(_history(messages))
        self.steps.append(step)

        if isinstance(step, Say):
            async for event in _emit_text(step.text):
                yield event
            return

        async for event in _emit_tool_use(step, len(self.steps)):
            yield event

    async def structured_output(
        self,
        output_model: type,
        prompt: list[dict],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict, None]:
        raise NotImplementedError(
            "The deterministic planner does not produce structured output. "
            "Configure a Bedrock model for interpretation tasks."
        )


# -- reading the conversation back -----------------------------------------------------


def _history(messages: list[dict]) -> list[Call]:
    """Reconstruct the tool calls made so far, pairing each use with its result.

    Derived from the message list rather than kept as instance state, so a resumed
    session or a replayed conversation reaches the same next step.
    """
    uses: dict[str, tuple[str, dict]] = {}
    results: dict[str, Any] = {}

    for message in messages:
        for block in message.get("content") or []:
            if "toolUse" in block:
                use = block["toolUse"]
                uses[use["toolUseId"]] = (use["name"], use.get("input") or {})
            elif "toolResult" in block:
                result = block["toolResult"]
                results[result["toolUseId"]] = _decode(result.get("content") or [])

    return [
        Call(name, arguments, results.get(tool_use_id))
        for tool_use_id, (name, arguments) in uses.items()
    ]


def _decode(content: list[dict]) -> Any:
    """Pull a tool's return value back out of the result blocks."""
    for block in content:
        if "json" in block:
            return block["json"]
        if "text" in block:
            try:
                return json.loads(block["text"])
            except (TypeError, ValueError):
                return block["text"]
    return None


# -- emitting the Bedrock converse-stream shape ----------------------------------------


async def _emit_text(text: str) -> AsyncIterable[dict]:
    yield {"messageStart": {"role": "assistant"}}
    yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
    yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}}
    yield {"contentBlockStop": {"contentBlockIndex": 0}}
    yield {"messageStop": {"stopReason": "end_turn"}}


async def _emit_tool_use(step: Use, ordinal: int) -> AsyncIterable[dict]:
    tool_use_id = f"planned-{ordinal}-{step.name}"
    yield {"messageStart": {"role": "assistant"}}
    yield {
        "contentBlockStart": {
            "contentBlockIndex": 0,
            "start": {"toolUse": {"toolUseId": tool_use_id, "name": step.name}},
        }
    }
    yield {
        "contentBlockDelta": {
            "contentBlockIndex": 0,
            "delta": {"toolUse": {"input": json.dumps(step.arguments)}},
        }
    }
    yield {"contentBlockStop": {"contentBlockIndex": 0}}
    yield {"messageStop": {"stopReason": "tool_use"}}
