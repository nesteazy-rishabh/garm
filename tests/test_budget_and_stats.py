from datetime import date

import pytest

from garm_bench.budget import (
    HARD_CAP_USD,
    BudgetExceeded,
    Ledger,
    Price,
    PricesNotVerified,
    check_prices_fresh,
)
from garm_bench.stats import summarize, wilson

PRICE = Price(input=0.10, output=0.50, cache_write=0.125, cache_read=0.01, long_context_threshold=100_000,
              long_input=0.50, long_output=2.50)


def test_cost_uses_each_token_class():
    assert PRICE.cost(10_000, 0, 0, 0) == pytest.approx(0.001)
    assert PRICE.cost(0, 10_000, 0, 0) == pytest.approx(0.005)
    assert PRICE.cost(0, 0, 10_000, 0) == pytest.approx(0.00125)
    assert PRICE.cost(0, 0, 0, 10_000) == pytest.approx(0.0001)


def test_long_context_tier_applies_above_threshold():
    assert PRICE.cost(150_000, 1000, 0, 0) == pytest.approx((150_000 * 0.50 + 1000 * 2.50) / 1e6)


def test_upper_bound_exceeds_actual_for_same_tokens():
    assert PRICE.upper_bound(10_000, 2000) >= PRICE.cost(10_000, 2000, 0, 0)


def test_invocation_cap_refuses_and_records(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl", 0.01, "t")
    r = ledger.reserve(0.006)
    ledger.settle(r, 0.005, {"model": "m"})
    with pytest.raises(BudgetExceeded):
        ledger.reserve(0.006)
    ledger.close()
    assert (tmp_path / "ledger.jsonl").read_text().count("\n") == 1


def test_hard_cap_is_cumulative_across_invocations(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = Ledger(path, HARD_CAP_USD, "a")
    first.settle(first.reserve(1.0), HARD_CAP_USD - 0.001, {})
    first.close()
    second = Ledger(path, 50.0, "b")
    with pytest.raises(BudgetExceeded):
        second.reserve(0.01)
    second.close()


def test_invocation_cap_cannot_exceed_hard_cap(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl", 10_000.0, "t")
    assert ledger.invocation_cap == HARD_CAP_USD
    ledger.close()


def test_only_one_paid_invocation_at_a_time(tmp_path):
    first = Ledger(tmp_path / "l.jsonl", 1.0, "a")
    with pytest.raises(RuntimeError):
        Ledger(tmp_path / "l.jsonl", 1.0, "b")
    first.close()


def test_prices_must_be_verified_and_recent():
    with pytest.raises(PricesNotVerified):
        check_prices_fresh({"verified_on": None})
    with pytest.raises(PricesNotVerified):
        check_prices_fresh({"verified_on": "2026-09-01"}, today=date(2026, 10, 19))
    check_prices_fresh({"verified_on": "2026-10-19"}, today=date(2026, 10, 20))


def test_wilson_interval_known_value():
    p, lo, hi = wilson(8, 10)
    assert p == 0.8
    assert lo == pytest.approx(0.4902, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)


def test_pass_k_requires_every_attempt():
    rows = [{"episode_id": "a", "passed": True, "approvals": [], "cost_usd": 0.0, "job_runs": []},
            {"episode_id": "a", "passed": False, "approvals": [], "cost_usd": 0.0, "job_runs": []},
            {"episode_id": "b", "passed": True, "approvals": [1], "cost_usd": 0.0, "job_runs": []},
            {"episode_id": "b", "passed": True, "approvals": [], "cost_usd": 0.0, "job_runs": []}]
    s = summarize(rows)
    assert s["pass^2"] == 0.5
    assert s["pass_rate"] == 0.75
    assert s["approvals_per_100_episodes"] == 25.0
