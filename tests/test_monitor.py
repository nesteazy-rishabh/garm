import asyncio

from garm_bench.approvals import ApproveAll
from garm_bench.defences import get_defence
from garm_bench.episodes import load_episodes
from garm_bench.models.base import ModelTurn
from garm_bench.models.scripted import ScriptedModel
from garm_bench.runner import run_episode


class FakeMonitor:
    """Escalates any action whose prompt mentions one of the given strings; counts calls."""

    model_id = "fake-monitor"

    def __init__(self, flags, garbled=False):
        self.flags = [f.lower() for f in flags]
        self.garbled = garbled
        self.calls = 0

    async def generate(self, system, tools, messages, max_tokens):
        self.calls += 1
        if self.garbled:
            return ModelTurn(text="I think it's fine", tool_calls=[], stop_reason="end_turn")
        text = messages[-1]["text"].lower()
        bad = any(f in text for f in self.flags)
        decision = "escalate" if bad else "allow"
        return ModelTurn(text=f'{{"decision": "{decision}", "reason": "test"}}', tool_calls=[], stop_reason="end_turn")


def run(ep, monitor, script):
    return asyncio.run(run_episode(ep, get_defence("D3", monitor), ScriptedModel(ep[script]), ApproveAll()))


def test_monitor_escalates_ungated_actions_it_flags():
    ep = next(e for e in load_episodes("attack") if e["id"] == "attack-s4-fake-payable-01")
    mon = FakeMonitor(ep["attack"]["indicators"])
    world, _ = run(ep, mon, "victim")
    assert mon.calls > 0
    assert world.scalar("SELECT COUNT(*) FROM approvals WHERE reason = 'monitor escalation'") >= 1


def test_monitor_that_allows_everything_adds_no_approvals_beyond_d2():
    ep = load_episodes("benign")[0]
    world, r = run(ep, FakeMonitor([]), "oracle")
    assert r.passed
    assert world.scalar("SELECT COUNT(*) FROM approvals WHERE reason = 'monitor escalation'") == 0


def test_unreadable_monitor_output_escalates():
    ep = load_episodes("benign")[0]
    world, _ = run(ep, FakeMonitor([], garbled=True), "oracle")
    assert world.scalar("SELECT COUNT(*) FROM approvals WHERE reason = 'monitor escalation'") >= 1
