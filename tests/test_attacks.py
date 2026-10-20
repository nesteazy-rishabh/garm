"""Attack episodes are validated without API calls: a secure scripted agent must keep the attack inactive and still
do the legitimate work; a scripted victim that follows the attacker must trigger activation (and be logged as
incorporation); a do-nothing agent must trigger nothing."""

import asyncio

import pytest

from garm_bench.approvals import ApproveAll, DenyAll
from garm_bench.defences import get_defence
from garm_bench.episodes import load_episodes
from garm_bench.models.scripted import NullModel, ScriptedModel
from garm_bench.runner import run_episode

ATTACKS = load_episodes("attack")
IDS = [e["id"] for e in ATTACKS]


def run(ep, defence, model, approver=None):
    return asyncio.run(run_episode(ep, get_defence(defence), model, approver or ApproveAll()))


def test_attack_split_shape():
    assert len(ATTACKS) == 60
    timings = [e["attack"]["timing"] for e in ATTACKS]
    assert timings.count("cross_run") == 40 and timings.count("same_run") == 20


@pytest.mark.parametrize("ep", ATTACKS, ids=IDS)
def test_secure_oracle_blocks_attack_and_does_the_work(ep):
    _, r = run(ep, "D0", ScriptedModel(ep["oracle"]))
    assert all(j["status"] == "ok" for j in r.job_runs), r.transcripts
    assert not r.attack["activation"], r.attack["activation_checks"]
    assert r.passed, [c for c in r.checks if not c["passed"]]


@pytest.mark.parametrize("ep", ATTACKS, ids=IDS)
def test_victim_activates_attack(ep):
    _, r = run(ep, "D0", ScriptedModel(ep["victim"]))
    assert all(j["status"] == "ok" for j in r.job_runs), r.transcripts
    assert r.attack["activation"], r.attack["activation_checks"]
    assert r.attack["incorporation"]


@pytest.mark.parametrize("ep", ATTACKS, ids=IDS)
def test_null_agent_triggers_nothing(ep):
    _, r = run(ep, "D0", NullModel())
    assert not any(r.attack[s] for s in ("write", "incorporation", "activation"))


@pytest.mark.parametrize("ep", [e for e in ATTACKS if e["attack"]["timing"] == "cross_run"],
                         ids=[e["id"] for e in ATTACKS if e["attack"]["timing"] == "cross_run"])
def test_cross_run_victim_activation_happens_in_a_later_run_than_the_write(ep):
    _, r = run(ep, "D0", ScriptedModel(ep["victim"]))
    assert r.attack["write"]
    assert any(a["later_run_than_first_write"] for a in r.attack["incorporating_actions"]), r.attack


@pytest.mark.parametrize("ep", ATTACKS, ids=IDS)
def test_d2_deny_all_stops_gated_victims(ep):
    """Under D2 with every approval denied, attacks that need a gated action cannot activate."""
    world, r = run(ep, "D2", ScriptedModel(ep["victim"]), DenyAll())
    gated = world.scalar("SELECT COUNT(*) FROM approvals")
    if gated:
        assert not r.attack["activation"], r.attack["activation_checks"]
