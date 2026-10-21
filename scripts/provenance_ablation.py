"""Provenance ablation and positive control for the card readers (pre-registered in docs/approver-models.md).

Ablation: D5 twin (l4) and family-X cards with the provenance block removed. Control: the verified bank changes (l1),
each with its real D4 change card plus the high-authority provenance D5 computes for it from the run's saved database.
Every card is judged several times per reader and prompt; per-sample rejection rates are compared with the
resampled cards that kept their provenance (runs/resample/).

    uv run python scripts/provenance_ablation.py run --samples 5 --max-spend 1.50     # paid
    uv run python scripts/provenance_ablation.py report                               # offline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import resample_cards as rc
from dotenv import load_dotenv

from garm_bench import authority as A
from garm_bench.analysis import CARD_PROMPTS, judge_cards
from garm_bench.budget import Ledger, check_prices_fresh, load_prices
from garm_bench.defences import _with_chain
from garm_bench.episodes import DATA_DIR, load_episodes
from garm_bench.models.anthropic_model import AnthropicModel
from garm_bench.stats import wilson
from garm_bench.world import World

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "ablation"
MARK = "\nProvenance:\n"


def _main_dir(split_defence: str) -> Path:
    return ROOT / next(line.strip() for line in (ROOT / "runs/main/dirs.txt").read_text().splitlines()
                       if f"-{split_defence}-" in line)


def control_cards() -> dict:
    """{card: episode id}: each l1 episode's D4 bank-change card with the provenance D5 computed for that change."""
    episodes = {e["id"]: e for e in load_episodes("legit")}
    base = {}
    for line in (_main_dir("legit-D4") / "results.regraded.jsonl").read_text().splitlines():
        r = json.loads(line)
        if "-l1-" in r["episode_id"] and r["episode_id"] not in base:
            cards = [a["card_text"] for a in r["approvals"] if "bank_name" in a["card_text"]]
            if cards:
                base[r["episode_id"]] = cards[0]
    out = {}
    for eid, card in sorted(base.items()):
        vendor = re.search(r"on vendor (V\d+)", card).group(1)
        world = World.open_saved(str(_main_dir("legit-D5") / "transcripts" / f"{eid}--0.db"),
                                 datetime.fromisoformat(episodes[eid]["end"]))
        parts = []
        for field in ("bank_name", "account_number", "routing_number"):
            v = world.one("SELECT * FROM vendor_verifications WHERE vendor_id = ? AND field = ? ORDER BY id DESC "
                          "LIMIT 1", (vendor, field))
            parts.append(A.verification_authority(world, v))
        auth = A.combine(*parts)
        assert not auth.low, (eid, auth)
        out[_with_chain(card, auth)] = eid
    return out


def stripped_cards() -> dict:
    """{stripped card: family} for the D5 twin and family-X cards."""
    groups = rc.collect(rc.SCOPES["core"])
    return {c.split(MARK)[0]: fam for (pol, d, fam), cards in groups.items() if d == "D5" for c in cards}


def run(args) -> None:
    load_dotenv(ROOT / ".env")
    prices, meta = load_prices(DATA_DIR / "prices.json")
    check_prices_fresh(meta)
    OUT.mkdir(parents=True, exist_ok=True)
    sets = {"control": control_cards(), "stripped": stripped_cards()}
    (OUT / "cards.json").write_text(json.dumps(sets, indent=1))
    cards = sorted({c for s in sets.values() for c in s})
    print(f"{len(sets['control'])} control cards, {len(sets['stripped'])} stripped cards, {args.samples} samples")
    ledger = Ledger(ROOT / "runs" / "ledger.jsonl", args.max_spend, label="provenance-ablation")
    try:
        for reader in rc.READERS:
            client = AnthropicModel(reader, prices[reader], ledger, effort="low")
            for prompt in rc.PROMPTS:
                path = OUT / f"{reader}-{prompt}.json"
                done = json.loads(path.read_text()) if path.exists() else {}
                for s in range(args.samples):
                    todo = [c for c in cards if len(done.get(c, [])) <= s]
                    if todo:
                        for card, ok in asyncio.run(judge_cards(todo, client, CARD_PROMPTS[prompt])).items():
                            done.setdefault(card, []).append(ok)
                        path.write_text(json.dumps(done, indent=1))
    finally:
        ledger.close()
    print(json.dumps(ledger.summary()))


def _rej(samples: list[bool]) -> str:
    k, n = sum(not ok for ok in samples), len(samples)
    _, lo, hi = wilson(k, n)
    return f"{k}/{n} rejected ({100 * k / n:.0f}%, {100 * lo:.0f}–{100 * hi:.0f})"


def report(_args) -> None:
    sets = json.loads((OUT / "cards.json").read_text())
    with_prov = rc.collect(rc.SCOPES["core"])
    rows = []
    for reader in rc.READERS:
        for prompt in rc.PROMPTS:
            ab = json.loads((OUT / f"{reader}-{prompt}.json").read_text())
            rs = json.loads((rc.OUT / f"{reader}-{prompt}.json").read_text())
            row = {"reader": reader, "prompt": prompt}
            for fam in ("l4", "x"):
                row[f"{fam}_with_provenance"] = _rej([ok for c in with_prov[("on_file", "D5", fam)] for ok in rs[c]])
                row[f"{fam}_stripped"] = _rej([ok for c, f in sets["stripped"].items() if f == fam for ok in ab[c]])
            row["control_high_authority"] = _rej([ok for c in sets["control"] for ok in ab[c]])
            rows.append(row)
    (OUT / "report.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(json.dumps(r, indent=1))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--samples", type=int, default=5)
    r.add_argument("--max-spend", type=float, required=True)
    r.set_defaults(fn=run)
    sub.add_parser("report").set_defaults(fn=report)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
