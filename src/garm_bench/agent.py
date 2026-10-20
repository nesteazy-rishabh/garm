"""The agent loop for one job run (tool-calling defences D0 to D3)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .defences import Defence
from .jobs import HANDBOOK, Job
from .models.base import ModelClient, Usage
from .tools import REGISTRY, ToolContext, ToolError, execute, validate_args
from .world import World, Writer

MAX_TURNS = 25
MAX_TOKENS = 8000


@dataclass
class JobRunRecord:
    job: str
    job_run_id: int
    at: str
    status: str = "ok"
    error: str | None = None
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    approvals_queued: int = 0
    latency_s: float = 0.0
    transcript: list[dict] = field(default_factory=list)


def system_prompt(defence: Defence, job: Job) -> str:
    return HANDBOOK + defence.system_addendum() + "\n# This job\n" + job.instructions + "\n"


def queue_approval(world: World, job_run_id: int, tool: str, args: dict, reason: str, card: str) -> int:
    cur = world.conn.execute(
        "INSERT INTO approvals (job_run_id, tool, args_json, card_text, reason, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (job_run_id, tool, json.dumps(args, sort_keys=True), card, reason, world.ts()),
    )
    return cur.lastrowid


async def run_job(world: World, job: Job, defence: Defence, client: ModelClient, max_turns: int = MAX_TURNS) -> JobRunRecord:
    run_id = world.start_job_run(job.name)
    rec = JobRunRecord(job=job.name, job_run_id=run_id, at=world.ts())
    ctx = ToolContext(world, Writer("agent", job.name, run_id))
    system = system_prompt(defence, job)
    tools = [REGISTRY[n].schema() for n in job.tools]
    messages: list[dict] = [{"role": "user", "text": job.kickoff(world)}]
    if hasattr(client, "on_job_start"):
        client.on_job_start(job.name)
    started = time.monotonic()
    try:
        for _ in range(max_turns):
            turn = await client.generate(system, tools, messages, MAX_TOKENS)
            rec.model_calls += 1
            rec.usage += turn.usage
            rec.cost_usd += turn.cost_usd
            messages.append({"role": "assistant", "turn": turn})
            rec.transcript.append({"assistant": turn.text, "stop_reason": turn.stop_reason,
                                   "tool_calls": [{"id": c.id, "name": c.name, "input": c.input} for c in turn.tool_calls]})
            if turn.stop_reason == "refusal":
                rec.status = "refused"
                break
            if not turn.tool_calls:
                if turn.stop_reason == "max_tokens":
                    rec.status = "truncated"
                break
            results = []
            for call in turn.tool_calls:
                rec.tool_calls += 1
                content, is_error = _dispatch(world, ctx, defence, call.name, call.input, rec)
                results.append({"id": call.id, "name": call.name, "content": content, "is_error": is_error})
                world.conn.commit()
            messages.append({"role": "tool_results", "results": results})
            rec.transcript.append({"tool_results": results})
        else:
            rec.status = "turn_limit"
    except Exception as e:  # noqa: BLE001 -- recorded, not raised: one failed run must not stop the episode
        rec.status = "error"
        rec.error = f"{type(e).__name__}: {e}"
    rec.latency_s = round(time.monotonic() - started, 3)
    world.finish_job_run(run_id, rec.status, rec.model_calls, rec.tool_calls, rec.error)
    world.conn.commit()
    return rec


def _dispatch(world: World, ctx: ToolContext, defence: Defence, name: str, args, rec: JobRunRecord) -> tuple[str, bool]:
    if name not in REGISTRY or name not in _allowed(rec.job):
        return f"Error: tool {name} is not available in this job", True
    try:
        args = validate_args(REGISTRY[name], dict(args) if isinstance(args, dict) else args)
        gate = defence.gate(world, name, args)
        if gate is not None:
            approval_id = queue_approval(world, ctx.writer.job_run_id, name, args, gate.reason, gate.card)
            rec.approvals_queued += 1
            return defence.render({"status": "queued_for_approval", "approval_id": approval_id,
                                   "message": "Held for staff approval. It will run if approved. Do not retry."}), False
        return defence.render(execute(ctx, name, args)), False
    except ToolError as e:
        return f"Error: {e}", True


def _allowed(job_name: str) -> tuple[str, ...]:
    from .jobs import JOBS

    return JOBS[job_name].tools
