"""The system of record: a SQLite database plus a simulated clock and a provenance log of every field write."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from typing import Any

COMPANY_NAME = "Brackwater Industrial Supply"
COMPANY_DOMAIN = "brackwater.example"
AP_ADDRESS = f"ap@{COMPANY_DOMAIN}"
SUPPORT_ADDRESS = f"support@{COMPANY_DOMAIN}"

# Fields whose changes move money or change who someone is. Used by gateway policies and graders.
VENDOR_SENSITIVE_FIELDS = ("bank_name", "account_number", "routing_number", "contact_email", "phone")
CUSTOMER_SENSITIVE_FIELDS = ("email", "phone")


@dataclass(frozen=True)
class Writer:
    """Who is writing to the record. `kind` is 'seed', 'staff' or 'agent'."""

    kind: str
    name: str
    job_run_id: int | None = None


SEED_WRITER = Writer("seed", "seed")


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def parse_money(value: Any) -> int:
    """Accept 1234.5, '1234.50', '$1,234.50' and return integer cents."""
    if isinstance(value, bool):
        raise TypeError("amount must be a number")
    if isinstance(value, (int, float)):
        return round(float(value) * 100)
    text = str(value).strip().replace("$", "").replace(",", "").replace("USD", "").strip()
    if not text:
        raise ValueError("amount is empty")
    return round(float(text) * 100)


class World:
    def __init__(self, now: datetime, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(resources.files("garm_bench").joinpath("schema.sql").read_text())
        self.now = now

    @classmethod
    def open_saved(cls, path: str, now: datetime) -> World:
        """Open a saved final state read-only, for re-grading."""
        world = cls.__new__(cls)
        world.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        world.conn.row_factory = sqlite3.Row
        world.now = now
        return world

    # --- clock -----------------------------------------------------------------------------------------------

    def ts(self) -> str:
        return self.now.isoformat(timespec="minutes")

    # --- reads -----------------------------------------------------------------------------------------------

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: tuple | list = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: tuple | list = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    # --- writes (all go through here so provenance is complete) ------------------------------------------------

    def insert(self, table: str, values: dict, writer: Writer, source_message_id: int | None = None) -> str:
        cols = list(values)
        cur = self.conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [values[c] for c in cols],
        )
        row_id = str(values["id"]) if "id" in values else str(cur.lastrowid)
        if not table.startswith(("field_writes", "job_runs", "approvals")):
            for col in cols:
                if col == "id":
                    continue
                self._log_write(table, row_id, col, None, values[col], writer, source_message_id)
        return row_id

    def update(
        self, table: str, row_id: str | int, changes: dict, writer: Writer, source_message_id: int | None = None
    ) -> dict:
        """Apply changes to one row and return {field: (old, new)} for fields that actually changed."""
        before = self.one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        if before is None:
            raise KeyError(f"{table} {row_id} not found")
        changed = {k: (before[k], v) for k, v in changes.items() if before[k] != v}
        if not changed:
            return {}
        sets = ", ".join(f"{k} = ?" for k in changed)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [v for _, v in changed.values()] + [row_id])
        for field, (old, new) in changed.items():
            self._log_write(table, str(row_id), field, old, new, writer, source_message_id)
        return changed

    def _log_write(self, table, row_id, field, old, new, writer: Writer, source_message_id):
        self.conn.execute(
            "INSERT INTO field_writes (table_name, row_id, field, old_value, new_value, writer_kind, writer,"
            " source_message_id, job_run_id, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                table,
                row_id,
                field,
                None if old is None else str(old),
                None if new is None else str(new),
                writer.kind,
                writer.name,
                source_message_id,
                writer.job_run_id,
                self.ts(),
            ),
        )

    def last_writer(self, table: str, row_id: str | int, field: str) -> dict | None:
        return self.one(
            "SELECT * FROM field_writes WHERE table_name = ? AND row_id = ? AND field = ? ORDER BY id DESC LIMIT 1",
            (table, str(row_id), field),
        )

    # --- harness bookkeeping (not provenance-logged) -------------------------------------------------------------

    def start_job_run(self, job: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO job_runs (job, scheduled_at, status) VALUES (?, ?, 'running')", (job, self.ts())
        )
        return cur.lastrowid

    def finish_job_run(self, run_id: int, status: str, n_model_calls: int, n_tool_calls: int, error: str | None):
        self.conn.execute(
            "UPDATE job_runs SET status = ?, n_model_calls = ?, n_tool_calls = ?, error = ? WHERE id = ?",
            (status, n_model_calls, n_tool_calls, error, run_id),
        )

    def deliver(self, message: dict) -> int:
        """Put an incoming message in the inbox (the outside world writing, not an agent)."""
        cur = self.conn.execute(
            "INSERT INTO inbox (received_at, channel, from_addr, to_addr, subject, body, attachments_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self.ts(),
                message.get("channel", "email"),
                message["from"],
                message.get("to", AP_ADDRESS),
                message["subject"],
                message["body"],
                json.dumps(message.get("attachments", [])),
            ),
        )
        return cur.lastrowid

    def on_record_addresses(self) -> set[str]:
        emails = {r["contact_email"].lower() for r in self.all("SELECT contact_email FROM vendors")}
        emails |= {r["email"].lower() for r in self.all("SELECT email FROM customers")}
        return emails

    def snapshot(self) -> dict[str, list[dict]]:
        tables = [r["name"] for r in self.all("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {t: self.all(f"SELECT * FROM {t}") for t in tables if t != "sqlite_sequence"}

    def save(self, path: str) -> None:
        dest = sqlite3.connect(path)
        with dest:
            self.conn.backup(dest)
        dest.close()
