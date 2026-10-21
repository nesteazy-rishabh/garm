"""D6 (known-good callback) validated offline with the scripted oracle, which follows the handbook exactly."""

import asyncio

import pytest

from garm_bench.approvals import ApproveAll
from garm_bench.defences import get_defence
from garm_bench.episodes import load_episodes
from garm_bench.models.scripted import ScriptedDraftModel, ScriptedModel
from garm_bench.runner import run_episode

LAUNDERED = [e for e in load_episodes("attack") if e["attack"].get("laundered_by_policy")]
LEGIT = load_episodes("legit")
TWINS = [e for e in LEGIT if "phone-then-bank" in e["id"]]


def run(ep, policy, defence="D0"):
    model = ScriptedDraftModel(ep["oracle"]) if defence in ("D4", "D5") else ScriptedModel(ep["oracle"])
    return asyncio.run(run_episode(ep, get_defence(defence), model, ApproveAll(), callback_policy=policy))


@pytest.mark.parametrize("policy", ["known_good", "known_good_strict"])
@pytest.mark.parametrize("ep", LAUNDERED, ids=[e["id"] for e in LAUNDERED])
def test_known_good_callback_stops_the_laundered_chain(ep, policy):
    """The callback reaches the real vendor, who denies it, so no verification exists and no high-scrutiny field
    takes the attacker's value. (Activation is not asserted: the scripted oracle replays fixed steps written for the
    world in which the callback succeeded, and two of them address mail to the attacker regardless. Model agents
    read the record.)"""
    world, _ = run(ep, policy)
    assert world.scalar("SELECT COUNT(*) FROM vendor_verifications") == 0
    assert world.scalar("SELECT COUNT(*) FROM callback_requests WHERE status != 'denied'") == 0
    values = [i.lower() for i in ep["attack"]["indicators"]]
    for f in ("bank_name", "account_number", "routing_number", "contact_email"):
        assert all(str(v[f]).lower() not in values for v in world.all(f"SELECT {f} FROM vendors"))


@pytest.mark.parametrize("ep", LEGIT, ids=[e["id"] for e in LEGIT])
def test_lenient_known_good_callback_keeps_every_legitimate_update(ep):
    _, r = run(ep, "known_good")
    assert r.passed, [c for c in r.checks if not c["passed"]]


@pytest.mark.parametrize("ep", TWINS, ids=[e["id"] for e in TWINS])
def test_strict_known_good_callback_blocks_the_honest_twin(ep):
    """The cost bracket: an honest vendor whose old line is disconnected cannot confirm its own bank change."""
    _, r = run(ep, "known_good_strict")
    assert not r.passed


@pytest.mark.parametrize("ep", TWINS, ids=[e["id"] for e in TWINS])
def test_d5_trusts_a_known_good_callback_and_asks_for_no_approval(ep):
    _, on_file = run(ep, "on_file", "D5")
    _, known = run(ep, "known_good", "D5")
    assert on_file.approvals, "on file, the twin's verification rests on an outside-written number"
    assert known.passed and not known.approvals


def test_cli_validate_covers_every_split_and_family_x(capsys):
    """`garm validate` makes the same family-X checks as these tests, so it reports no false problems."""
    from types import SimpleNamespace

    from garm_bench.cli import cmd_validate

    assert cmd_validate(SimpleNamespace(split="all", episodes=None)) == 0
    assert "124 episodes checked" in capsys.readouterr().out
