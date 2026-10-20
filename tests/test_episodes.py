"""Grader validation without API calls: a scripted correct solver must pass every episode, a do-nothing agent must
fail every episode, and the oracle must also pass under D1 and D2 (with every queued action approved)."""

import asyncio

import pytest

from garm_bench.approvals import ApproveAll, DenyAll
from garm_bench.defences import get_defence
from garm_bench.episodes import load_episodes
from garm_bench.models.scripted import NullModel, ScriptedModel
from garm_bench.runner import run_episode

EPISODES = load_episodes("benign") + load_episodes("legit")


def run(ep, defence, model, approver=None, mode="inline"):
    return asyncio.run(run_episode(ep, get_defence(defence), model, approver or ApproveAll(), approval_mode=mode))


def failed(result):
    return [f"{c['name']}: {c['detail']}" for c in result.checks if not c["passed"]]


def test_episode_counts():
    assert len(load_episodes("benign")) == 40
    assert len(load_episodes("legit")) == 16


@pytest.mark.parametrize("defence,mode", [("D0", "inline"), ("D1", "inline"), ("D2", "inline"), ("D2", "deferred")])
@pytest.mark.parametrize("ep", EPISODES, ids=[e["id"] for e in EPISODES])
def test_oracle_passes(ep, defence, mode):
    _, result = run(ep, defence, ScriptedModel(ep["oracle"]), mode=mode)
    assert all(r["status"] == "ok" for r in result.job_runs), result.transcripts
    assert result.passed, failed(result)


@pytest.mark.parametrize("ep", EPISODES, ids=[e["id"] for e in EPISODES])
def test_null_agent_fails(ep):
    _, result = run(ep, "D0", NullModel())
    assert not result.passed


@pytest.mark.parametrize("ep", [e for e in EPISODES if e["expect"].get("payments") or e["expect"].get("refunds_issued")],
                         ids=lambda e: e["id"])
@pytest.mark.parametrize("mode", ["inline", "deferred"])
def test_gateway_with_deny_all_blocks_money_movement(ep, mode):
    world, result = run(ep, "D2", ScriptedModel(ep["oracle"]), DenyAll(), mode)
    assert world.scalar("SELECT COUNT(*) FROM approvals WHERE status = 'rejected'") > 0
    assert not result.passed


def test_job_scheduling_counts():
    ep = next(e for e in EPISODES if e["template"] == "refund_valid")
    _, result = run(ep, "D0", ScriptedModel(ep["oracle"]))
    assert [r["job"] for r in result.job_runs] == ["triage_inbox", "refund_queue", "customer_followup"]
