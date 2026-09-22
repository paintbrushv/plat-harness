"""Replaceable model interface, with NullModel as the offline default."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from plat_harness.errors import HarnessError, NO_MODEL_CONFIGURED


@dataclass(frozen=True)
class ModelTurn:
    """One model step; optional timing fields preserve existing callers."""

    content: str
    tool_calls: tuple[dict[str, Any], ...] = ()
    model_id: str = "null"
    finish_reason: str | None = None
    latency_s: float | None = None
    ttft_s: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class ModelRouter(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        """Return the next model turn. Must not hard-code Anthropic/Claude tool XML."""
        ...


class NullModel:
    """Slice 0 implementation. Refuses to answer. No network, no API key."""

    model_id = "null"

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        raise HarnessError(
            NO_MODEL_CONFIGURED,
            "No rented model is configured. Slice 0 uses NullModel; "
            "arithmetic and certified metrics must come from tools, not a chat product.",
        )
