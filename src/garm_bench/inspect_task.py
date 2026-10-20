"""Inspect (inspect_ai) wrapper, so any model Inspect supports can be run on Garm.

    inspect eval src/garm_bench/inspect_task.py --model anthropic/claude-haiku-5-5 -T defence=D2 -T max_spend=1.0

Models with an entry in data/prices.json are metered through the same budget ledger as the native runner. Other
models run only with -T allow_unpriced=true, because their spend cannot be capped by this harness.
"""

from __future__ import annotations

from dataclasses import asdict

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    GenerateConfig,
    Model,
    get_model,
)
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer, stderr
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.tool import ToolInfo, ToolParams

from garm_bench.approvals import APPROVERS
from garm_bench.budget import Ledger, check_prices_fresh, load_prices
from garm_bench.defences import get_defence
from garm_bench.episodes import DATA_DIR, load_episodes
from garm_bench.models.base import ModelTurn, ToolCall, Usage
from garm_bench.runner import run_episode

_LEDGER: Ledger | None = None


class InspectModel:
    def __init__(self, model: Model, price=None, ledger: Ledger | None = None):
        self.model = model
        self.model_id = str(model)
        self.price = price
        self.ledger = ledger

    async def generate(self, system: str, tools: list[dict], messages: list[dict], max_tokens: int) -> ModelTurn:
        from garm_bench.models.base import estimate_tokens

        msgs = [ChatMessageSystem(content=system)]
        for m in messages:
            if m["role"] == "user":
                msgs.append(ChatMessageUser(content=m["text"]))
            elif m["role"] == "assistant":
                msgs.append(m["turn"].raw)
            else:
                msgs.extend(ChatMessageTool(content=r["content"], tool_call_id=r["id"], function=r.get("name"))
                            for r in m["results"])
        infos = [ToolInfo(name=t["name"], description=t["description"],
                          parameters=ToolParams.model_validate(t["input_schema"])) for t in tools]
        reservation = 0.0
        if self.ledger:
            reservation = self.ledger.reserve(self.price.upper_bound(estimate_tokens(system, tools, messages), max_tokens))
        try:
            out = await self.model.generate(msgs, tools=infos, config=GenerateConfig(max_tokens=max_tokens))
        except Exception:
            if self.ledger:
                self.ledger.settle(reservation, 0.0, {"model": self.model_id, "error": "generate failed"})
            raise
        u = out.usage
        usage = Usage(input_tokens=u.input_tokens if u else 0, output_tokens=u.output_tokens if u else 0,
                      cache_write_tokens=(u.input_tokens_cache_write or 0) if u else 0,
                      cache_read_tokens=(u.input_tokens_cache_read or 0) if u else 0)
        cost = 0.0
        if self.price:
            cost = self.price.cost(usage.input_tokens, usage.output_tokens, usage.cache_write_tokens,
                                   usage.cache_read_tokens)
        if self.ledger:
            self.ledger.settle(reservation, cost, {"model": self.model_id, "via": "inspect",
                                                   "input_tokens": usage.input_tokens,
                                                   "output_tokens": usage.output_tokens,
                                                   "cache_write_tokens": usage.cache_write_tokens,
                                                   "cache_read_tokens": usage.cache_read_tokens})
        msg: ChatMessageAssistant = out.message
        calls = [ToolCall(tc.id, tc.function, tc.arguments) for tc in (msg.tool_calls or [])]
        return ModelTurn(text=msg.text, tool_calls=calls, stop_reason=str(out.stop_reason), usage=usage, raw=msg,
                         cost_usd=cost)


@solver
def garm_solver(defence: str, approver: str, max_spend: float, allow_unpriced: bool):
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        global _LEDGER
        model = get_model()
        name = str(model).split("/")[-1]
        prices, meta = load_prices(DATA_DIR / "prices.json")
        price = prices.get(name)
        if price is None and not str(model).startswith("mockllm") and not allow_unpriced:
            raise RuntimeError(f"no price for {model}; add it to data/prices.json or pass allow_unpriced=true")
        if price is not None:
            check_prices_fresh(meta)
            if _LEDGER is None:
                _LEDGER = Ledger(DATA_DIR.parent / "runs" / "ledger.jsonl", max_spend, label=f"inspect:{model}")
        client = InspectModel(model, price, _LEDGER if price is not None else None)
        _, result = await run_episode(state.metadata["episode"], get_defence(defence), client, APPROVERS[approver]())
        state.metadata["garm_result"] = asdict(result)
        state.metadata["garm_transcripts"] = result.transcripts
        return state

    return solve


@scorer(metrics=[accuracy(), stderr()])
def garm_scorer():
    async def score(state: TaskState, target: Target) -> Score:
        r = state.metadata["garm_result"]
        failed = [f"{c['name']}: {c['detail']}" for c in r["checks"] if not c["passed"]]
        return Score(value=CORRECT if r["passed"] else INCORRECT, explanation="; ".join(failed) or "all checks passed",
                     metadata={"approvals": len(r["approvals"]), "cost_usd": r["cost_usd"]})

    return score


@task
def garm(split: str = "benign", defence: str = "D0", approver: str = "approve_all", max_spend: float = 1.0,
         episodes: str | list[str] | None = None, allow_unpriced: bool = False) -> Task:
    if isinstance(episodes, str):
        episodes = episodes.split(",")
    eps = load_episodes(split, episodes or None)
    samples = [Sample(id=e["id"], input=e["description"], metadata={"episode": e}) for e in eps]
    return Task(dataset=MemoryDataset(samples), solver=garm_solver(defence, approver, max_spend, allow_unpriced),
                scorer=garm_scorer())
