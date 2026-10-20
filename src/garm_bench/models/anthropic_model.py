"""Anthropic Messages API adapter. Every call is metered through the budget ledger.

The API key is read by the SDK from the environment (ANTHROPIC_API_KEY); this module never reads or prints it.
"""

from __future__ import annotations

import time

import anthropic

from ..budget import Ledger, Price
from .base import ModelTurn, ToolCall, Usage, estimate_tokens


def _to_api_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": m["text"]}]})
        elif m["role"] == "assistant":
            out.append({"role": "assistant", "content": m["turn"].raw})
        elif m["role"] == "tool_results":
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": r["is_error"]}
                for r in m["results"]
            ]})
    # Cache breakpoint on the newest user content so each turn reads the previous turn's prefix from cache.
    if out and out[-1]["role"] == "user":
        last = dict(out[-1]["content"][-1])
        last["cache_control"] = {"type": "ephemeral"}
        out[-1] = {"role": "user", "content": [*out[-1]["content"][:-1], last]}
    return out


class AnthropicModel:
    def __init__(self, model_id: str, price: Price, ledger: Ledger, effort: str | None = None, max_retries: int = 4):
        self.model_id = model_id
        self.price = price
        self.ledger = ledger
        self.effort = effort
        self.client = anthropic.AsyncAnthropic(max_retries=max_retries)

    async def generate(self, system: str, tools: list[dict], messages: list[dict], max_tokens: int) -> ModelTurn:
        api_messages = _to_api_messages(messages)
        est = self.price.upper_bound(estimate_tokens(system, tools, api_messages), max_tokens)
        reservation = self.ledger.reserve(est)
        kwargs = {}
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        started = time.monotonic()
        try:
            resp = await self.client.messages.create(
                model=self.model_id,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=api_messages,
                **kwargs,
            )
        except Exception as e:
            self.ledger.settle(reservation, 0.0, {"model": self.model_id, "error": type(e).__name__})
            raise
        u = resp.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
            cache_read_tokens=u.cache_read_input_tokens or 0,
        )
        cost = self.price.cost(usage.input_tokens, usage.output_tokens, usage.cache_write_tokens, usage.cache_read_tokens)
        self.ledger.settle(reservation, cost, {
            "model": self.model_id, "request_id": getattr(resp, "_request_id", None), "stop_reason": resp.stop_reason,
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "cache_write_tokens": usage.cache_write_tokens, "cache_read_tokens": usage.cache_read_tokens,
            "latency_s": round(time.monotonic() - started, 3),
        })
        calls = [ToolCall(b.id, b.name, b.input) for b in resp.content if b.type == "tool_use"]
        text = "".join(b.text for b in resp.content if b.type == "text")
        return ModelTurn(text=text, tool_calls=calls, stop_reason=resp.stop_reason or "", usage=usage,
                         raw=resp.content, cost_usd=cost)
