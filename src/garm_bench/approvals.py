"""The human approval step between job runs.

Week 1 has the two bounding approvers. The approver models (oracle, rubber-stamp, fatigue, card-only) are applied
post hoc to the logged queue in week 2: under approve-all every queued action runs, so the log holds each action
together with what it caused.
"""

from __future__ import annotations

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


APPROVERS = {"approve_all": ApproveAll, "deny_all": DenyAll}


def process_pending(world: World, approver: Approver) -> int:
    pending = world.all(
        "SELECT a.*, r.job FROM approvals a JOIN job_runs r ON r.id = a.job_run_id WHERE a.status = 'pending'"
        " ORDER BY a.id"
    )
    for a in pending:
        if not approver.decide(world, a):
            status, result = "rejected", None
        else:
            ctx = ToolContext(world, Writer("agent", f"{a['job']}+approved", a["job_run_id"]))
            try:
                result = json.dumps(execute(ctx, a["tool"], json.loads(a["args_json"])), default=str)
                status = "executed"
            except ToolError as e:
                status, result = "failed", str(e)
        world.conn.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, result_text = ? WHERE id = ?",
            (status, world.ts(), approver.name, result, a["id"]),
        )
    world.conn.commit()
    return len(pending)
