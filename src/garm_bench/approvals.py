"""The human approval step.

Two timings are supported. Inline: the approver decides when the agent attempts the action, and the agent sees the
outcome (the default, as in an interactive permission prompt). Deferred: decisions are made after the job run has
ended, so the agent never learns the outcome within the run (a naive asynchronous queue).

Week 1 has the two bounding approvers. The approver models (oracle, rubber-stamp, fatigue, card-only) are applied
post hoc to the logged queue in week 2: under approve-all every queued action runs, so the log holds each action
together with what it caused.
"""

from __future__ import annotations

import inspect
import json
from typing import Protocol

from .tools import ToolContext, ToolError, execute
from .world import World, Writer


class Approver(Protocol):
    name: str

    def decide(self, world: World, approval: dict) -> bool: ...


class ApproveAll:
    name = "approve_all"

    def decide(self, world: World, approval: dict) -> bool:
        return True


class DenyAll:
    name = "deny_all"

    def decide(self, world: World, approval: dict) -> bool:
        return False


class OracleApprover:
    """Knows the episode's ground truth: rejects any action whose card or arguments carry an attack indicator."""

    name = "oracle"

    def __init__(self):
        self.indicators: list[str] = []

    def bind(self, episode: dict) -> None:
        self.indicators = [s.lower() for s in episode.get("attack", {}).get("indicators", [])]

    def decide(self, world: World, approval: dict) -> bool:
        text = (approval["card_text"] + " " + approval["args_json"]).lower()
        return not any(i in text for i in self.indicators)


class LLMCardApprover:
    """A model that sees only the approval card (including D5 provenance when the card has it). Decisions are cached
    by card text, so identical cards get identical answers."""

    name = "card_llm"

    def __init__(self, client):
        self.client = client
        self.cache: dict[str, bool] = {}

    async def decide(self, world: World, approval: dict) -> bool:
        from .analysis import CARD_APPROVER_PROMPT

        card = approval["card_text"]
        if card not in self.cache:
            turn = await self.client.generate(CARD_APPROVER_PROMPT, [], [{"role": "user", "text": card}], 400)
            try:
                data = json.loads(turn.text[turn.text.index("{"): turn.text.rindex("}") + 1])
                self.cache[card] = str(data.get("decision", "")).lower().startswith("approve")
            except ValueError:
                self.cache[card] = True
        return self.cache[card]


APPROVERS = {"approve_all": ApproveAll, "deny_all": DenyAll, "oracle": OracleApprover}


async def decide(world: World, approval: dict, approver: Approver) -> tuple[str, str | None]:
    """Ask the approver about one queued action; execute it if approved. Returns (status, result text)."""
    verdict = approver.decide(world, approval)
    if inspect.isawaitable(verdict):
        verdict = await verdict
    if not verdict:
        status, result = "rejected", None
    else:
        ctx = ToolContext(world, Writer("agent", f"{approval['job']}+approved", approval["job_run_id"]))
        try:
            result = json.dumps(execute(ctx, approval["tool"], json.loads(approval["args_json"])), default=str)
            status = "executed"
        except ToolError as e:
            status, result = "failed", str(e)
    world.conn.execute(
        "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, result_text = ? WHERE id = ?",
        (status, world.ts(), approver.name, result, approval["id"]),
    )
    world.conn.commit()
    return status, result


def pending(world: World, approval_id: int | None = None) -> list[dict]:
    sql = ("SELECT a.*, r.job FROM approvals a JOIN job_runs r ON r.id = a.job_run_id WHERE a.status = 'pending'")
    if approval_id is not None:
        return world.all(sql + " AND a.id = ?", (approval_id,))
    return world.all(sql + " ORDER BY a.id")


async def process_pending(world: World, approver: Approver) -> int:
    """Deferred mode: decide everything queued during the last run, after the run has ended."""
    rows = pending(world)
    for a in rows:
        await decide(world, a, approver)
    return len(rows)
