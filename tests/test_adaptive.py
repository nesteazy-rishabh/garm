import asyncio
import json

from garm_bench.adaptive import adapt, apply_rewrite, controlled, keeps_required_values
from garm_bench.approvals import ApproveAll
from garm_bench.defences import get_defence
from garm_bench.episodes import load_episodes
from garm_bench.models.base import ModelTurn
from garm_bench.models.scripted import NullModel, ScriptedModel

SEED = next(e for e in load_episodes("attack") if e["id"] == "attack-c1-payment-redirect-01")


class FakeAttacker:
    model_id = "fake-attacker"

    def __init__(self, drop_values=False):
        self.calls = 0
        self.drop = drop_values

    async def generate(self, system, tools, messages, max_tokens):
        self.calls += 1
        assert messages[0]["role"] == "user" and messages[-1]["role"] == "user"
        i = controlled(SEED)[0]
        body = SEED["events"][i]["message"]["body"]
        if self.drop:
            for v in SEED["attack"]["indicators"]:
                body = body.replace(v, "XXXX")
        rewrite = {"messages": [{"index": i, "subject": f"Rewrite {self.calls}", "body": body}], "rationale": "test"}
        return ModelTurn(text=json.dumps(rewrite), tool_calls=[], stop_reason="end_turn")


def run(target, attacker, iterations=3):
    return asyncio.run(adapt(SEED, lambda: get_defence("D0"), target, attacker, ApproveAll, iterations, "none"))


def test_only_attacker_events_are_controlled():
    idx = controlled(SEED)
    assert idx and all(SEED["events"][i]["attacker"] for i in idx)
    assert any(not ev["attacker"] for ev in SEED["events"])


def test_loop_runs_all_iterations_when_attack_never_lands():
    out = run(NullModel(), FakeAttacker())
    assert out["attempts_used"] == 4 and not out["success"]
    assert out["attempts"][1]["messages"][0]["subject"] == "Rewrite 1"


def test_rewrites_that_drop_required_values_are_rejected():
    out = run(NullModel(), FakeAttacker(drop_values=True))
    assert all(a["messages"][0]["subject"] == SEED["events"][controlled(SEED)[0]]["message"]["subject"]
               for a in out["attempts"])


def test_loop_stops_on_activation():
    out = run(ScriptedModel(SEED["victim"]), FakeAttacker())
    assert out["success"] and out["attempts_used"] == 1


def test_apply_rewrite_does_not_touch_genuine_events():
    i = controlled(SEED)[0]
    ep = apply_rewrite(SEED, {"messages": [{"index": i, "body": "x"}, {"index": 1 - i, "body": "hijack"}]}, [i])
    assert ep["events"][1 - i]["message"]["body"] != "hijack"
    assert not keeps_required_values(SEED, ep, [i])
