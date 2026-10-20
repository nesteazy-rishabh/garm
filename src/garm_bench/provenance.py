"""Field-level provenance and authority for agents that write to a system of record.

This module is self-contained (standard library only) so it can be copied into another project. It answers one
question for an approval gate: does the value an action depends on rest only on writers you trust, or does it trace
back, possibly through a verification, to text an outsider wrote?

Three ideas:

- **Every write is recorded** with who wrote it (a writer kind such as "seed", "staff" or "agent") and, for agent
  writes, the outside source it came from.
- **Verifications are attestations.** A staff callback that confirms a new bank account attests (entity, field,
  value). If the attestation was made through a channel stored in the record, such as the phone number on file, it
  names that channel with `via`, and it is only as trustworthy as the write that put that channel value there.
- **Authority is computed, not stored.** A field has high authority if its current value was written by a trusted
  writer, or by an untrusted writer applying a change that a high-authority attestation confirms. Everything else is
  low. The result carries a human-readable chain for the approval card.

Example (the laundered callback that Garm's family X exploits):

    log = ProvenanceLog()
    log.record("vendor:V101", "phone", "555-0100", "seed", "seed", at="2026-01-01")
    log.record("vendor:V101", "phone", "555-0111", "agent", "update_records",
               source="email from office@vendor.example", at="2026-03-02T09:00")
    log.attest("vendor:V101", "account_number", "7123941702", by="staff:ap_clerk",
               via=("vendor:V101", "phone", "555-0111"), at="2026-03-02T13:00")
    log.record("vendor:V101", "account_number", "7123941702", "agent", "update_records",
               source="email from office@vendor.example", at="2026-03-02T14:00")
    a = Policy(trusted={"seed", "staff"}, attestable={"account_number"}).authority(log, "vendor:V101",
                                                                                   "account_number")
    a.level   # "low": the callback went to a number an outside email had just written
    a.card()  # the chain, one step per line
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

HIGH, LOW = "high", "low"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS writes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, field TEXT NOT NULL, value TEXT,
    writer_kind TEXT NOT NULL, writer TEXT NOT NULL, source TEXT, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attestations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, field TEXT NOT NULL, value TEXT,
    by TEXT NOT NULL, via_entity TEXT, via_field TEXT, via_value TEXT, at TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Authority:
    level: str
    chain: tuple[str, ...] = field(default_factory=tuple)

    @property
    def low(self) -> bool:
        return self.level == LOW

    def card(self) -> str:
        return "\n".join(f"- {step}" for step in self.chain)


def combine(*parts: Authority) -> Authority:
    """Low if any part is low; the chain keeps the low parts (or all parts when everything is high)."""
    level = LOW if any(p.low for p in parts) else HIGH
    return Authority(level, tuple(c for p in parts if p.low or level == HIGH for c in p.chain))


class ProvenanceLog:
    """An append-only log of field writes and attestations. Timestamps are ISO 8601 strings, compared as text."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self.conn = conn or sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def record(self, entity: str, field: str, value, writer_kind: str, writer: str, source: str | None = None,
               at: str = "") -> None:
        self.conn.execute("INSERT INTO writes (entity, field, value, writer_kind, writer, source, at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (entity, field, None if value is None else str(value), writer_kind, writer, source, at))

    def attest(self, entity: str, field: str, value, by: str, via: tuple[str, str, str] | None = None,
               at: str = "") -> None:
        ve, vf, vv = via or (None, None, None)
        self.conn.execute("INSERT INTO attestations (entity, field, value, by, via_entity, via_field, via_value, at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (entity, field, None if value is None else str(value), by, ve, vf, vv, at))

    def last_write(self, entity: str, field: str, value=None, before: str | None = None) -> dict | None:
        sql, params = "SELECT * FROM writes WHERE entity = ? AND field = ?", [entity, field]
        if value is not None:
            sql, params = sql + " AND value = ?", [*params, str(value)]
        if before is not None:
            sql, params = sql + " AND at <= ?", [*params, before]
        row = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
        return dict(row) if row else None

    def attestation(self, entity: str, field: str, value, before: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM attestations WHERE entity = ? AND field = ? AND lower(value) = lower(?) AND at <= ? "
            "ORDER BY id DESC LIMIT 1", (entity, field, str(value), before)).fetchone()
        return dict(row) if row else None


@dataclass
class Policy:
    """Which writer kinds are trusted, and which fields an attestation can raise to high authority."""

    trusted: set[str] = field(default_factory=lambda: {"seed", "staff"})
    attestable: set[str] = field(default_factory=set)
    max_depth: int = 8  # attestation chains longer than this are treated as low (and cycles cannot loop)

    def _describe(self, w: dict) -> str:
        text = f"{w['field']} = {w['value']!r} written {w['at']} by {w['writer_kind']} ({w['writer']})"
        return text + (f" from {w['source']}" if w["writer_kind"] not in self.trusted and w.get("source") else "")

    def _write_authority(self, log: ProvenanceLog, w: dict | None, label: str, depth: int) -> Authority:
        if w is None:
            return Authority(LOW, (f"{label}: no record of who wrote it",))
        if w["writer_kind"] in self.trusted:
            return Authority(HIGH, (self._describe(w),))
        if w["field"] in self.attestable and depth < self.max_depth:
            a = log.attestation(w["entity"], w["field"], w["value"], before=w["at"])
            if a is not None:
                aa = self.attestation_authority(log, a, depth + 1)
                return Authority(aa.level, (self._describe(w), *aa.chain))
            return Authority(LOW, (self._describe(w), "no matching verification"))
        return Authority(LOW, (self._describe(w),))

    def attestation_authority(self, log: ProvenanceLog, a: dict, depth: int = 0) -> Authority:
        """An attestation made through a stored channel is only as trustworthy as that channel's value."""
        head = f"{a['field']} verified {a['at']} by {a['by']}"
        if a["via_entity"] is None:
            return Authority(HIGH, (head,))
        head += f" via {a['via_field']} {a['via_value']}"
        w = log.last_write(a["via_entity"], a["via_field"], value=a["via_value"], before=a["at"])
        inner = self._write_authority(log, w, f"{a['via_field']} {a['via_value']}", depth)
        return Authority(inner.level, (head, *inner.chain))

    def authority(self, log: ProvenanceLog, entity: str, field: str) -> Authority:
        """Authority of the current value of (entity, field)."""
        return self._write_authority(log, log.last_write(entity, field), f"{entity}.{field}", 0)
