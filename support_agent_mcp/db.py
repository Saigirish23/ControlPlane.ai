"""
db.py — SQLite database setup, schema creation, and seed data loader.

Provides:
  - get_connection()  : yields a sqlite3 connection
  - init_db()         : creates all tables
  - seed_db()         : populates realistic mock data
  - Repository classes for each entity (thin query wrappers)
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator, List, Optional

from support_agent_mcp.config import DB_PATH
from support_agent_mcp.models import (
    ComplaintStatus, ComplaintType, EscalationUrgency,
    OrderStatus, RefundStatus,
)

# ── Connection ────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Return a sqlite3 connection with row_factory set to Row."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session() -> Generator[sqlite3.Connection, None, None]:
    """Context manager that auto-commits or rolls back."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL UNIQUE,
    phone        TEXT NOT NULL,
    address      TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_partners (
    partner_id        TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    phone             TEXT NOT NULL,
    vehicle_type      TEXT NOT NULL,
    current_location  TEXT NOT NULL,
    rating            REAL NOT NULL,
    is_available      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS orders (
    order_id              TEXT PRIMARY KEY,
    customer_id           TEXT NOT NULL REFERENCES customers(customer_id),
    restaurant_name       TEXT NOT NULL,
    restaurant_address    TEXT NOT NULL,
    delivery_address      TEXT NOT NULL,
    items_json            TEXT NOT NULL,   -- JSON array of OrderItem dicts
    subtotal              REAL NOT NULL,
    delivery_fee          REAL NOT NULL,
    total_amount          REAL NOT NULL,
    status                TEXT NOT NULL,
    payment_method        TEXT NOT NULL,
    payment_status        TEXT NOT NULL,
    delivery_partner_id   TEXT REFERENCES delivery_partners(partner_id),
    placed_at             TEXT NOT NULL,
    estimated_delivery    TEXT,
    delivered_at          TEXT,
    special_instructions  TEXT
);

CREATE TABLE IF NOT EXISTS complaints (
    complaint_id  TEXT PRIMARY KEY,
    order_id      TEXT NOT NULL REFERENCES orders(order_id),
    customer_id   TEXT NOT NULL REFERENCES customers(customer_id),
    type          TEXT NOT NULL,
    description   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    created_at    TEXT NOT NULL,
    resolved_at   TEXT,
    resolution    TEXT
);

CREATE TABLE IF NOT EXISTS refund_requests (
    refund_id         TEXT PRIMARY KEY,
    order_id          TEXT NOT NULL REFERENCES orders(order_id),
    customer_id       TEXT NOT NULL REFERENCES customers(customer_id),
    item_ids_json     TEXT NOT NULL,   -- JSON array of item IDs
    reason            TEXT NOT NULL,
    requested_amount  REAL NOT NULL,
    approved_amount   REAL,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL,
    processed_at      TEXT,
    notes             TEXT
);

CREATE TABLE IF NOT EXISTS escalation_tickets (
    ticket_id          TEXT PRIMARY KEY,
    order_id           TEXT NOT NULL REFERENCES orders(order_id),
    customer_id        TEXT NOT NULL REFERENCES customers(customer_id),
    complaint_id       TEXT REFERENCES complaints(complaint_id),
    reason             TEXT NOT NULL,
    urgency            TEXT NOT NULL,
    assigned_helpline  TEXT NOT NULL,
    human_agent        TEXT,
    status             TEXT NOT NULL DEFAULT 'open',
    created_at         TEXT NOT NULL,
    notes              TEXT
);
"""


def init_db() -> None:
    """Create all tables if they don't exist."""
    with db_session() as conn:
        conn.executescript(SCHEMA_SQL)
    print(f"[DB] Database initialised at {DB_PATH}")


# ── Seed Data ─────────────────────────────────────────────────────────────────

def _ts(dt: datetime) -> str:
    return dt.isoformat()


def seed_db() -> None:
    """Populate SQLite with realistic mock data. Safe to call multiple times (skips if data exists)."""
    with db_session() as conn:
        # Skip if already seeded
        existing = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        if existing > 0:
            print("[DB] Seed data already present, skipping.")
            return

        now = datetime.utcnow()

        # ── Customers ────────────────────────────────────────────────────────
        customers = [
            ("CUST001", "Arjun Sharma",   "arjun.sharma@email.com",  "+91-9876543210", "12 MG Road, Bengaluru, KA 560001"),
            ("CUST002", "Priya Menon",    "priya.menon@email.com",   "+91-9845012345", "45 Anna Nagar, Chennai, TN 600040"),
            ("CUST003", "Rohit Verma",    "rohit.verma@email.com",   "+91-9812345678", "7 Sector 18, Noida, UP 201301"),
            ("CUST004", "Sneha Pillai",   "sneha.pillai@email.com",  "+91-9898765432", "22 Koregaon Park, Pune, MH 411001"),
            ("CUST005", "Dev Kapoor",     "dev.kapoor@email.com",    "+91-9123456789", "5 Banjara Hills, Hyderabad, TS 500034"),
        ]
        conn.executemany(
            "INSERT INTO customers VALUES (?,?,?,?,?,?)",
            [(c[0], c[1], c[2], c[3], c[4], _ts(now)) for c in customers],
        )

        # ── Delivery Partners ─────────────────────────────────────────────────
        partners = [
            ("DP001", "Ravi Kumar",     "+91-9700000001", "motorcycle", "Indiranagar, Bengaluru",  4.7, 1),
            ("DP002", "Suresh Babu",    "+91-9700000002", "bicycle",    "T. Nagar, Chennai",       4.2, 1),
            ("DP003", "Manish Singh",   "+91-9700000003", "motorcycle", "Sector 62, Noida",        4.9, 0),
            ("DP004", "Vijay Patil",    "+91-9700000004", "car",        "Kalyani Nagar, Pune",     4.5, 1),
            ("DP005", "Arun Reddy",     "+91-9700000005", "motorcycle", "Jubilee Hills, Hyderabad",4.3, 1),
        ]
        conn.executemany(
            "INSERT INTO delivery_partners VALUES (?,?,?,?,?,?,?)",
            partners,
        )

        # ── Orders ────────────────────────────────────────────────────────────
        def make_items(items_raw):
            return json.dumps([
                {"item_id": i[0], "name": i[1], "quantity": i[2],
                 "unit_price": i[3], "total_price": i[2]*i[3], "notes": i[4]}
                for i in items_raw
            ])

        orders = [
            # ORD001 — Out for delivery, customer asking ETA
            {
                "order_id": "ORD001",
                "customer_id": "CUST001",
                "restaurant_name": "Pizza Paradise",
                "restaurant_address": "88 Church Street, Bengaluru",
                "delivery_address": "12 MG Road, Bengaluru, KA 560001",
                "items_json": make_items([
                    ("ITEM001", "Margherita Pizza (L)", 1, 349.0, None),
                    ("ITEM002", "Garlic Bread",         2, 99.0,  None),
                    ("ITEM003", "Coke 500ml",           2, 60.0,  None),
                ]),
                "subtotal": 667.0, "delivery_fee": 40.0, "total_amount": 707.0,
                "status": OrderStatus.OUT_FOR_DELIVERY,
                "payment_method": "card", "payment_status": "paid",
                "delivery_partner_id": "DP001",
                "placed_at": _ts(now - timedelta(minutes=45)),
                "estimated_delivery": _ts(now + timedelta(minutes=15)),
                "delivered_at": None, "special_instructions": "Ring the doorbell twice",
            },
            # ORD002 — Delivered, customer says items were missing
            {
                "order_id": "ORD002",
                "customer_id": "CUST002",
                "restaurant_name": "Burger Barn",
                "restaurant_address": "12 Anna Salai, Chennai",
                "delivery_address": "45 Anna Nagar, Chennai, TN 600040",
                "items_json": make_items([
                    ("ITEM004", "Double Smash Burger", 2, 259.0, None),
                    ("ITEM005", "Cheese Fries (L)",    2, 149.0, None),
                    ("ITEM006", "Chocolate Shake",     1, 179.0, None),
                ]),
                "subtotal": 995.0, "delivery_fee": 35.0, "total_amount": 1030.0,
                "status": OrderStatus.DELIVERED,
                "payment_method": "wallet", "payment_status": "paid",
                "delivery_partner_id": "DP002",
                "placed_at": _ts(now - timedelta(hours=2)),
                "estimated_delivery": _ts(now - timedelta(hours=1, minutes=15)),
                "delivered_at": _ts(now - timedelta(hours=1, minutes=10)),
                "special_instructions": None,
            },
            # ORD003 — Severely delayed, customer very unhappy (escalation scenario)
            {
                "order_id": "ORD003",
                "customer_id": "CUST003",
                "restaurant_name": "Sushi Spot",
                "restaurant_address": "3 Expressway Plaza, Noida",
                "delivery_address": "7 Sector 18, Noida, UP 201301",
                "items_json": make_items([
                    ("ITEM007", "Salmon Nigiri (8pc)", 1, 549.0, None),
                    ("ITEM008", "Dragon Roll",         1, 449.0, "No wasabi"),
                    ("ITEM009", "Miso Soup",           2, 129.0, None),
                ]),
                "subtotal": 1256.0, "delivery_fee": 60.0, "total_amount": 1316.0,
                "status": OrderStatus.OUT_FOR_DELIVERY,
                "payment_method": "card", "payment_status": "paid",
                "delivery_partner_id": "DP003",
                "placed_at": _ts(now - timedelta(hours=2, minutes=30)),
                "estimated_delivery": _ts(now - timedelta(hours=1)),  # already late!
                "delivered_at": None, "special_instructions": None,
            },
            # ORD004 — Cancelled, customer wants refund
            {
                "order_id": "ORD004",
                "customer_id": "CUST004",
                "restaurant_name": "Biryani Hub",
                "restaurant_address": "9 FC Road, Pune",
                "delivery_address": "22 Koregaon Park, Pune, MH 411001",
                "items_json": make_items([
                    ("ITEM010", "Chicken Dum Biryani (Full)", 1, 399.0, None),
                    ("ITEM011", "Raita",                      1, 69.0,  None),
                    ("ITEM012", "Gulab Jamun (4pc)",          1, 89.0,  None),
                ]),
                "subtotal": 557.0, "delivery_fee": 30.0, "total_amount": 587.0,
                "status": OrderStatus.CANCELLED,
                "payment_method": "card", "payment_status": "paid",
                "delivery_partner_id": None,
                "placed_at": _ts(now - timedelta(hours=3)),
                "estimated_delivery": None, "delivered_at": None,
                "special_instructions": "Extra spicy",
            },
            # ORD005 — Delivered but food quality complaint (soup spilled)
            {
                "order_id": "ORD005",
                "customer_id": "CUST005",
                "restaurant_name": "The Noodle House",
                "restaurant_address": "77 Road No. 10, Hyderabad",
                "delivery_address": "5 Banjara Hills, Hyderabad, TS 500034",
                "items_json": make_items([
                    ("ITEM013", "Chicken Ramen",     1, 329.0, None),
                    ("ITEM014", "Gyoza (6pc)",       1, 199.0, None),
                    ("ITEM015", "Green Tea",         2, 79.0,  None),
                ]),
                "subtotal": 686.0, "delivery_fee": 45.0, "total_amount": 731.0,
                "status": OrderStatus.DELIVERED,
                "payment_method": "cash", "payment_status": "paid",
                "delivery_partner_id": "DP005",
                "placed_at": _ts(now - timedelta(hours=1, minutes=30)),
                "estimated_delivery": _ts(now - timedelta(minutes=30)),
                "delivered_at": _ts(now - timedelta(minutes=25)),
                "special_instructions": None,
            },
        ]

        for o in orders:
            conn.execute(
                """INSERT INTO orders VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    o["order_id"], o["customer_id"], o["restaurant_name"],
                    o["restaurant_address"], o["delivery_address"], o["items_json"],
                    o["subtotal"], o["delivery_fee"], o["total_amount"],
                    o["status"].value, o["payment_method"], o["payment_status"],
                    o["delivery_partner_id"], o["placed_at"],
                    o["estimated_delivery"], o["delivered_at"],
                    o["special_instructions"],
                ),
            )

        print("[DB] Seed data loaded successfully.")
        print(f"[DB]   {len(customers)} customers | {len(partners)} delivery partners | {len(orders)} orders")


# ── Repository: Orders ────────────────────────────────────────────────────────

class OrderRepository:

    @staticmethod
    def get_by_id(order_id: str) -> Optional[sqlite3.Row]:
        with db_session() as conn:
            return conn.execute(
                "SELECT * FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()

    @staticmethod
    def get_by_customer(customer_id: str) -> List[sqlite3.Row]:
        with db_session() as conn:
            return conn.execute(
                "SELECT * FROM orders WHERE customer_id = ? ORDER BY placed_at DESC",
                (customer_id,),
            ).fetchall()

    @staticmethod
    def update_status(order_id: str, status: OrderStatus) -> None:
        with db_session() as conn:
            conn.execute(
                "UPDATE orders SET status = ? WHERE order_id = ?",
                (status.value, order_id),
            )


# ── Repository: Delivery Partners ─────────────────────────────────────────────

class DeliveryPartnerRepository:

    @staticmethod
    def get_by_id(partner_id: str) -> Optional[sqlite3.Row]:
        with db_session() as conn:
            return conn.execute(
                "SELECT * FROM delivery_partners WHERE partner_id = ?",
                (partner_id,),
            ).fetchone()


# ── Repository: Complaints ────────────────────────────────────────────────────

class ComplaintRepository:

    @staticmethod
    def create(
        complaint_id: str,
        order_id: str,
        customer_id: str,
        complaint_type: ComplaintType,
        description: str,
    ) -> None:
        with db_session() as conn:
            conn.execute(
                """INSERT INTO complaints
                   (complaint_id, order_id, customer_id, type, description, status, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (complaint_id, order_id, customer_id, complaint_type.value,
                 description, ComplaintStatus.OPEN.value, _ts(datetime.utcnow())),
            )

    @staticmethod
    def get_by_order(order_id: str) -> List[sqlite3.Row]:
        with db_session() as conn:
            return conn.execute(
                "SELECT * FROM complaints WHERE order_id = ? ORDER BY created_at DESC",
                (order_id,),
            ).fetchall()

    @staticmethod
    def update_status(complaint_id: str, status: ComplaintStatus, resolution: Optional[str] = None) -> None:
        with db_session() as conn:
            conn.execute(
                """UPDATE complaints
                   SET status = ?, resolved_at = ?, resolution = ?
                   WHERE complaint_id = ?""",
                (status.value,
                 _ts(datetime.utcnow()) if status == ComplaintStatus.RESOLVED else None,
                 resolution, complaint_id),
            )


# ── Repository: Refund Requests ───────────────────────────────────────────────

class RefundRepository:

    @staticmethod
    def create(
        refund_id: str,
        order_id: str,
        customer_id: str,
        item_ids: List[str],
        reason: str,
        requested_amount: float,
    ) -> None:
        with db_session() as conn:
            conn.execute(
                """INSERT INTO refund_requests
                   (refund_id, order_id, customer_id, item_ids_json, reason,
                    requested_amount, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (refund_id, order_id, customer_id, json.dumps(item_ids),
                 reason, requested_amount, RefundStatus.PENDING.value,
                 _ts(datetime.utcnow())),
            )

    @staticmethod
    def get_by_order(order_id: str) -> List[sqlite3.Row]:
        with db_session() as conn:
            return conn.execute(
                "SELECT * FROM refund_requests WHERE order_id = ? ORDER BY created_at DESC",
                (order_id,),
            ).fetchall()

    @staticmethod
    def update(refund_id: str, status: RefundStatus, approved_amount: Optional[float], notes: Optional[str]) -> None:
        with db_session() as conn:
            conn.execute(
                """UPDATE refund_requests
                   SET status = ?, approved_amount = ?, processed_at = ?, notes = ?
                   WHERE refund_id = ?""",
                (status.value, approved_amount,
                 _ts(datetime.utcnow()) if status in (RefundStatus.APPROVED, RefundStatus.REJECTED) else None,
                 notes, refund_id),
            )


# ── Repository: Escalation Tickets ────────────────────────────────────────────

class EscalationRepository:

    @staticmethod
    def create(
        ticket_id: str,
        order_id: str,
        customer_id: str,
        reason: str,
        urgency: EscalationUrgency,
        assigned_helpline: str,
        complaint_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        with db_session() as conn:
            conn.execute(
                """INSERT INTO escalation_tickets
                   (ticket_id, order_id, customer_id, complaint_id, reason,
                    urgency, assigned_helpline, status, created_at, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (ticket_id, order_id, customer_id, complaint_id, reason,
                 urgency.value, assigned_helpline, "open",
                 _ts(datetime.utcnow()), notes),
            )

    @staticmethod
    def get_by_order(order_id: str) -> List[sqlite3.Row]:
        with db_session() as conn:
            return conn.execute(
                "SELECT * FROM escalation_tickets WHERE order_id = ? ORDER BY created_at DESC",
                (order_id,),
            ).fetchall()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    seed_db()
