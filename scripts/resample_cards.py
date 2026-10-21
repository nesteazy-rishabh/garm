"""Resampled card readers (pre-registered in docs/approver-models.md): judge each distinct card in scope several
times per reader and prompt, store every sample, and report per-sample, per-episode and expected per-run rates.

Scopes: `core` is the honest twin (l4) and family X under D2, D4 and D5 with the on-file callback; `extension` adds
the other legitimate-update families (l1-l3) under the same defences, and customer email changes (l2) under D5 with
the known-good callback (D6).

    uv run python scripts/resample_cards.py run --scope extension --samples 5 --max-spend 2.80   # paid
    uv run python scripts/resample_cards.py report                                              # offline
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from garm_bench.analysis import CARD_PROMPTS, INFRA_ERRORS, judge_cards
from garm_bench.approvers import MODELS, effective_attack_success, expected_pass
from garm_bench.budget import Ledger, check_prices_fresh, load_prices
from garm_bench.episodes import DATA_DIR, load_episodes
from garm_bench.models.anthropic_model import AnthropicModel
from garm_bench.stats import wilson

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "resample"
LISTS = ["runs/main/dirs.txt", "runs/sonnet/dirs.txt", "runs/d6/dirs.txt"]
READERS = ("claude-haiku-5-5", "claude-sonnet-5-5")
PROMPTS = ("strict", "base_rate")
CORE = {("on_file", d, f) for d in ("D2", "D4", "D5") for f in ("l4", "x")}
SCOPES = {
    "core": CORE,
    "extension": CORE | {("on_file", d, f) for d in ("D2", "D4", "D5") for f in ("l1", "l2", "l3")}
    | {("known_good", "D5", "l2")},
}


def runs():
    """(group, run row) for every valid legitimate-update and family-X run, where group is (policy, defence, family).
    Rows with infrastructure errors are skipped, as in `garm analyze`."""
    for name in LISTS:
        for d in [line.strip() for line in (ROOT / name).read_text().splitlines() if line.strip()]:
            cfg = json.loads((ROOT / d / "config.json").read_text())
            for line in (ROOT / d / "results.regraded.jsonl").read_text().splitlines():
                r = json.loads(line)
                if any((j.get("error") or "").startswith(INFRA_ERRORS) for j in r["job_runs"]):
                    continue
                fam = r["episode_id"].split("-")[1]
                if cfg["split"] == "legit" or fam == "x":
                    r["_agent"] = cfg["model"]
                    yield (cfg.get("callback_policy", "on_file"), cfg["defence"], fam), r


def collect(scope: set) -> dict:
    """{group: {card text: [episode ids]}} for the groups in scope."""
    groups: dict = defaultdict(dict)
    for g, r in runs():
        if g in scope:
            for a in r["approvals"]:
                eps = groups[g].setdefault(a["card_text"], [])
                if r["episode_id"] not in eps:
                    eps.append(r["episode_id"])
    return groups


def run(args) -> None:
    load_dotenv(ROOT / ".env")
    prices, meta = load_prices(DATA_DIR / "prices.json")
    check_prices_fresh(meta)
    OUT.mkdir(parents=True, exist_ok=True)
    cards = sorted({c for g in collect(SCOPES[args.scope]).values() for c in g})
    print(f"{len(cards)} cards in scope {args.scope}, {args.samples} samples each")
    ledger = Ledger(ROOT / "runs" / "ledger.jsonl", args.max_spend, label="resample-cards")
    try:
        for reader in READERS:
            client = AnthropicModel(reader, prices[reader], ledger, effort="low")
            for prompt in PROMPTS:
                path = OUT / f"{reader}-{prompt}.json"
                done = json.loads(path.read_text()) if path.exists() else {}
                for s in range(args.samples):
                    todo = [c for c in cards if len(done.get(c, [])) <= s]
                    if not todo:
                        continue
                    for card, ok in asyncio.run(judge_cards(todo, client, CARD_PROMPTS[prompt])).items():
                        done.setdefault(card, []).append(ok)
                    path.write_text(json.dumps(done, indent=1))
                    print(f"{reader} {prompt} sample {s + 1}: {len(todo)} cards")
    finally:
        ledger.close()
    print(json.dumps(ledger.summary()))


def _rate(k: int, n: int) -> str:
    _, lo, hi = wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.0f}%, {100 * lo:.0f}–{100 * hi:.0f})"


def report(_args) -> None:
    """Card-level rates per group, and run-level outcomes with the repo's own definitions (approvers.py): false blocks
    are 1 − mean expected pass over runs that passed under approve-all, counting only executed cards that are not on an
    attack path; laundered success is the mean of effective attack success, counting only executed attack-path cards
    on runs where the attack activated. Each card contributes its sampled approval rate."""
    groups = collect(SCOPES["extension"])
    episodes = {e["id"]: e for split in ("legit", "attack") for e in load_episodes(split)}
    oracle = MODELS["oracle"]  # unused when decisions are given
    cards_out, runs_out = [], []
    for reader in READERS:
        for prompt in PROMPTS:
            decisions = json.loads((OUT / f"{reader}-{prompt}.json").read_text())
            approve = {c: sum(v) / len(v) for c, v in decisions.items()}
            for g in sorted(groups):
                sel = {c: decisions[c] for c in groups[g]}
                samples = [ok for v in sel.values() for ok in v]
                cards_out.append({
                    "reader": reader, "prompt": prompt, "policy": g[0], "defence": g[1], "family": g[2],
                    "cards": len(sel), "rejected_samples": _rate(sum(not ok for ok in samples), len(samples)),
                    "approved_samples": sum(samples),
                    "cards_with_unanimous_samples": f"{sum(len(set(v)) == 1 for v in sel.values())}/{len(sel)}"})
            by = defaultdict(list)
            for g, r in runs():
                if g in groups:
                    by[(r["_agent"], *g)].append(r)
            for (agent, policy, defence, fam), rs in sorted(by.items()):
                row = {"reader": reader, "prompt": prompt, "agent": agent, "policy": policy, "defence": defence,
                       "family": fam}
                if fam == "x":
                    vals = [effective_attack_success(r, episodes[r["episode_id"]], oracle, llm_decisions=approve)
                            for r in rs]
                    row |= {"laundered_success": round(sum(vals) / len(vals), 3), "runs": len(rs),
                            "activated_runs": sum(bool(r["attack"]["activation"]) for r in rs)}
                else:
                    passed = [r for r in rs if r["passed"]]
                    vals = [expected_pass(r, episodes[r["episode_id"]], oracle, llm_decisions=approve) for r in passed]
                    row |= {"false_block": round(1 - sum(vals) / len(vals), 3) if vals else None,
                            "passing_runs": len(passed)}
                runs_out.append(row)
    (OUT / "report.json").write_text(json.dumps({"cards": cards_out, "runs": runs_out}, indent=1))
    (OUT / "per-card.json").write_text(json.dumps({
        f"{reader}/{prompt}": {c: sum(v) / len(v) for c, v in json.loads((OUT / f"{reader}-{prompt}.json")
                                                                         .read_text()).items()}
        for reader in READERS for prompt in PROMPTS}, indent=1))
    for r in runs_out:
        print(" | ".join(str(v) for v in r.values()))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--scope", choices=sorted(SCOPES), default="core")
    r.add_argument("--samples", type=int, default=5)
    r.add_argument("--max-spend", type=float, required=True)
    r.set_defaults(fn=run)
    sub.add_parser("report").set_defaults(fn=report)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
