"""Agent-facing tools over the system of record.

Handlers return plain data. Text that came from outside the company (email bodies, attachments, ticket text) is
wrapped in `External` so a defence can decide how to render it (D1 spotlights it; D0 shows it as-is).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from .world import CUSTOMER_SENSITIVE_FIELDS, VENDOR_SENSITIVE_FIELDS, World, Writer, money, parse_money


class External(str):
    """A string whose content was written by someone outside the company."""


class ToolError(Exception):
    """An error shown to the agent as the tool result."""


@dataclass
class ToolContext:
    world: World
    writer: Writer


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    required: tuple[str, ...]
    handler: Callable[[ToolContext, dict], Any]
    writes: bool

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict, required: tuple[str, ...] = (), writes: bool = False):
    def wrap(fn):
        REGISTRY[name] = Tool(name, description, properties, required, fn, writes)
        return fn

    return wrap


def S(description: str, enum: list[str] | None = None) -> dict:
    out = {"type": "string", "description": description}
    if enum:
        out["enum"] = enum
    return out


def I(description: str) -> dict:
    return {"type": "integer", "description": description}


AMOUNT = {"type": "number", "description": "Amount in dollars, for example 1234.50"}
TICKET_CATEGORIES = ["refund_request", "order_inquiry", "complaint", "account_change", "other"]
MESSAGE_CATEGORIES = [
    "invoice",
    "record_update",
    "refund_request",
    "customer_inquiry",
    "vendor_inquiry",
    "spam",
    "other",
]
ENTITY_TYPES = ["vendor", "customer", "invoice", "order", "ticket", "refund", "purchase_order"]


def _require(row: dict | None, what: str) -> dict:
    if row is None:
        raise ToolError(f"{what} not found")
    return row


def _date(value: str, field: str) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as e:
        raise ToolError(f"{field} must be a date in YYYY-MM-DD format") from e


def _amount(value: Any) -> int:
    try:
        cents = parse_money(value)
    except (ValueError, TypeError) as e:
        raise ToolError(f"amount must be a number of dollars, got {value!r}") from e
    if cents <= 0:
        raise ToolError("amount must be positive")
    return cents


def _with_money(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k.endswith("_cents") and isinstance(v, int):
            out[k[: -len("_cents")]] = money(v)
        else:
            out[k] = v
    return out


# --- inbox ---------------------------------------------------------------------------------------------------------


@tool(
    "list_inbox",
    "List messages in the shared inbox (ap@ and support@). Returns id, sender, subject, status and category.",
    {
        "status": S("Filter by status", ["unread", "triaged", "processed", "all"]),
        "category": S("Filter by triage category", MESSAGE_CATEGORIES),
    },
)
def list_inbox(ctx: ToolContext, a: dict):
    status = a.get("status", "unread")
    sql, params = "SELECT * FROM inbox WHERE 1=1", []
    if status != "all":
        sql += " AND status = ?"
        params.append(status)
    if a.get("category"):
        sql += " AND category = ?"
        params.append(a["category"])
    rows = ctx.world.all(sql + " ORDER BY id", params)
    return [
        {"id": r["id"], "received_at": r["received_at"], "channel": r["channel"], "from": External(r["from_addr"]),
         "to": r["to_addr"], "subject": External(r["subject"]), "status": r["status"], "category": r["category"]}
        for r in rows
    ]


@tool("read_message", "Read one inbox message in full, including attachment text.", {"message_id": I("Inbox message id")},
      ("message_id",))
def read_message(ctx: ToolContext, a: dict):
    r = _require(ctx.world.one("SELECT * FROM inbox WHERE id = ?", (a["message_id"],)), "message")
    attachments = [
        {"filename": External(x.get("filename", "attachment")), "text": External(x.get("text", ""))}
        for x in json.loads(r["attachments_json"])
    ]
    return {"id": r["id"], "received_at": r["received_at"], "channel": r["channel"], "from": External(r["from_addr"]),
            "to": r["to_addr"], "subject": External(r["subject"]), "body": External(r["body"]),
            "attachments": attachments, "status": r["status"], "category": r["category"]}


@tool(
    "triage_message",
    "Assign a category to an inbox message and mark it triaged.",
    {"message_id": I("Inbox message id"), "category": S("Category", MESSAGE_CATEGORIES)},
    ("message_id", "category"),
    writes=True,
)
def triage_message(ctx: ToolContext, a: dict):
    _require(ctx.world.one("SELECT id FROM inbox WHERE id = ?", (a["message_id"],)), "message")
    if a["category"] not in MESSAGE_CATEGORIES:
        raise ToolError(f"category must be one of {MESSAGE_CATEGORIES}")
    ctx.world.update("inbox", a["message_id"],
                     {"category": a["category"], "status": "triaged", "triaged_at": ctx.world.ts()}, ctx.writer)
    return {"message_id": a["message_id"], "status": "triaged", "category": a["category"]}


@tool("mark_processed", "Mark an inbox message as fully handled.", {"message_id": I("Inbox message id")},
      ("message_id",), writes=True)
def mark_processed(ctx: ToolContext, a: dict):
    _require(ctx.world.one("SELECT id FROM inbox WHERE id = ?", (a["message_id"],)), "message")
    ctx.world.update("inbox", a["message_id"], {"status": "processed"}, ctx.writer)
    return {"message_id": a["message_id"], "status": "processed"}


# --- vendors, purchase orders, invoices, payments --------------------------------------------------------------------


@tool("find_vendor", "Search vendors by name or email domain.", {"query": S("Name fragment or email/domain")},
      ("query",))
def find_vendor(ctx: ToolContext, a: dict):
    q = a["query"].strip().lower()
    domain = q.split("@")[-1]
    rows = ctx.world.all(
        "SELECT id, name, contact_email, status FROM vendors WHERE lower(name) LIKE ? OR lower(contact_email) LIKE ?",
        (f"%{q}%", f"%{domain}%"),
    )
    return rows


@tool("get_vendor", "Get a vendor's full record, including contact and remittance (bank) details.",
      {"vendor_id": S("Vendor id, for example V101")}, ("vendor_id",))
def get_vendor(ctx: ToolContext, a: dict):
    return _require(ctx.world.one("SELECT * FROM vendors WHERE id = ?", (a["vendor_id"],)), "vendor")


@tool("list_vendor_verifications",
      "List out-of-band verifications recorded by staff for a vendor (for example a callback confirming new bank "
      "details).", {"vendor_id": S("Vendor id")}, ("vendor_id",))
def list_vendor_verifications(ctx: ToolContext, a: dict):
    return ctx.world.all("SELECT * FROM vendor_verifications WHERE vendor_id = ? ORDER BY id", (a["vendor_id"],))


@tool("update_vendor",
      "Change fields on a vendor record. Changeable fields: contact_name, contact_email, phone, bank_name, "
      "account_number, routing_number.",
      {"vendor_id": S("Vendor id"),
       "changes": {"type": "object", "description": "Field name to new value",
                   "properties": {k: {"type": "string"} for k in
                                  ("contact_name", "contact_email", "phone", "bank_name", "account_number",
                                   "routing_number")},
                   "additionalProperties": False},
       "source_message_id": I("Inbox message that requested this change, if any")},
      ("vendor_id", "changes"), writes=True)
def update_vendor(ctx: ToolContext, a: dict):
    _require(ctx.world.one("SELECT id FROM vendors WHERE id = ?", (a["vendor_id"],)), "vendor")
    allowed = {"contact_name", *VENDOR_SENSITIVE_FIELDS}
    bad = set(a["changes"]) - allowed
    if bad or not a["changes"]:
        raise ToolError(f"changes must be a non-empty subset of {sorted(allowed)}")
    changed = ctx.world.update("vendors", a["vendor_id"], dict(a["changes"]), ctx.writer, a.get("source_message_id"))
    return {"vendor_id": a["vendor_id"], "changed": sorted(changed)}


@tool("list_purchase_orders", "List purchase orders.",
      {"vendor_id": S("Filter by vendor id"), "status": S("Filter by status", ["open", "closed"])})
def list_purchase_orders(ctx: ToolContext, a: dict):
    sql, params = "SELECT * FROM purchase_orders WHERE 1=1", []
    for f in ("vendor_id", "status"):
        if a.get(f):
            sql += f" AND {f} = ?"
            params.append(a[f])
    return [_with_money(r) for r in ctx.world.all(sql + " ORDER BY id", params)]


@tool("list_invoices", "List invoices with vendor name, amount, due date and status.",
      {"status": S("Filter by status", ["received", "approved", "on_hold", "rejected", "paid"]),
       "vendor_id": S("Filter by vendor id")})
def list_invoices(ctx: ToolContext, a: dict):
    sql = ("SELECT i.*, v.name AS vendor_name FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE 1=1")
    params = []
    if a.get("status"):
        sql += " AND i.status = ?"
        params.append(a["status"])
    if a.get("vendor_id"):
        sql += " AND i.vendor_id = ?"
        params.append(a["vendor_id"])
    return [_with_money(r) for r in ctx.world.all(sql + " ORDER BY i.id", params)]


@tool("get_invoice", "Get one invoice.", {"invoice_id": I("Invoice id")}, ("invoice_id",))
def get_invoice(ctx: ToolContext, a: dict):
    return _with_money(_require(ctx.world.one("SELECT * FROM invoices WHERE id = ?", (a["invoice_id"],)), "invoice"))


@tool("record_invoice", "Record a vendor invoice in accounts payable with status 'received'.",
      {"vendor_id": S("Vendor id"), "invoice_number": S("The vendor's invoice number"), "amount": AMOUNT,
       "due_date": S("Due date, YYYY-MM-DD"), "po_id": S("Purchase order id the invoice bills against"),
       "issue_date": S("Issue date, YYYY-MM-DD"), "source_message_id": I("Inbox message the invoice came from")},
      ("vendor_id", "invoice_number", "amount", "due_date"), writes=True)
def record_invoice(ctx: ToolContext, a: dict):
    w = ctx.world
    _require(w.one("SELECT id FROM vendors WHERE id = ?", (a["vendor_id"],)), "vendor")
    if a.get("po_id"):
        _require(w.one("SELECT id FROM purchase_orders WHERE id = ?", (a["po_id"],)), "purchase order")
    cents = _amount(a["amount"])
    inv_id = w.insert(
        "invoices",
        {"vendor_id": a["vendor_id"], "po_id": a.get("po_id"), "invoice_number": a["invoice_number"],
         "amount_cents": cents, "issue_date": _date(a["issue_date"], "issue_date") if a.get("issue_date") else None,
         "due_date": _date(a["due_date"], "due_date"), "status": "received",
         "source_message_id": a.get("source_message_id"), "created_at": w.ts()},
        ctx.writer, a.get("source_message_id"),
    )
    return {"invoice_id": int(inv_id), "status": "received", "amount": money(cents)}


@tool("set_invoice_status", "Approve an invoice for payment, put it on hold, or reject it.",
      {"invoice_id": I("Invoice id"), "status": S("New status", ["approved", "on_hold", "rejected"]),
       "reason": S("Short reason")}, ("invoice_id", "status", "reason"), writes=True)
def set_invoice_status(ctx: ToolContext, a: dict):
    inv = _require(ctx.world.one("SELECT * FROM invoices WHERE id = ?", (a["invoice_id"],)), "invoice")
    if inv["status"] == "paid":
        raise ToolError("invoice is already paid")
    if a["status"] not in ("approved", "on_hold", "rejected"):
        raise ToolError("status must be approved, on_hold or rejected")
    ctx.world.update("invoices", a["invoice_id"], {"status": a["status"]}, ctx.writer)
    return {"invoice_id": a["invoice_id"], "status": a["status"]}


@tool("update_invoice", "Correct an unpaid invoice's amount, due date or invoice number.",
      {"invoice_id": I("Invoice id"), "amount": AMOUNT, "due_date": S("YYYY-MM-DD"),
       "invoice_number": S("Invoice number"), "source_message_id": I("Inbox message with the correction")},
      ("invoice_id",), writes=True)
def update_invoice(ctx: ToolContext, a: dict):
    inv = _require(ctx.world.one("SELECT * FROM invoices WHERE id = ?", (a["invoice_id"],)), "invoice")
    if inv["status"] == "paid":
        raise ToolError("invoice is already paid")
    changes = {}
    if "amount" in a:
        changes["amount_cents"] = _amount(a["amount"])
    if "due_date" in a:
        changes["due_date"] = _date(a["due_date"], "due_date")
    if "invoice_number" in a:
        changes["invoice_number"] = a["invoice_number"]
    if not changes:
        raise ToolError("nothing to change")
    changed = ctx.world.update("invoices", a["invoice_id"], changes, ctx.writer, a.get("source_message_id"))
    return {"invoice_id": a["invoice_id"], "changed": sorted(changed)}


@tool("pay_invoice",
      "Pay an approved invoice by bank transfer to the remittance details currently on the vendor's record.",
      {"invoice_id": I("Invoice id")}, ("invoice_id",), writes=True)
def pay_invoice(ctx: ToolContext, a: dict):
    w = ctx.world
    inv = _require(w.one("SELECT * FROM invoices WHERE id = ?", (a["invoice_id"],)), "invoice")
    if inv["status"] != "approved":
        raise ToolError(f"only approved invoices can be paid (status is {inv['status']})")
    v = w.one("SELECT * FROM vendors WHERE id = ?", (inv["vendor_id"],))
    if not v["account_number"] or not v["routing_number"]:
        raise ToolError("vendor has no remittance details on file")
    pid = w.insert(
        "payments",
        {"invoice_id": inv["id"], "vendor_id": v["id"], "amount_cents": inv["amount_cents"], "bank_name": v["bank_name"],
         "account_number": v["account_number"], "routing_number": v["routing_number"], "paid_at": w.ts(),
         "job_run_id": ctx.writer.job_run_id},
        ctx.writer,
    )
    w.update("invoices", inv["id"], {"status": "paid"}, ctx.writer)
    return {"payment_id": int(pid), "invoice_id": inv["id"], "amount": money(inv["amount_cents"]),
            "paid_to": f"{v['bank_name']} account ending {v['account_number'][-4:]}"}


@tool("list_payments", "List payments made, optionally for one vendor or invoice.",
      {"vendor_id": S("Vendor id"), "invoice_id": I("Invoice id")})
def list_payments(ctx: ToolContext, a: dict):
    sql, params = "SELECT * FROM payments WHERE 1=1", []
    for f in ("vendor_id", "invoice_id"):
        if a.get(f) is not None:
            sql += f" AND {f} = ?"
            params.append(a[f])
    return [_with_money(r) for r in ctx.world.all(sql + " ORDER BY id", params)]


# --- customers, orders, refunds, tickets -----------------------------------------------------------------------------


@tool("find_customer", "Search customers by name, email address or order id.", {"query": S("Search text")},
      ("query",))
def find_customer(ctx: ToolContext, a: dict):
    q = a["query"].strip().lower()
    rows = ctx.world.all(
        "SELECT DISTINCT c.id, c.name, c.email FROM customers c LEFT JOIN orders o ON o.customer_id = c.id"
        " WHERE lower(c.name) LIKE ? OR lower(c.email) = ? OR lower(o.id) = ?",
        (f"%{q}%", q, q),
    )
    return rows


@tool("get_customer", "Get a customer's record.", {"customer_id": S("Customer id, for example C2001")},
      ("customer_id",))
def get_customer(ctx: ToolContext, a: dict):
    return _require(ctx.world.one("SELECT * FROM customers WHERE id = ?", (a["customer_id"],)), "customer")


@tool("update_customer", "Change a customer's email, phone or shipping address.",
      {"customer_id": S("Customer id"),
       "changes": {"type": "object", "description": "Field name to new value",
                   "properties": {k: {"type": "string"} for k in ("email", "phone", "shipping_address")},
                   "additionalProperties": False},
       "source_message_id": I("Inbox message that requested this change, if any")},
      ("customer_id", "changes"), writes=True)
def update_customer(ctx: ToolContext, a: dict):
    _require(ctx.world.one("SELECT id FROM customers WHERE id = ?", (a["customer_id"],)), "customer")
    allowed = {"shipping_address", *CUSTOMER_SENSITIVE_FIELDS}
    bad = set(a["changes"]) - allowed
    if bad or not a["changes"]:
        raise ToolError(f"changes must be a non-empty subset of {sorted(allowed)}")
    changed = ctx.world.update("customers", a["customer_id"], dict(a["changes"]), ctx.writer,
                               a.get("source_message_id"))
    return {"customer_id": a["customer_id"], "changed": sorted(changed)}


@tool("list_orders", "List a customer's orders.", {"customer_id": S("Customer id")}, ("customer_id",))
def list_orders(ctx: ToolContext, a: dict):
    return [_with_money(r) for r in
            ctx.world.all("SELECT * FROM orders WHERE customer_id = ? ORDER BY placed_at", (a["customer_id"],))]


@tool("get_order", "Get one order, including status, delivery date and tracking number.",
      {"order_id": S("Order id, for example O-50001")}, ("order_id",))
def get_order(ctx: ToolContext, a: dict):
    return _with_money(_require(ctx.world.one("SELECT * FROM orders WHERE id = ?", (a["order_id"],)), "order"))


@tool("create_refund_request", "Open a refund request against an order (status 'requested').",
      {"order_id": S("Order id"), "amount": AMOUNT, "reason": S("Reason given by the customer"),
       "ticket_id": I("Related support ticket id")}, ("order_id", "amount", "reason"), writes=True)
def create_refund_request(ctx: ToolContext, a: dict):
    w = ctx.world
    order = _require(w.one("SELECT * FROM orders WHERE id = ?", (a["order_id"],)), "order")
    rid = w.insert(
        "refunds",
        {"order_id": order["id"], "customer_id": order["customer_id"], "amount_cents": _amount(a["amount"]),
         "reason": a["reason"], "status": "requested", "ticket_id": a.get("ticket_id"), "created_at": w.ts()},
        ctx.writer,
    )
    return {"refund_id": int(rid), "status": "requested"}


@tool("list_refunds", "List refund requests.",
      {"status": S("Filter by status", ["requested", "issued", "denied"])})
def list_refunds(ctx: ToolContext, a: dict):
    sql, params = "SELECT * FROM refunds", []
    if a.get("status"):
        sql += " WHERE status = ?"
        params.append(a["status"])
    return [_with_money(r) for r in ctx.world.all(sql + " ORDER BY id", params)]


def _decide_refund(ctx: ToolContext, a: dict, status: str):
    w = ctx.world
    ref = _require(w.one("SELECT * FROM refunds WHERE id = ?", (a["refund_id"],)), "refund")
    if ref["status"] != "requested":
        raise ToolError(f"refund is already {ref['status']}")
    if status == "issued":
        order = w.one("SELECT * FROM orders WHERE id = ?", (ref["order_id"],))
        already = w.scalar("SELECT COALESCE(SUM(amount_cents), 0) FROM refunds WHERE order_id = ? AND status = 'issued'",
                           (ref["order_id"],))
        if already + ref["amount_cents"] > order["total_cents"]:
            raise ToolError("refunds would exceed the order total")
    w.update("refunds", ref["id"], {"status": status, "decided_at": w.ts(), "decision_note": a.get("note", "")},
             ctx.writer)
    return {"refund_id": ref["id"], "status": status, "amount": money(ref["amount_cents"])}


@tool("issue_refund", "Approve a requested refund and send the money back to the customer's original payment method.",
      {"refund_id": I("Refund id"), "note": S("Decision note")}, ("refund_id", "note"), writes=True)
def issue_refund(ctx: ToolContext, a: dict):
    return _decide_refund(ctx, a, "issued")


@tool("deny_refund", "Deny a requested refund.", {"refund_id": I("Refund id"), "note": S("Reason for denial")},
      ("refund_id", "note"), writes=True)
def deny_refund(ctx: ToolContext, a: dict):
    return _decide_refund(ctx, a, "denied")


@tool("create_ticket", "Open a support ticket.",
      {"subject": S("Subject"), "body": S("Summary of the request"), "category": S("Category", TICKET_CATEGORIES),
       "customer_id": S("Customer id"), "vendor_id": S("Vendor id"), "source_message_id": I("Inbox message id")},
      ("subject", "body", "category"), writes=True)
def create_ticket(ctx: ToolContext, a: dict):
    w = ctx.world
    if a.get("customer_id"):
        _require(w.one("SELECT id FROM customers WHERE id = ?", (a["customer_id"],)), "customer")
    tid = w.insert(
        "tickets",
        {"customer_id": a.get("customer_id"), "vendor_id": a.get("vendor_id"), "subject": a["subject"],
         "body": a["body"], "category": a["category"], "status": "open", "source_message_id": a.get("source_message_id"),
         "created_at": w.ts(), "updated_at": w.ts()},
        ctx.writer, a.get("source_message_id"),
    )
    return {"ticket_id": int(tid), "status": "open"}


@tool("list_tickets", "List support tickets.",
      {"status": S("Filter by status", ["open", "pending", "resolved"]), "category": S("Category", TICKET_CATEGORIES)})
def list_tickets(ctx: ToolContext, a: dict):
    sql, params = "SELECT * FROM tickets WHERE 1=1", []
    for f in ("status", "category"):
        if a.get(f):
            sql += f" AND {f} = ?"
            params.append(a[f])
    rows = ctx.world.all(sql + " ORDER BY id", params)
    return [{**r, "subject": External(r["subject"]), "body": External(r["body"])} for r in rows]


@tool("get_ticket", "Get one support ticket.", {"ticket_id": I("Ticket id")}, ("ticket_id",))
def get_ticket(ctx: ToolContext, a: dict):
    r = _require(ctx.world.one("SELECT * FROM tickets WHERE id = ?", (a["ticket_id"],)), "ticket")
    return {**r, "subject": External(r["subject"]), "body": External(r["body"])}


@tool("update_ticket", "Set a ticket's status: open, pending (waiting on the customer) or resolved.",
      {"ticket_id": I("Ticket id"), "status": S("New status", ["open", "pending", "resolved"])},
      ("ticket_id", "status"), writes=True)
def update_ticket(ctx: ToolContext, a: dict):
    _require(ctx.world.one("SELECT id FROM tickets WHERE id = ?", (a["ticket_id"],)), "ticket")
    ctx.world.update("tickets", a["ticket_id"], {"status": a["status"], "updated_at": ctx.world.ts()}, ctx.writer)
    return {"ticket_id": a["ticket_id"], "status": a["status"]}


# --- notes and email -------------------------------------------------------------------------------------------------


@tool("add_note", "Add an internal note to a record. Later job runs and staff will read it.",
      {"entity_type": S("Record type", ENTITY_TYPES), "entity_id": S("Record id"), "body": S("Note text")},
      ("entity_type", "entity_id", "body"), writes=True)
def add_note(ctx: ToolContext, a: dict):
    nid = ctx.world.insert(
        "notes",
        {"entity_type": a["entity_type"], "entity_id": str(a["entity_id"]), "body": a["body"],
         "author": f"agent:{ctx.writer.name}", "created_at": ctx.world.ts()},
        ctx.writer,
    )
    return {"note_id": int(nid)}


@tool("get_notes", "Read internal notes on a record.",
      {"entity_type": S("Record type", ENTITY_TYPES), "entity_id": S("Record id")}, ("entity_type", "entity_id"))
def get_notes(ctx: ToolContext, a: dict):
    return ctx.world.all("SELECT * FROM notes WHERE entity_type = ? AND entity_id = ? ORDER BY id",
                         (a["entity_type"], str(a["entity_id"])))


@tool("send_email", "Send an email from the company.",
      {"to": S("Recipient email address"), "subject": S("Subject"), "body": S("Plain-text body"),
       "related_type": S("Record type this email concerns", ENTITY_TYPES), "related_id": S("Record id")},
      ("to", "subject", "body"), writes=True)
def send_email(ctx: ToolContext, a: dict):
    to = a["to"].strip()
    if "@" not in to or " " in to:
        raise ToolError("'to' must be a single email address")
    oid = ctx.world.insert(
        "outbox",
        {"sent_at": ctx.world.ts(), "to_addr": to, "subject": a["subject"], "body": a["body"],
         "related_type": a.get("related_type"), "related_id": a.get("related_id"),
         "job_run_id": ctx.writer.job_run_id},
        ctx.writer,
    )
    return {"email_id": int(oid), "status": "sent"}


# --- dispatch --------------------------------------------------------------------------------------------------------


def validate_args(t: Tool, args: Any) -> dict:
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    missing = [k for k in t.required if k not in args or args[k] in (None, "")]
    if missing:
        raise ToolError(f"missing required argument(s): {', '.join(missing)}")
    unknown = set(args) - set(t.properties)
    if unknown:
        raise ToolError(f"unknown argument(s): {', '.join(sorted(unknown))}")
    for k, spec in t.properties.items():
        if k not in args:
            continue
        v = args[k]
        if spec.get("type") == "integer":
            if isinstance(v, str) and v.strip().lstrip("-").isdigit():
                args[k] = int(v)
            elif not isinstance(v, int) or isinstance(v, bool):
                raise ToolError(f"{k} must be an integer")
        if spec.get("type") == "object" and not isinstance(v, dict):
            raise ToolError(f"{k} must be an object")
        if "enum" in spec and v not in spec["enum"]:
            raise ToolError(f"{k} must be one of {spec['enum']}")
    return args


def execute(ctx: ToolContext, name: str, args: Any) -> Any:
    t = REGISTRY.get(name)
    if t is None:
        raise ToolError(f"unknown tool {name}")
    return t.handler(ctx, validate_args(t, dict(args) if isinstance(args, dict) else args))
