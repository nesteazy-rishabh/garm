"""Cross-company audit panel: non-Anthropic reviewers see the exact same evidence bundle and reviewer prompt as the
two Claude auditors (scripts/audit_models.py), but from different model families and vendors.

The point is to control for same-family reviewer bias: a Claude-built, Claude-audited artifact reviewed only by Claude
shares its blind spots by construction (self-preference bias, Panickssery et al. NeurIPS 2024). Models from OpenAI,
xAI, Moonshot, Meta, DeepSeek and Alibaba do not. Spend is metered on a SEPARATE ledger capped at US$4, so this run
cannot touch the Anthropic budget.

    uv run python scripts/audit_panel.py --max-spend 4.00 [--tag panel-]        # paid, needs OPENROUTER_API_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from dotenv import load_dotenv

import audit_models as am
from garm_bench.budget import Ledger, check_prices_fresh, load_prices
from garm_bench.episodes import DATA_DIR
from garm_bench.models.openrouter_model import OpenRouterModel

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "audit"

# One reviewer per vendor/class. Every id and context window was confirmed live on 2026-10-21; all read the full bundle.
PANEL = [
    "openai/gpt-6.1-sol",          # OpenAI, frontier (user-requested)
    "openai/gpt-6-luna",           # OpenAI, efficient (newest Luna; no gpt-6.1-luna exists)
    "x-ai/grok-4.3",               # xAI
    "moonshotai/kimi-k2.5",        # Moonshot (user-requested)
    "meta-llama/llama-4-maverick", # Meta
    "deepseek/deepseek-v3.2",      # DeepSeek
    "qwen/qwen3.5-plus-02-15",     # Alibaba
]


def _slug(model_id: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", model_id.lower())


async def run_panel(clients: dict, text: str) -> dict:
    async def one(model, client):
        try:
            turn = await client.generate(am.SYSTEM, [], [{"role": "user", "text": text}], 16000)
            return model, turn
        except Exception as e:  # one vendor failing must not sink the panel
            return model, e

    return dict(await asyncio.gather(*(one(m, c) for m, c in clients.items())))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--max-spend", type=float, required=True)
    p.add_argument("--tag", default="panel-", help="prefix for the output files")
    p.add_argument("--models", nargs="+", help="run only these model ids instead of the full PANEL")
    args = p.parse_args()
    load_dotenv(ROOT / ".env")
    prices, meta = load_prices(DATA_DIR / "prices-openrouter.json")
    check_prices_fresh(meta)
    OUT.mkdir(parents=True, exist_ok=True)
    panel = args.models or PANEL
    text = am.bundle()
    print(f"bundle: {len(text):,} characters; {len(panel)} reviewers across {len({m.split('/')[0] for m in panel})} vendors")
    ledger = Ledger(ROOT / "runs" / "ledger-openrouter.jsonl", args.max_spend, label="audit-panel")
    try:
        clients = {m: OpenRouterModel(m, prices[m], ledger) for m in panel}
        for model, turn in asyncio.run(run_panel(clients, text)).items():
            slug = _slug(model)
            if isinstance(turn, Exception):
                print(f"{model}: FAILED ({type(turn).__name__}: {str(turn)[:120]})")
                (OUT / f"{args.tag}{slug}.error.txt").write_text(f"{type(turn).__name__}: {turn}")
                continue
            (OUT / f"{args.tag}{slug}.txt").write_text(turn.text)
            try:
                parsed = json.loads(turn.text[turn.text.index("{"): turn.text.rindex("}") + 1])
                (OUT / f"{args.tag}{slug}.json").write_text(json.dumps(parsed, indent=1))
                print(f"{model}: {len(parsed.get('claims', []))} claims, something_here="
                      f"{parsed.get('something_here', {}).get('answer')}, stop={turn.stop_reason}, "
                      f"US${turn.cost_usd:.3f}")
            except (ValueError, KeyError):
                print(f"{model}: unparseable output (stop={turn.stop_reason}, US${turn.cost_usd:.3f}); raw text saved")
    finally:
        ledger.close()
    print(json.dumps(ledger.summary()))


if __name__ == "__main__":
    main()
