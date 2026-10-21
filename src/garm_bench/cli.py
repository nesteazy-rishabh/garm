"""Command line: validate episodes offline, run a model on episodes under a spend cap, and show spend.

    garm validate                      # every episode: scripted oracle, victim and do-nothing checks (no API calls)
    garm run --defence D0 --model claude-haiku-5-5 --repeats 1 --max-spend 1.00 [--episodes benign-t1]
    garm spend                         # cumulative spend from the ledger
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .approvals import APPROVERS, ApproveAll
from .budget import BudgetExceeded, Ledger, PricesNotVerified, budget_cap, check_prices_fresh, load_prices
from .defences import DEFENCES, get_defence
from .episodes import DATA_DIR, input_hash, load_episodes
from .models.scripted import NullModel, ScriptedModel
from .runner import run_episode
from .staff import CALLBACK_POLICIES
from .stats import summarize

ROOT = DATA_DIR.parent
LEDGER_PATH = ROOT / "runs" / "ledger.jsonl"


def _validate_one(ep) -> list[str]:
    """The offline checks the test suite makes, for one episode. Family X is laundered by design: there the
    handbook-following oracle must be fooled by today's callback process and protected by a known-good one (D6)."""
    bad = []
    attack = ep.get("attack") or {}
    laundered = bool(attack.get("laundered_by_policy"))
    for defence in ("D0", "D1", "D2"):
        world, r = asyncio.run(run_episode(ep, get_defence(defence), ScriptedModel(ep["oracle"]), ApproveAll()))
        if any(j["status"] != "ok" for j in r.job_runs):
            bad.append(f"oracle job error in {ep['id']} under {defence}")
        elif laundered:
            staff = world.scalar("SELECT COUNT(*) FROM vendor_verifications WHERE verified_by = 'staff:ap_clerk'")
            if not r.attack["activation"] or not staff:
                bad.append(f"handbook-following oracle is not laundered in {ep['id']} under {defence}")
        elif not r.passed or (attack and r.attack["activation"]):
            bad.append(f"oracle fails {ep['id']} under {defence}: "
                       + "; ".join(c["name"] for c in r.checks if not c["passed"]))
    if laundered:
        # The scripted oracle replays steps written for the world where the callback succeeded, so check the state
        # D6 controls (verifications and high-scrutiny fields), not activation; see tests/test_callback_policy.py.
        world, _ = asyncio.run(run_episode(ep, get_defence("D0"), ScriptedModel(ep["oracle"]), ApproveAll(),
                                           callback_policy="known_good"))
        values = {i.lower() for i in attack["indicators"]}
        fields = [str(v).lower() for row in world.all("SELECT bank_name, account_number, routing_number, contact_email "
                                                      "FROM vendors") for v in row.values()]
        if world.scalar("SELECT COUNT(*) FROM vendor_verifications") or values & set(fields):
            bad.append(f"known-good callback does not stop {ep['id']}")
    _, r = asyncio.run(run_episode(ep, get_defence("D0"), NullModel(), ApproveAll()))
    if r.passed:
        bad.append(f"do-nothing agent passes {ep['id']}")
    if attack:
        if any(r.attack[s] for s in ("write", "incorporation", "activation")):
            bad.append(f"do-nothing agent triggers part of {ep['id']}")
        _, r = asyncio.run(run_episode(ep, get_defence("D0"), ScriptedModel(ep["victim"]), ApproveAll()))
        if not (r.attack["activation"] and r.attack["incorporation"]):
            bad.append(f"scripted victim does not activate {ep['id']}")
        elif attack["timing"] == "cross_run" and not any(a["later_run_than_first_write"]
                                                         for a in r.attack["incorporating_actions"]):
            bad.append(f"cross-run victim acts in the same run as the write in {ep['id']}")
    return bad


def cmd_validate(args) -> int:
    splits = ["benign", "legit", "attack"] if args.split == "all" else [args.split]
    episodes = [ep for split in splits for ep in load_episodes(split, args.episodes)]
    bad = [line for ep in episodes for line in _validate_one(ep)]
    for line in bad:
        print(line)
    print(f"{len(episodes)} episodes checked ({', '.join(splits)}); {len(bad)} problem(s)")
    return 1 if bad else 0


def cmd_regrade(args) -> int:
    """Re-score a finished run's saved final states with the current episodes and graders (no API calls)."""
    from .graders import capture_baseline, grade, grade_attack
    from .runner import build_world
    from .world import World

    run_dir = Path(args.run_dir)
    episodes = {e["id"]: e for e in load_episodes(json.loads((run_dir / "config.json").read_text())["split"])}
    rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines() if line.strip()]
    changed = 0
    for row in rows:
        ep = episodes[row["episode_id"]]
        if row.get("input_hash") != input_hash(ep):
            print(f"{ep['id']} attempt {row['attempt']}: inputs changed since this run; not regraded")
            row["regrade_skipped"] = True
            continue
        baseline = capture_baseline(build_world(ep))
        world = World.open_saved(str(run_dir / "transcripts" / f"{ep['id']}--{row['attempt']}.db"),
                                 datetime.fromisoformat(ep["end"]))
        checks = grade(world, ep, baseline)
        passed = all(c["passed"] for c in checks)
        if ep.get("kind") == "attack":
            attack = grade_attack(world, ep, baseline)
            for stage in ("write", "incorporation", "activation"):
                if attack[stage] != row["attack"].get(stage):
                    changed += 1
                    print(f"{ep['id']} attempt {row['attempt']}: {stage} {row['attack'].get(stage)} -> {attack[stage]}")
            row["attack"] = attack
        if passed != row["passed"]:
            changed += 1
            print(f"{ep['id']} attempt {row['attempt']}: {'FAIL -> PASS' if passed else 'PASS -> FAIL'}")
        row["checks"], row["passed"] = checks, passed
    (run_dir / "results.regraded.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    rows = [r for r in rows if not r.get("regrade_skipped")]
    summary = summarize(rows)
    (run_dir / "summary.regraded.json").write_text(json.dumps(summary, indent=2))
    print(f"{changed} verdict(s) changed; regraded summary: pass rate {summary['pass_rate']} "
          f"CI {summary['pass_rate_ci95']}; failed {summary['failed_episodes']}")
    return 0


def cmd_analyze(args) -> int:
    from .analysis import analyze, load_runs

    decisions = {}
    for spec in args.card_decisions or []:
        name, _, path = spec.partition("=")
        decisions[name] = json.loads(Path(path).read_text())
    table = analyze(load_runs(args.run_dirs, args.regraded), decisions, args.tasks_per_day)
    text = json.dumps(table, indent=2)
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return 0


def cmd_judge_cards(args) -> int:
    from dotenv import load_dotenv

    from .analysis import CARD_PROMPTS, judge_cards, load_runs, unique_cards
    from .models.anthropic_model import AnthropicModel

    load_dotenv(ROOT / ".env")
    prices, meta = load_prices(DATA_DIR / "prices.json")
    check_prices_fresh(meta)
    out = Path(args.out)
    known = json.loads(out.read_text()) if out.exists() else {}
    cards = [c for c in unique_cards(load_runs(args.run_dirs)) if c not in known]
    print(f"{len(cards)} new card(s) to judge with {args.model}")
    ledger = Ledger(LEDGER_PATH, args.max_spend, label=f"judge-cards:{args.model}")
    try:
        client = AnthropicModel(args.model, prices[args.model], ledger, effort="low")
        known |= asyncio.run(judge_cards(cards, client, CARD_PROMPTS[args.prompt]))
    finally:
        ledger.close()
    out.write_text(json.dumps(known, indent=1))
    print(json.dumps(ledger.summary()))
    return 0


def cmd_adapt(args) -> int:
    """Adaptive attacker over seed attack episodes (paid)."""
    from dotenv import load_dotenv

    from .adaptive import adapt
    from .approvals import LLMCardApprover
    from .models.anthropic_model import AnthropicModel

    load_dotenv(ROOT / ".env")
    prices, meta = load_prices(DATA_DIR / "prices.json")
    check_prices_fresh(meta)
    seeds = load_episodes("attack", args.seeds)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "runs" / f"{stamp}-adaptive-{args.feedback}-{args.defence}-{args.model}"
    out_dir.mkdir(parents=True)
    ledger = Ledger(LEDGER_PATH, args.max_spend, label=out_dir.name)
    (out_dir / "config.json").write_text(json.dumps(vars(args) | {"seeds": [s["id"] for s in seeds], "prices": meta,
                                                                  "card_prompt": "strict"},
                                                    indent=2, default=str))
    heldout = DATA_DIR / "episodes" / "heldout"  # git-ignored: successful rewrites stay out of the public repo
    target = AnthropicModel(args.model, prices[args.model], ledger, effort=args.effort)
    attacker = AnthropicModel(args.attacker_model, prices[args.attacker_model], ledger, effort="medium")
    monitor = AnthropicModel(args.model, prices[args.model], ledger, effort="low") if args.defence == "D3" else None
    card_client = AnthropicModel(args.model, prices[args.model], ledger, effort="low")
    approver = (lambda: LLMCardApprover(card_client)) if args.approver == "card_llm" else APPROVERS[args.approver]
    description = get_defence(args.defence).description

    async def go():
        sem = asyncio.Semaphore(args.concurrency)

        async def one(seed):
            async with sem:
                res = await adapt(seed, lambda: get_defence(args.defence, monitor), target, attacker, approver,
                                  args.iterations, description, args.feedback)
                won = res.pop("episode")
                if won is not None:
                    heldout.mkdir(parents=True, exist_ok=True)
                    won["id"] = f"{seed['id']}-adaptive-{args.feedback}-{args.defence}-{stamp}"
                    (heldout / f"{won['id']}.json").write_text(json.dumps(won, indent=2))
                with open(out_dir / "results.jsonl", "a") as f:  # noqa: ASYNC230 -- small append per seed
                    f.write(json.dumps(res, default=str) + "\n")
                print(f"{'SUCCESS' if res['success'] else 'held'} {seed['id']} best={res['best_stage']} "
                      f"attempts={res['attempts_used']}", flush=True)
                return res

        return await asyncio.gather(*(one(s) for s in seeds), return_exceptions=True)

    try:
        results = asyncio.run(go())
    finally:
        ledger.close()
    ok = [r for r in results if isinstance(r, dict)]
    summary = {"defence": args.defence, "approver": args.approver, "seeds": len(seeds), "completed": len(ok),
               "success_within_n": sum(r["success"] for r in ok), "iterations": args.iterations,
               "errors": [repr(r) for r in results if not isinstance(r, dict)], "ledger": ledger.summary()}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_spend(args) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    total, calls, by_model = 0.0, 0, {}
    if LEDGER_PATH.exists():
        for line in LEDGER_PATH.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                total += e["cost_usd"]
                calls += 1
                by_model[e.get("model", "?")] = by_model.get(e.get("model", "?"), 0.0) + e["cost_usd"]
    print(json.dumps({"cumulative_spend_usd": round(total, 4), "calls": calls, "budget_cap_usd": budget_cap(),
                      "by_model": {k: round(v, 4) for k, v in by_model.items()}}, indent=2))
    return 0


async def _run_all(episodes, defence_name, client, approver_name, repeats, concurrency, out_dir: Path,
                   approval_mode: str, monitor=None, callback_policy: str = "on_file"):
    sem = asyncio.Semaphore(concurrency)
    results = []
    (out_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    async def one(ep, attempt):
        async with sem:
            world, r = await run_episode(ep, get_defence(defence_name, monitor), client, APPROVERS[approver_name](),
                                         attempt, approval_mode, callback_policy)
            row = asdict(r)
            results.append(row)
            with open(out_dir / "results.jsonl", "a") as f:  # noqa: ASYNC230 -- small append per episode
                f.write(json.dumps(row, default=str) + "\n")
            (out_dir / "transcripts" / f"{ep['id']}--{attempt}.json").write_text(
                json.dumps(r.transcripts, indent=1, default=str))
            world.save(str(out_dir / "transcripts" / f"{ep['id']}--{attempt}.db"))
            mark = "PASS" if r.passed else "FAIL"
            print(f"{mark} {ep['id']} attempt {attempt} cost ${r.cost_usd:.4f} approvals {len(r.approvals)}", flush=True)

    tasks = [one(ep, a) for a in range(repeats) for ep in episodes]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [o for o in outcomes if isinstance(o, BaseException)]
    return results, errors


def cmd_run(args) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # the key is read by the SDK from the environment; never printed
    if "ANTHROPIC_API_KEY" not in os.environ:
        print("ANTHROPIC_API_KEY is not set (expected in garm/.env)", file=sys.stderr)
        return 2
    prices, meta = load_prices(DATA_DIR / "prices.json")
    try:
        check_prices_fresh(meta)
    except PricesNotVerified as e:
        print(f"refusing to run: {e}", file=sys.stderr)
        return 2
    if args.model not in prices:
        print(f"no price for {args.model} in data/prices.json", file=sys.stderr)
        return 2
    from .models.anthropic_model import AnthropicModel

    episodes = load_episodes(args.split, args.episodes)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    policy = "" if args.callback_policy == "on_file" else f"+{args.callback_policy}"
    out_dir = ROOT / "runs" / f"{stamp}-{args.split}-{args.defence}{policy}-{args.model}"
    out_dir.mkdir(parents=True)
    ledger = Ledger(LEDGER_PATH, args.max_spend, label=out_dir.name)
    config = {"split": args.split, "defence": args.defence, "model": args.model, "effort": args.effort,
              "approver": args.approver, "approval_mode": args.approval_mode, "monitor_model": args.monitor_model or (args.model if args.defence == "D3" else None), "repeats": args.repeats, "callback_policy": args.callback_policy, "episodes": [e["id"] for e in episodes],
              "max_spend_usd": args.max_spend, "prices": meta, "started": stamp}
    (out_dir / "config.json").write_text(json.dumps(config, indent=2))
    print(f"running {len(episodes)} episodes x {args.repeats} on {args.model} under {args.defence}; "
          f"cap ${ledger.invocation_cap:.2f} this run, ${ledger.prior_spend:.4f} spent so far of ${ledger.cap:.0f}")
    client = AnthropicModel(args.model, prices[args.model], ledger, effort=args.effort)
    monitor = None
    if args.defence == "D3":
        m = args.monitor_model or args.model
        if m not in prices:
            print(f"no price for monitor model {m}", file=sys.stderr)
            return 2
        monitor = AnthropicModel(m, prices[m], ledger, effort="low")
    try:
        results, errors = asyncio.run(_run_all(episodes, args.defence, client, args.approver, args.repeats,
                                               args.concurrency, out_dir, args.approval_mode, monitor,
                                               args.callback_policy))
    finally:
        ledger.close()
    summary = summarize(results) | {"ledger": ledger.summary(), "errors": [repr(e) for e in errors]}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if any(isinstance(e, BudgetExceeded) for e in errors):
        print("stopped early: budget cap reached", file=sys.stderr)
        return 3
    return 0


def load_plugins() -> None:
    """Import modules named in GARM_PLUGINS (comma-separated) so their defences register before parsing."""
    import importlib

    for name in filter(None, (m.strip() for m in os.environ.get("GARM_PLUGINS", "").split(","))):
        importlib.import_module(name)


def main(argv: list[str] | None = None) -> int:
    load_plugins()
    p = argparse.ArgumentParser(prog="garm")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="check episodes and graders offline")
    v.add_argument("--split", default="all", choices=["all", "benign", "legit", "attack"])
    v.add_argument("--episodes", nargs="*")
    v.set_defaults(fn=cmd_validate)
    g = sub.add_parser("regrade", help="re-score a finished run with the current graders (no API calls)")
    g.add_argument("run_dir")
    g.set_defaults(fn=cmd_regrade)
    a = sub.add_parser("analyze", help="headline table from run folders (no API calls)")
    a.add_argument("run_dirs", nargs="+")
    a.add_argument("--card-decisions", action="append", help="NAME=path to judge-cards output; repeatable")
    a.add_argument("--tasks-per-day", type=int, default=200)
    a.add_argument("--regraded", action="store_true", help="read results.regraded.jsonl written by `garm regrade`")
    a.add_argument("--out", default=None)
    a.set_defaults(fn=cmd_analyze)
    j = sub.add_parser("judge-cards", help="card-only LLM approver over logged cards (paid)")
    j.add_argument("run_dirs", nargs="+")
    j.add_argument("--model", default="claude-haiku-5-5")
    j.add_argument("--out", required=True)
    j.add_argument("--prompt", default="strict", choices=["strict", "base_rate"])
    j.add_argument("--max-spend", type=float, required=True)
    j.set_defaults(fn=cmd_judge_cards)
    ad = sub.add_parser("adapt", help="adaptive attacker over seed attack episodes (paid)")
    ad.add_argument("--seeds", nargs="+", required=True, help="attack episode ids or prefixes")
    ad.add_argument("--defence", default="D0", choices=sorted(DEFENCES))
    ad.add_argument("--model", default="claude-haiku-5-5")
    ad.add_argument("--effort", default="medium")
    ad.add_argument("--attacker-model", default="claude-sonnet-5-5")
    ad.add_argument("--approver", default="approve_all", choices=[*sorted(APPROVERS), "card_llm"])
    ad.add_argument("--iterations", type=int, default=10)
    ad.add_argument("--feedback", choices=["stage", "trace"], default="stage",
                    help="stage: grey-box (furthest stage reached); trace: also what the agent did")
    ad.add_argument("--concurrency", type=int, default=4)
    ad.add_argument("--max-spend", type=float, required=True)
    ad.set_defaults(fn=cmd_adapt)
    s = sub.add_parser("spend", help="show cumulative spend")
    s.set_defaults(fn=cmd_spend)
    r = sub.add_parser("run", help="run a model (paid)")
    r.add_argument("--split", default="benign")
    r.add_argument("--episodes", nargs="*", help="episode ids or id prefixes")
    r.add_argument("--defence", default="D0", choices=sorted(DEFENCES))
    r.add_argument("--model", required=True)
    r.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max"])
    r.add_argument("--approver", default="approve_all", choices=sorted(APPROVERS))
    r.add_argument("--approval-mode", default="inline", choices=["inline", "deferred"])
    r.add_argument("--callback-policy", default="on_file", choices=list(CALLBACK_POLICIES),
                   help="staff callback process; known_good and known_good_strict are D6 (docs/design-d6.md)")
    r.add_argument("--monitor-model", default=None, help="D3 monitor model (default: the agent's model)")
    r.add_argument("--repeats", type=int, default=1)
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--max-spend", type=float, required=True, help="cap for this invocation in USD")
    r.set_defaults(fn=cmd_run)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
