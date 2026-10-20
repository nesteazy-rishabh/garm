"""The agent loop for one job run (tool-calling defences D0 to D3)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .approvals import decide, pending
from .defences import CONSEQUENTIAL_TOOLS, Defence, effect_card
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


async def run_job(world: World, job: Job, defence: Defence, client: ModelClient, max_turns: int = MAX_TURNS,
                  inline_approver=None) -> JobRunRecord:
    """Run one job. With `inline_approver`, gated actions are decided when attempted; otherwise they stay queued."""
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
                content, is_error = await _dispatch(world, ctx, defence, call.name, call.input, rec, inline_approver)
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


def _log_action(world: World, run_id: int, name: str, args: dict, card: str, outcome: str,
                approval_id: int | None = None) -> None:
    world.conn.execute(
        "INSERT INTO action_log (job_run_id, tool, args_json, card_text, outcome, approval_id, at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, name, json.dumps(args, sort_keys=True, default=str), card, outcome, approval_id, world.ts()),
    )


async def _dispatch(world: World, ctx: ToolContext, defence: Defence, name: str, args, rec: JobRunRecord,
              inline_approver=None) -> tuple[str, bool]:
    if name not in REGISTRY or name not in _allowed(rec.job):
        return f"Error: tool {name} is not available in this job", True
    run_id = ctx.writer.job_run_id
    try:
        args = validate_args(REGISTRY[name], dict(args) if isinstance(args, dict) else args)
    except ToolError as e:
        return f"Error: {e}", True
    card = effect_card(world, name, args) if name in CONSEQUENTIAL_TOOLS else None
    try:
        gate = await defence.review(world, name, args, _context(rec))
        if gate is None:
            result = execute(ctx, name, args)
            if card:
                _log_action(world, run_id, name, args, card, "executed")
            return defence.render(result), False
        approval_id = queue_approval(world, run_id, name, args, gate.reason, gate.card)
        rec.approvals_queued += 1
        if inline_approver is None:
            _log_action(world, run_id, name, args, card or gate.card, "queued", approval_id)
            return defence.render({"status": "queued_for_approval", "approval_id": approval_id,
                                   "message": "Held for staff approval. It will run later if approved. Do not retry."}), False
        status, result = await decide(world, pending(world, approval_id)[0], inline_approver)
        _log_action(world, run_id, name, args, card or gate.card, status, approval_id)
        if status == "executed":
            return defence.render({"status": "approved_and_done", "result": json.loads(result)}), False
        if status == "rejected":
            return defence.render({"status": "rejected", "approval_id": approval_id,
                                   "message": "A staff reviewer rejected this action. Do not retry it."}), False
        return f"Error: approved but failed: {result}", True
    except ToolError as e:
        if card:
            _log_action(world, run_id, name, args, card, "error")
        return f"Error: {e}", True


def _context(rec: JobRunRecord, limit: int = 4000) -> str:
    """Recent turns of this run, newest first, for a monitor."""
    parts = []
    for item in reversed(rec.transcript):
        if "tool_results" in item:
            parts.extend(f"[tool result] {r['content'][:800]}" for r in item["tool_results"])
        else:
            if item.get("assistant"):
                parts.append(f"[agent] {item['assistant'][:400]}")
            parts.extend(f"[agent call] {c['name']} {json.dumps(c['input'], default=str)[:300]}"
                         for c in item.get("tool_calls", []))
    return "\n".join(parts)[:limit]


def _allowed(job_name: str) -> tuple[str, ...]:
    from .jobs import JOBS

    return JOBS[job_name].tools
