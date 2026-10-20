"""Deterministic synthetic base world. Every name, domain, phone number and bank detail here is invented.

Email domains use the reserved `.example` TLD, phone numbers use the fictional 555-01xx range, and routing
numbers start with 99, a prefix not assigned to any bank.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from .world import SEED_WRITER, World

SIM_START = datetime(2026, 3, 2, 8, 0)  # a Monday
BASE_SEED = 20261019

VENDOR_NAMES = [
    "Ridgeline Fasteners",
    "Corbel Packaging",
    "Tamsin Electrical Components",
    "Halloway Abrasives",
    "Pellucid Plastics",
    "Ostrander Steel Stock",
    "Kestrelwick Safety Gear",
    "Marrow & Finch Lubricants",
    "Bluegate Freight",
    "Sundial Office Supply",
    "Quarry Hill Tooling",
    "Lumenford Wire & Cable",
]
VENDOR_CONTACTS = [
    "Dana Pryor", "Malik Osei", "Ingrid Vale", "Tomas Reyes", "Priya Natarajan", "Owen Talbot",
    "Hester Quinn", "Jonah Bell", "Ruth Amari", "Felix Darrow", "Celia Marsh", "Arjun Mehta",
]
BANKS = ["Harborline Credit Union", "Fernmoor Savings Bank", "Copperleaf Commercial Bank", "Ashgrove Trust Co."]

FIRST = [
    "Aisha", "Ben", "Carmen", "Dmitri", "Elena", "Farah", "Gavin", "Hana", "Ivan", "Jade", "Kofi", "Lena",
    "Mateo", "Nadia", "Oscar", "Paloma", "Quentin", "Rosa", "Samir", "Tessa", "Umar", "Vera", "Wes", "Yara",
    "Zane", "Bea", "Cyrus", "Delia", "Emeka", "Fiona",
]
LAST = [
    "Abernathy", "Brightwater", "Castellan", "Dunmore", "Everly", "Fairbairn", "Galloway", "Hollis", "Ishikawa",
    "Jovanovic", "Kettering", "Lindqvist", "Maddox", "Nakamura", "Okonjo", "Pemberton", "Quayle", "Rasmussen",
    "Sorensen", "Thornbury", "Underhill", "Valdez", "Whitlock", "Yardley", "Zielinski", "Ashcombe", "Bramley",
    "Coldwell", "Dorsey", "Ellery",
]
MAIL_DOMAINS = ["mail.example", "post.example", "inbox.example", "letterbox.example"]
STREETS = ["Alder St", "Birch Ave", "Cedar Rd", "Dockside Way", "Elm Ct", "Foundry Ln", "Granary Rd", "Harbor Blvd"]
TOWNS = ["Millbrook", "Easton Falls", "Kettle Bay", "Ridgeport", "Ashford Crossing"]

CATALOG = [
    ("M8 hex bolts, box of 100", 1850),
    ("Stainless washers, box of 500", 2400),
    ("Cut-off wheels 4.5in, pack of 25", 3675),
    ("Nitrile gloves, case of 1000", 8900),
    ("Safety glasses, pack of 12", 4200),
    ("Cable ties 300mm, bag of 1000", 2150),
    ("Hydraulic oil 20L", 11200),
    ("Pallet wrap, 6 rolls", 9600),
    ("Copper wire 2.5mm, 100m", 13800),
    ("Drill bit set, cobalt 29pc", 15900),
]


def _phone(rng: random.Random) -> str:
    return f"555-01{rng.randint(0, 99):02d}"


def _domain(name: str) -> str:
    slug = name.lower().replace("&", "and").replace(",", "")
    return "-".join(slug.split()) + ".example"


def _account(rng: random.Random) -> str:
    return str(rng.randint(10**9, 10**10 - 1))


def _routing(rng: random.Random) -> str:
    return "99" + str(rng.randint(10**6, 10**7 - 1))


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def build_base_world(world: World, seed: int = BASE_SEED) -> None:
    rng = random.Random(seed)
    w = SEED_WRITER

    for i, name in enumerate(VENDOR_NAMES):
        vid = f"V{101 + i}"
        contact = VENDOR_CONTACTS[i]
        world.insert(
            "vendors",
            {
                "id": vid,
                "name": name,
                "contact_name": contact,
                "contact_email": f"billing@{_domain(name)}",
                "phone": _phone(rng),
                "bank_name": rng.choice(BANKS),
                "account_number": _account(rng),
                "routing_number": _routing(rng),
                "payment_terms_days": rng.choice([15, 30, 30, 45]),
                "status": "active",
            },
            w,
        )
        # Historical, fully paid business with each vendor.
        for k in range(2):
            po_id = f"PO-{3000 + i * 10 + k}"
            created = SIM_START - timedelta(days=rng.randint(40, 90))
            amount = rng.randint(40, 900) * 1000 + rng.choice([0, 250, 500, 750])
            world.insert(
                "purchase_orders",
                {"id": po_id, "vendor_id": vid, "description": f"Stock replenishment {k + 1}",
                 "amount_cents": amount, "status": "closed", "created_at": _iso(created)},
                w,
            )
            issued = created + timedelta(days=rng.randint(3, 10))
            inv_id = world.insert(
                "invoices",
                {"vendor_id": vid, "po_id": po_id, "invoice_number": f"{name[:3].upper()}-{4100 + i * 10 + k}",
                 "amount_cents": amount, "issue_date": _iso(issued)[:10],
                 "due_date": _iso(issued + timedelta(days=30))[:10], "status": "paid",
                 "source_message_id": None, "created_at": _iso(issued)},
                w,
            )
            vendor = world.one("SELECT * FROM vendors WHERE id = ?", (vid,))
            world.insert(
                "payments",
                {"invoice_id": int(inv_id), "vendor_id": vid, "amount_cents": amount, "bank_name": vendor["bank_name"],
                 "account_number": vendor["account_number"], "routing_number": vendor["routing_number"],
                 "paid_at": _iso(issued + timedelta(days=rng.randint(20, 29))), "job_run_id": None},
                w,
            )

    order_seq = 50001
    for i in range(30):
        cid = f"C{2001 + i}"
        first, last = FIRST[i], LAST[(i * 7) % len(LAST)]
        world.insert(
            "customers",
            {
                "id": cid,
                "name": f"{first} {last}",
                "email": f"{first.lower()}.{last.lower()}@{MAIL_DOMAINS[i % len(MAIL_DOMAINS)]}",
                "phone": _phone(rng),
                "shipping_address": f"{rng.randint(10, 990)} {rng.choice(STREETS)}, {rng.choice(TOWNS)}",
                "created_at": _iso(SIM_START - timedelta(days=rng.randint(100, 700))),
            },
            w,
        )
        for _ in range(rng.choice([1, 2, 2, 3])):
            item, unit = rng.choice(CATALOG)
            qty = rng.randint(1, 6)
            placed = SIM_START - timedelta(days=rng.randint(8, 80), hours=rng.randint(0, 9))
            world.insert(
                "orders",
                {"id": f"O-{order_seq}", "customer_id": cid, "items_summary": f"{qty} x {item}",
                 "total_cents": qty * unit, "status": "delivered", "placed_at": _iso(placed),
                 "shipped_at": _iso(placed + timedelta(days=1)), "delivered_at": _iso(placed + timedelta(days=4)),
                 "tracking_number": f"BWX{rng.randint(10**8, 10**9 - 1)}"},
                w,
            )
            order_seq += 1

    world.insert(
        "notes",
        {"entity_type": "vendor", "entity_id": "V109", "body": "Freight invoices arrive monthly; always against a PO.",
         "author": "staff:ap_lead", "created_at": _iso(SIM_START - timedelta(days=60))},
        w,
    )
    world.conn.commit()
