"""Run one episode: build the world, advance simulated time, fire jobs, process approvals, grade."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .agent import JobRunRecord, run_job
from .approvals import Approver, process_pending
from .defences import Defence
from .episodes import input_hash
from .graders import capture_baseline, grade
from .jobs import JOBS
from .models.base import ModelClient, Usage
from .seed import build_base_world
from .world import SEED_WRITER, World, Writer

MAX_JOB_RUNS = 12
FIXTURE_ORDER = ("purchase_orders", "invoices", "payments", "orders", "refunds", "tickets", "vendor_verifications",
                 "notes")
STAFF_WRITER = Writer("staff", "staff")


@dataclass
class EpisodeResult:
    episode_id: str
    defence: str
    model: str
    approver: str
    attempt: int
    passed: bool
    checks: list[dict]
    job_runs: list[dict] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_s: float = 0.0
    status: str = "ok"
    input_hash: str = ""
    approval_mode: str = "inline"


def schedule_at(tick: datetime) -> list[str]:
    jobs = ["triage_inbox", "update_records"]
    if tick.hour == 10:
        jobs.append("refund_queue")
    if tick.weekday() == 3 and tick.hour == 11:
        jobs.append("vendor_payment_run")
    if tick.hour == 16:
        jobs.append("customer_followup")
    return jobs


def build_world(episode: dict) -> World:
    world = World(datetime.fromisoformat(episode["start"]))
    build_base_world(world)
    for table in FIXTURE_ORDER:
        for row in episode.get("fixtures", {}).get(table, []):
            writer = STAFF_WRITER if table == "vendor_verifications" else SEED_WRITER
            world.insert(table, dict(row), writer)
    world.conn.commit()
    return world


async def run_episode(episode: dict, defence: Defence, client: ModelClient, approver: Approver,
                      attempt: int = 0, approval_mode: str = "inline") -> tuple[World, EpisodeResult]:
    if approval_mode not in ("inline", "deferred"):
        raise ValueError("approval_mode must be inline or deferred")
    world = build_world(episode)
    baseline = capture_baseline(world)
    start, end = datetime.fromisoformat(episode["start"]), datetime.fromisoformat(episode["end"])
    events = sorted(episode["events"], key=lambda e: e["at"])
    tick = start.replace(minute=0) + (timedelta(hours=1) if start.minute else timedelta())
    records: list[JobRunRecord] = []
    status = "ok"
    t0 = time.monotonic()
    while tick <= end:
        while events and datetime.fromisoformat(events[0]["at"]) <= tick:
            ev = events.pop(0)
            world.now = datetime.fromisoformat(ev["at"])
            world.deliver(ev["message"])
        world.now = tick
        world.conn.commit()
        for name in schedule_at(tick):
            job = JOBS[name]
            if not job.has_work(world):
                continue
            if len(records) >= MAX_JOB_RUNS:
                status = "job_run_cap"
                break
            records.append(await run_job(world, job, defence, client,
                                         inline_approver=approver if approval_mode == "inline" else None))
            process_pending(world, approver)
        if status != "ok":
            break
        tick += timedelta(hours=1)
    checks = grade(world, episode, baseline)
    usage = Usage()
    for r in records:
        usage += r.usage
    result = EpisodeResult(
        episode_id=episode["id"], defence=defence.name, model=getattr(client, "model_id", "?"),
        approver=approver.name, attempt=attempt, passed=all(c["passed"] for c in checks), checks=checks,
        job_runs=[{k: v for k, v in asdict(r).items() if k not in ("transcript", "usage")} | {"usage": asdict(r.usage)}
                  for r in records],
        approvals=world.all("SELECT id, job_run_id, tool, args_json, card_text, reason, status, decided_by FROM approvals"),
        usage=asdict(usage), cost_usd=round(sum(r.cost_usd for r in records), 6),
        latency_s=round(time.monotonic() - t0, 3), status=status, input_hash=input_hash(episode), approval_mode=approval_mode,
    )
    result.transcripts = [{"job": r.job, "at": r.at, "status": r.status, "error": r.error, "turns": r.transcript}
                          for r in records]
    return world, result
