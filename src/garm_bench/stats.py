"""Summary statistics: Wilson score intervals and pass^k."""

from __future__ import annotations

import math
from collections import defaultdict


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float, float]:
    """Point estimate and 95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def summarize(results: list[dict]) -> dict:
    by_episode: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_episode[r["episode_id"]].append(bool(r["passed"]))
    k = min((len(v) for v in by_episode.values()), default=0)
    attempts = sum(len(v) for v in by_episode.values())
    passes = sum(sum(v) for v in by_episode.values())
    all_pass = sum(all(v[:k]) for v in by_episode.values()) if k else 0
    n_ep = len(by_episode)
    approvals = sum(len(r["approvals"]) for r in results)
    p1, lo1, hi1 = wilson(passes, attempts)
    pk, lok, hik = wilson(all_pass, n_ep)
    return {
        "episodes": n_ep,
        "attempts": attempts,
        "pass_rate": round(p1, 4), "pass_rate_ci95": [round(lo1, 4), round(hi1, 4)],
        f"pass^{k}": round(pk, 4), f"pass^{k}_ci95": [round(lok, 4), round(hik, 4)],
        "approvals_per_100_episodes": round(100 * approvals / attempts, 2) if attempts else None,
        "cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "cost_per_attempt_usd": round(sum(r["cost_usd"] for r in results) / attempts, 5) if attempts else None,
        "job_run_errors": sum(1 for r in results for j in r["job_runs"] if j["status"] != "ok"),
        "failed_episodes": sorted(e for e, v in by_episode.items() if not all(v)),
    } | attack_summary(results)


def attack_summary(results: list[dict]) -> dict:
    rows = [r for r in results if r.get("attack")]
    if not rows:
        return {}
    out = {}
    for timing in ("same_run", "cross_run", "all"):
        sel = [r for r in rows if timing == "all" or r["attack"]["timing"] == timing]
        if not sel:
            continue
        cell = {"n": len(sel)}
        for stage in ("write", "mention", "incorporation", "activation"):
            k = sum(1 for r in sel if r["attack"].get(stage))
            p, lo, hi = wilson(k, len(sel))
            cell[stage] = {"k": k, "rate": round(p, 4), "ci95": [round(lo, 4), round(hi, 4)]}
        out[timing] = cell
    return {"attack": out}
