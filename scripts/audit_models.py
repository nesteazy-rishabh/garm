"""Independent audit of the write-up by two models that see the same evidence and not each other's answers.

Each auditor gets the README, the docs, the core grading and analysis code, one family-X episode and the analysis
files, and is asked to check every quantitative claim against the data, flag overclaims, and judge usefulness and
originality. Outputs go to runs/audit/<model>.json (parsed) and <model>.txt (raw).

    uv run python scripts/audit_models.py --max-spend 3.00 [--tag final-]           # paid
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from garm_bench.budget import Ledger, check_prices_fresh, load_prices
from garm_bench.episodes import DATA_DIR
from garm_bench.models.anthropic_model import AnthropicModel

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "audit"
AUDITORS = ("claude-opus-5-5", "claude-sonnet-5-5")
DOCS = ["README.md", "docs/related-work.md", "docs/threat-model.md", "docs/design-laundering.md", "docs/design-d6.md",
        "docs/approver-models.md", "docs/results-main.md", "docs/results-d6.md", "docs/results-adaptive.md",
        "docs/results-pilot.md", "docs/grader-changes.md", "docs/dataset-card.md", "docs/provenance.md",
        "docs/defence-api.md"]
CODE = ["src/garm_bench/jobs.py", "src/garm_bench/graders.py", "src/garm_bench/staff.py",
        "src/garm_bench/authority.py", "src/garm_bench/approvers.py", "src/garm_bench/analysis.py",
        "data/episodes/attack/attack-x-laundered-callback-01.json"]
DATA = ["runs/main/analysis-regraded.json", "runs/main/analysis-readers.json", "runs/sonnet/analysis-regraded.json",
        "runs/sonnet/analysis-readers.json", "runs/d6/analysis.json", "runs/d6/analysis-sonnet.json",
        "runs/resample/report.json", "runs/ablation/report.json", "docs/figures/headline.csv"]

SYSTEM = """You are an independent reviewer of an AI-agent security benchmark. You are reviewing it for two audiences
at once: a workshop programme committee of AI-safety researchers, and a technical hiring panel at an AI lab. You are
paid for finding problems, not for praise. Another reviewer is auditing the same material independently.

Rules:
- Check every quantitative claim in README.md and docs/results-*.md against the analysis files provided. Recompute
  where you can (rates, counts, ranges, rounding). If a number cannot be checked from what you were given, say
  "cannot_check"; do not guess.
- Separate measured results from modelled ones and from results that follow from the environment's design.
- Do not invent prior work. Judge originality from your own knowledge and say how confident you are.
- Be specific: quote the claim, give the file, and point to the field in the data that confirms or contradicts it.

Return only one JSON object, no prose around it, with this shape:
{
 "claims": [{"file": str, "claim": str, "status": "verified" | "mismatch" | "unsupported" | "overclaim" | "cannot_check",
             "evidence": str, "fix": str}],
 "internal_inconsistencies": [{"where": str, "problem": str, "fix": str}],
 "methodology": [{"severity": "blocking" | "major" | "minor", "problem": str, "fix": str}],
 "usefulness": {"who_would_use_it": str, "for_what_decision": str, "score_1_to_10": int, "why": str},
 "originality": {"what_is_new": [str], "what_is_not": [str], "confidence": "low" | "medium" | "high"},
 "something_here": {"answer": "yes" | "maybe" | "no", "why": str, "lead_with": str},
 "top_fixes": [str]
}
Cover every quantitative claim in the README; for the results docs, cover at least the headline tables.
Keep each "evidence" and "fix" under 40 words."""


def bundle() -> str:
    parts = []
    for group, paths in (("DOCUMENT", DOCS), ("CODE", CODE), ("DATA", DATA)):
        for rel in paths:
            parts.append(f'<file kind="{group}" path="{rel}">\n{(ROOT / rel).read_text()}\n</file>')
    return "\n\n".join(parts)


async def audit_all(clients: dict, text: str, max_tokens: int) -> dict:
    async def one(model, client):
        turn = await client.generate(SYSTEM, [], [{"role": "user", "text": text}], max_tokens)
        return model, turn

    return dict(await asyncio.gather(*(one(m, c) for m, c in clients.items())))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--max-spend", type=float, required=True)
    p.add_argument("--tag", default="", help="prefix for the output files, e.g. final-")
    p.add_argument("--models", nargs="+", help="run only these model ids instead of the default two")
    p.add_argument("--max-tokens", type=int, default=48000, help="output budget per reviewer")
    p.add_argument("--no-thinking", action="store_true", help="disable extended thinking (frees the budget for the JSON)")
    args = p.parse_args()
    load_dotenv(ROOT / ".env")
    prices, meta = load_prices(DATA_DIR / "prices.json")
    check_prices_fresh(meta)
    OUT.mkdir(parents=True, exist_ok=True)
    text = bundle()
    print(f"bundle: {len(text):,} characters")
    ledger = Ledger(ROOT / "runs" / "ledger.jsonl", args.max_spend, label="audit-models")
    try:
        models = args.models or AUDITORS
        clients = {m: AnthropicModel(m, prices[m], ledger, effort="high", thinking=not args.no_thinking, stream=True)
                   for m in models}
        for model, turn in asyncio.run(audit_all(clients, text, args.max_tokens)).items():
            (OUT / f"{args.tag}{model}.txt").write_text(turn.text)
            try:
                parsed = json.loads(turn.text[turn.text.index("{"): turn.text.rindex("}") + 1])
                (OUT / f"{args.tag}{model}.json").write_text(json.dumps(parsed, indent=1))
                print(f"{model}: {len(parsed.get('claims', []))} claims checked, stop={turn.stop_reason}, "
                      f"US${turn.cost_usd:.2f}")
            except ValueError:
                print(f"{model}: unparseable output (stop={turn.stop_reason}); raw text saved")
    finally:
        ledger.close()
    print(json.dumps(ledger.summary()))


if __name__ == "__main__":
    main()
