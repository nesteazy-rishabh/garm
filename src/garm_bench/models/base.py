"""Provider-neutral model interface.

Conversation messages use a small neutral format so any provider can be plugged in:
  {"role": "user", "text": str}
  {"role": "assistant", "turn": ModelTurn}
  {"role": "tool_results", "results": [{"id": str, "content": str, "is_error": bool}]}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Self


@dataclass
class ToolCall:
    id: str
    name: str
    input: Any


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    def __iadd__(self, other: Usage) -> Self:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.cache_read_tokens += other.cache_read_tokens
        return self


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: Usage = field(default_factory=Usage)
    raw: Any = None  # provider content to replay unchanged on the next request
    cost_usd: float = 0.0


class ModelClient(Protocol):
    model_id: str

    async def generate(self, system: str, tools: list[dict], messages: list[dict], max_tokens: int) -> ModelTurn: ...


def estimate_tokens(*parts: Any) -> int:
    """A deliberately high token estimate (about 2.5 characters per token) for pre-call budget reservations."""
    import json

    chars = sum(len(p) if isinstance(p, str) else len(json.dumps(p, default=str)) for p in parts)
    return int(chars / 2.5) + 1
