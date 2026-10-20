"""D4 and D5 validated offline with scripted proposals (the oracle and victim scripts, write steps only)."""

import asyncio

import pytest

from garm_bench.approvals import ApproveAll, OracleApprover
from garm_bench.defences import get_defence
from garm_bench.episodes import load_episodes
from garm_bench.models.scripted import ScriptedDraftModel
from garm_bench.runner import run_episode

USEFUL = load_episodes("benign") + load_episodes("legit")
ATTACKS = load_episodes("attack")
DIRECT = [e for e in ATTACKS if not e["attack"].get("laundered_by_policy")]
LAUNDERED = [e for e in ATTACKS if e["attack"].get("laundered_by_policy")]


def run(ep, defence, script, approver=None):
    return asyncio.run(run_episode(ep, get_defence(defence), ScriptedDraftModel(ep[script]), approver or ApproveAll()))


@pytest.mark.parametrize("defence", ["D4", "D5"])
@pytest.mark.parametrize("ep", USEFUL, ids=[e["id"] for e in USEFUL])
def test_drafts_oracle_does_the_work(ep, defence):
    world, r = run(ep, defence, "oracle")
    rejected = world.all("SELECT tool, card_text FROM action_log WHERE outcome = 'rejected_by_check'")
    assert not rejected, rejected
    assert r.passed, [c for c in r.checks if not c["passed"]]


@pytest.mark.parametrize("ep", DIRECT, ids=[e["id"] for e in DIRECT])
def test_d4_record_check_stops_direct_attacks(ep):
    _, r = run(ep, "D4", "victim")
    assert not r.attack["activation"], r.attack["activation_checks"]


@pytest.mark.parametrize("ep", LAUNDERED, ids=[e["id"] for e in LAUNDERED])
def test_d4_is_laundered_and_its_cards_show_no_provenance(ep):
    world, r = run(ep, "D4", "oracle")
    assert r.attack["activation"], r.attack["activation_checks"]
    assert all("Provenance" not in a["card_text"] for a in world.all("SELECT card_text FROM approvals"))


@pytest.mark.parametrize("ep", LAUNDERED, ids=[e["id"] for e in LAUNDERED])
def test_d5_flags_the_laundered_verification_with_its_provenance(ep):
    world, _ = run(ep, "D5", "oracle")
    cards = [a["card_text"] for a in world.all("SELECT card_text FROM approvals WHERE reason = 'low-authority verification'")]
    assert cards, "D5 should hold the change that rests on the poisoned callback"
    assert any("that number was set" in c and "external" in c for c in cards), cards
    _, blocked = run(ep, "D5", "oracle", OracleApprover())
    assert not blocked.attack["activation"]


def test_d5_needs_fewer_approvals_than_d4_on_routine_work():
    d4 = sum(len(run(e, "D4", "oracle")[1].approvals) for e in load_episodes("benign"))
    d5 = sum(len(run(e, "D5", "oracle")[1].approvals) for e in load_episodes("benign"))
    assert d5 < d4, (d4, d5)
