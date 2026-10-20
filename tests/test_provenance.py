"""The standalone provenance module agrees with D5's authority labels on real episode states, and behaves as
documented on the laundered-callback example."""

import asyncio

import pytest

from garm_bench.approvals import ApproveAll
from garm_bench.authority import HIGH_SCRUTINY, field_authority
from garm_bench.defences import get_defence
from garm_bench.episodes import load_episodes
from garm_bench.models.scripted import ScriptedModel
from garm_bench.provenance import HIGH, LOW, Policy, ProvenanceLog
from garm_bench.runner import run_episode

EPISODES = [e for e in load_episodes("attack") if e["attack"].get("laundered_by_policy")] + load_episodes("legit")


def to_log(world) -> ProvenanceLog:
    log = ProvenanceLog()
    for w in world.all("SELECT * FROM field_writes ORDER BY id"):
        log.record(f"{w['table_name']}:{w['row_id']}", w["field"], w["new_value"], w["writer_kind"], w["writer"],
                   source=f"message #{w['source_message_id']}" if w["source_message_id"] else None, at=w["at"])
    for v in world.all("SELECT * FROM vendor_verifications ORDER BY id"):
        via = (f"vendors:{v['vendor_id']}", "phone", v["via_phone"]) if v["via_phone"] else None
        log.attest(f"vendors:{v['vendor_id']}", v["field"], v["value"], by=v["verified_by"], via=via,
                   at=v["verified_at"])
    return log


@pytest.mark.parametrize("policy", ["on_file", "known_good"])
@pytest.mark.parametrize("script", ["oracle", "victim"])
@pytest.mark.parametrize("ep", EPISODES, ids=[e["id"] for e in EPISODES])
def test_matches_d5_authority_on_every_vendor_field(ep, script, policy):
    if script not in ep:
        pytest.skip("no victim script for legitimate updates")
    world, _ = asyncio.run(run_episode(ep, get_defence("D0"), ScriptedModel(ep[script]), ApproveAll(),
                                       callback_policy=policy))
    log, rules = to_log(world), Policy(trusted={"seed", "staff"}, attestable=set(HIGH_SCRUTINY))
    for v in world.all("SELECT id FROM vendors"):
        for f in (*HIGH_SCRUTINY, "phone", "contact_name"):
            expected = field_authority(world, "vendors", v["id"], f).level
            assert rules.authority(log, f"vendors:{v['id']}", f).level == expected, (v["id"], f)


def test_docstring_example_is_low_and_explains_why():
    log = ProvenanceLog()
    log.record("vendor:V101", "phone", "555-0100", "seed", "seed", at="2026-01-01")
    log.record("vendor:V101", "phone", "555-0111", "agent", "update_records",
               source="email from office@vendor.example", at="2026-03-02T09:00")
    log.attest("vendor:V101", "account_number", "7123941702", by="staff:ap_clerk",
               via=("vendor:V101", "phone", "555-0111"), at="2026-03-02T13:00")
    log.record("vendor:V101", "account_number", "7123941702", "agent", "update_records",
               source="email from office@vendor.example", at="2026-03-02T14:00")
    rules = Policy(trusted={"seed", "staff"}, attestable={"account_number"})
    a = rules.authority(log, "vendor:V101", "account_number")
    assert a.level == LOW and "555-0111" in a.card() and "office@vendor.example" in a.card()
    # The same verification made through the original number is high authority.
    log.attest("vendor:V101", "account_number", "7123941702", by="staff:ap_clerk",
               via=("vendor:V101", "phone", "555-0100"), at="2026-03-02T13:30")
    assert rules.authority(log, "vendor:V101", "account_number").level == HIGH


def test_attestation_cycles_terminate_as_low():
    log = ProvenanceLog()
    log.record("e", "a", "1", "agent", "job", at="1")
    log.record("e", "b", "2", "agent", "job", at="1")
    log.attest("e", "a", "1", by="staff", via=("e", "b", "2"), at="1")
    log.attest("e", "b", "2", by="staff", via=("e", "a", "1"), at="1")
    assert Policy(attestable={"a", "b"}).authority(log, "e", "a").level == LOW
