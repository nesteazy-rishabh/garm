"""OpenRouter adapter (OpenAI-compatible chat completions), metered through the budget ledger.

One client reaches every non-Anthropic reviewer on the panel: OpenAI, xAI, Moonshot, Meta, DeepSeek, Alibaba.
The API key is read from the environment (OPENROUTER_API_KEY); this module never reads or prints it. Used only for
the write-up audit (a single system + user turn, no tools); it does not drive agent episodes.
"""

from __future__ import annotations

import os
import time

import httpx

from ..budget import Ledger, Price
from .base import ModelTurn, ModelUnavailable, Usage, estimate_tokens

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterModel:
    def __init__(self, model_id: str, price: Price, ledger: Ledger, timeout: float = 600.0):
        self.model_id = model_id
        self.price = price
        self.ledger = ledger
        self._key = os.environ["OPENROUTER_API_KEY"]
        self._timeout = timeout

    async def generate(self, system: str, tools: list[dict], messages: list[dict], max_tokens: int) -> ModelTurn:
        if tools:
            raise ValueError("the OpenRouter audit adapter does not support tools")
        chat = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] != "user":
                raise ValueError("the OpenRouter audit adapter only handles plain user turns")
            chat.append({"role": "user", "content": m["text"]})
        est = self.price.upper_bound(estimate_tokens(system, chat), max_tokens)
        reservation = self.ledger.reserve(est)
        body = {
            "model": self.model_id,
            "messages": chat,
            "max_tokens": max_tokens,
            "temperature": 0,
            "usage": {"include": True},  # ask OpenRouter to report the actual cost it charged
        }
        headers = {
            "Authorization": f"Bearer {self._key}",
            "X-Title": "garm-bench audit panel",
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(ENDPOINT, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            self.ledger.settle(reservation, 0.0, {"model": self.model_id, "error": type(e).__name__})
            raise ModelUnavailable(f"{type(e).__name__}: {e}") from e
        if "error" in data:  # OpenRouter reports upstream refusals/outages in the body with a 200
            self.ledger.settle(reservation, 0.0, {"model": self.model_id, "error": str(data["error"])[:200]})
            raise ModelUnavailable(f"openrouter error: {data['error']}")
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        u = data.get("usage", {}) or {}
        usage = Usage(input_tokens=u.get("prompt_tokens", 0) or 0, output_tokens=u.get("completion_tokens", 0) or 0)
        # Prefer the cost OpenRouter actually charged; fall back to the verified price table.
        reported = u.get("cost")
        cost = float(reported) if reported is not None else self.price.cost(usage.input_tokens, usage.output_tokens, 0, 0)
        self.ledger.settle(reservation, cost, {
            "model": self.model_id, "request_id": data.get("id"), "stop_reason": choice.get("finish_reason"),
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "reported_cost_usd": reported, "latency_s": round(time.monotonic() - started, 3),
        })
        return ModelTurn(text=text, tool_calls=[], stop_reason=choice.get("finish_reason") or "", usage=usage,
                         raw=text, cost_usd=cost)
