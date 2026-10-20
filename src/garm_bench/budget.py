"""Spend control: a dated price table and a ledger with a cumulative hard cap.

Every paid model call reserves a deliberately high cost estimate before it is sent and is refused if the reservation
would cross the cap. After the call the actual cost is recorded from the reported token usage. The ledger file is
the cumulative record across all invocations; only one paid invocation may hold it at a time.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

HARD_CAP_USD = 100.0  # project ceiling; GARM_BUDGET_CAP_USD can lower it, never raise it


def budget_cap() -> float:
    try:
        requested = float(os.environ.get("GARM_BUDGET_CAP_USD", HARD_CAP_USD))
    except ValueError:
        return HARD_CAP_USD
    return max(0.0, min(HARD_CAP_USD, requested))
PRICE_MAX_AGE_DAYS = 14


class BudgetExceeded(RuntimeError):
    pass


class PricesNotVerified(RuntimeError):
    pass


@dataclass(frozen=True)
class Price:
    input: float  # USD per million tokens
    output: float
    cache_write: float
    cache_read: float
    long_context_threshold: int | None = None
    long_input: float | None = None
    long_output: float | None = None

    def cost(self, input_tokens: int, output_tokens: int, cache_write: int, cache_read: int) -> float:
        prompt = input_tokens + cache_write + cache_read
        if self.long_context_threshold and prompt > self.long_context_threshold:
            scale_in = self.long_input / self.input
            scale_out = self.long_output / self.output
        else:
            scale_in = scale_out = 1.0
        return (
            input_tokens * self.input * scale_in
            + cache_write * self.cache_write * scale_in
            + cache_read * self.cache_read * scale_in
            + output_tokens * self.output * scale_out
        ) / 1e6

    def upper_bound(self, est_input_tokens: int, max_output_tokens: int) -> float:
        rate_in = max(self.input, self.cache_write)
        rate_out = self.output
        if self.long_context_threshold and est_input_tokens > self.long_context_threshold:
            rate_in, rate_out = self.long_input * 1.25, self.long_output
        return (est_input_tokens * rate_in + max_output_tokens * rate_out) / 1e6


def load_prices(path: Path) -> tuple[dict[str, Price], dict]:
    data = json.loads(path.read_text())
    prices = {m: Price(**p) for m, p in data["models"].items()}
    return prices, {k: v for k, v in data.items() if k != "models"}


def check_prices_fresh(meta: dict, today: date | None = None) -> None:
    verified = meta.get("verified_on")
    if not verified:
        raise PricesNotVerified(
            f"prices in the price table have not been verified against the live pricing page ({meta.get('source_url')})."
            " Re-check them and set verified_on before any paid run."
        )
    age = ((today or date.today()) - date.fromisoformat(verified)).days
    if age > PRICE_MAX_AGE_DAYS:
        raise PricesNotVerified(f"prices were verified {age} days ago; re-check them before a paid run")


class Ledger:
    def __init__(self, path: Path, invocation_cap_usd: float, label: str):
        if invocation_cap_usd <= 0:
            raise ValueError("invocation cap must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cap = budget_cap()
        self.invocation_cap = min(invocation_cap_usd, self.cap)
        self.label = label
        self._lock = threading.Lock()
        self._lockfile = open(self.path.with_suffix(".lock"), "w")  # noqa: SIM115 -- held until close()
        try:
            fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another paid invocation holds the ledger; run one at a time") from None
        self.prior_spend = self._read_total()
        self.invocation_spend = 0.0
        self.reserved = 0.0
        self.calls = 0

    def _read_total(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        with open(self.path) as f:
            for line in f:
                if line.strip():
                    total += json.loads(line)["cost_usd"]
        return total

    def remaining(self) -> float:
        return min(
            self.cap - self.prior_spend - self.invocation_spend - self.reserved,
            self.invocation_cap - self.invocation_spend - self.reserved,
        )

    def reserve(self, estimate_usd: float) -> float:
        with self._lock:
            if estimate_usd > self.remaining():
                raise BudgetExceeded(
                    f"call refused: estimate ${estimate_usd:.4f} exceeds remaining budget ${self.remaining():.4f} "
                    f"(spent before this run ${self.prior_spend:.4f}, this run ${self.invocation_spend:.4f}, "
                    f"run cap ${self.invocation_cap:.2f}, budget cap ${self.cap:.2f})"
                )
            self.reserved += estimate_usd
            return estimate_usd

    def settle(self, reservation: float, cost_usd: float, record: dict) -> None:
        with self._lock:
            self.reserved -= reservation
            self.invocation_spend += cost_usd
            self.calls += 1
            entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "label": self.label,
                     "cost_usd": round(cost_usd, 8), **record}
            with open(self.path, "a") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()
                os.fsync(f.fileno())

    def summary(self) -> dict:
        return {"calls": self.calls, "invocation_spend_usd": round(self.invocation_spend, 4),
                "cumulative_spend_usd": round(self.prior_spend + self.invocation_spend, 4),
                "invocation_cap_usd": self.invocation_cap, "budget_cap_usd": self.cap}

    def close(self) -> None:
        fcntl.flock(self._lockfile, fcntl.LOCK_UN)
        self._lockfile.close()
