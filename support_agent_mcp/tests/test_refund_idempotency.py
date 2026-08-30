"""
test_refund_idempotency.py — Deterministic tests for refund idempotency & replay guard.

Verifies:
  1. First request is accepted and created in the database.
  2. Exact duplicate request (same order_id, amount, reason within window) is rejected.
  3. Request with different amount for same order is accepted.
  4. Request with different reason for same order is accepted.
  5. Duplicate request submitted after the time window has expired is accepted.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from support_agent_mcp import db as db_module
from support_agent_mcp.db import (
    RefundRepository,
    SCHEMA_SQL,
    db_session,
    init_db,
)
from support_agent_mcp.server import request_refund_or_replacement


@pytest.fixture
def isolated_db(monkeypatch: pytest.MonkeyPatch):
    """Create an isolated test database with seeded orders for testing."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)

    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    # Initialize schema
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    # Seed test customer and delivered order
    conn.execute(
        "INSERT INTO customers VALUES ('CUST100', 'Test Customer', 'test100@example.com', '+91-9999900000', 'Address 100', '2026-08-30T00:00:00')"
    )
    items_json = json.dumps([
        {"item_id": "ITEM101", "name": "Pasta Alfredo", "quantity": 1, "unit_price": 300.0, "total_price": 300.0},
        {"item_id": "ITEM102", "name": "Garlic Bread", "quantity": 1, "unit_price": 100.0, "total_price": 100.0},
    ])
    conn.execute(
        """INSERT INTO orders (
            order_id, customer_id, restaurant_name, restaurant_address,
            delivery_address, items_json, subtotal, delivery_fee, total_amount,
            status, payment_method, payment_status, placed_at
        ) VALUES (
            'ORD100', 'CUST100', 'Italian Bistro', '12 Street',
            'Address 100', ?, 400.0, 40.0, 440.0,
            'delivered', 'card', 'paid', '2026-08-30T00:00:00'
        )""",
        (items_json,),
    )
    conn.commit()
    conn.close()

    yield db_path

    if db_path.exists():
        db_path.unlink()


class TestRefundIdempotencyGuard:
    """Validate idempotency and duplicate rejection on request_refund_or_replacement."""

    def test_first_request_accepted(self, isolated_db):
        """1. First refund request within policy is accepted and stored in the database."""
        result = request_refund_or_replacement(
            order_id="ORD100",
            customer_id="CUST100",
            reason="Missing Garlic Bread",
            complaint_type="missing_items",
            requested_amount=100.0,
        )

        assert result["success"] is True
        assert result["status"] == "approved"
        assert result["approved_amount"] == 100.0
        assert "refund_id" in result

        # Verify DB contains exactly 1 refund request
        with db_session() as conn:
            refunds = conn.execute("SELECT * FROM refund_requests WHERE order_id = 'ORD100'").fetchall()
            assert len(refunds) == 1
            assert refunds[0]["requested_amount"] == 100.0
            assert refunds[0]["reason"] == "Missing Garlic Bread"

    def test_exact_duplicate_rejected(self, isolated_db):
        """2. Exact duplicate request with same order_id, amount, and reason is rejected."""
        # First call
        first_result = request_refund_or_replacement(
            order_id="ORD100",
            customer_id="CUST100",
            reason="Missing Garlic Bread",
            complaint_type="missing_items",
            requested_amount=100.0,
        )
        assert first_result["success"] is True

        # Second call with exact duplicate parameters within window
        dup_result = request_refund_or_replacement(
            order_id="ORD100",
            customer_id="CUST100",
            reason="Missing Garlic Bread",
            complaint_type="missing_items",
            requested_amount=100.0,
        )

        assert dup_result["success"] is False
        assert dup_result["duplicate"] is True
        assert "Duplicate refund request detected" in dup_result["error"]
        assert dup_result["existing_refund_id"] == first_result["refund_id"]

        # Verify DB still contains only 1 refund request and 1 complaint
        with db_session() as conn:
            refund_count = conn.execute("SELECT COUNT(*) FROM refund_requests WHERE order_id = 'ORD100'").fetchone()[0]
            complaint_count = conn.execute("SELECT COUNT(*) FROM complaints WHERE order_id = 'ORD100'").fetchone()[0]
            assert refund_count == 1
            assert complaint_count == 1

    def test_different_amount_accepted(self, isolated_db):
        """3. Request for the same order but different amount is accepted."""
        # First call: 100.0
        first_result = request_refund_or_replacement(
            order_id="ORD100",
            customer_id="CUST100",
            reason="Missing Garlic Bread",
            complaint_type="missing_items",
            requested_amount=100.0,
        )
        assert first_result["success"] is True

        # Second call: 150.0 (different amount)
        diff_amount_result = request_refund_or_replacement(
            order_id="ORD100",
            customer_id="CUST100",
            reason="Missing Garlic Bread",
            complaint_type="missing_items",
            requested_amount=150.0,
        )

        assert diff_amount_result["success"] is True
        assert diff_amount_result.get("duplicate") is not True

        # Verify DB contains 2 refund requests
        with db_session() as conn:
            refund_count = conn.execute("SELECT COUNT(*) FROM refund_requests WHERE order_id = 'ORD100'").fetchone()[0]
            assert refund_count == 2

    def test_different_reason_accepted(self, isolated_db):
        """4. Request for the same order and amount but different reason is accepted."""
        # First call: reason A
        first_result = request_refund_or_replacement(
            order_id="ORD100",
            customer_id="CUST100",
            reason="Missing Garlic Bread",
            complaint_type="missing_items",
            requested_amount=100.0,
        )
        assert first_result["success"] is True

        # Second call: reason B (different reason)
        diff_reason_result = request_refund_or_replacement(
            order_id="ORD100",
            customer_id="CUST100",
            reason="Food was delivered cold and spilled",
            complaint_type="food_quality",
            requested_amount=100.0,
        )

        assert diff_reason_result["success"] is True
        assert diff_reason_result.get("duplicate") is not True

        # Verify DB contains 2 refund requests
        with db_session() as conn:
            refund_count = conn.execute("SELECT COUNT(*) FROM refund_requests WHERE order_id = 'ORD100'").fetchone()[0]
            assert refund_count == 2

    def test_duplicate_after_expiry_accepted(self, isolated_db):
        """5. Duplicate request submitted after the time window has expired is accepted."""
        # Manually insert an older refund request created 20 minutes ago (outside 10 min window)
        old_time = (datetime.utcnow() - timedelta(minutes=20)).isoformat()
        with db_session() as conn:
            conn.execute(
                """INSERT INTO refund_requests (
                    refund_id, order_id, customer_id, item_ids_json, reason,
                    requested_amount, approved_amount, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "REF-OLD100",
                    "ORD100",
                    "CUST100",
                    "[]",
                    "Missing Garlic Bread",
                    100.0,
                    100.0,
                    "approved",
                    old_time,
                ),
            )

        # Call request_refund_or_replacement with identical details
        result = request_refund_or_replacement(
            order_id="ORD100",
            customer_id="CUST100",
            reason="Missing Garlic Bread",
            complaint_type="missing_items",
            requested_amount=100.0,
        )

        assert result["success"] is True
        assert result.get("duplicate") is not True
        assert result["refund_id"] != "REF-OLD100"

        # Verify DB contains 2 refund requests now (the old one + the new one)
        with db_session() as conn:
            refund_count = conn.execute("SELECT COUNT(*) FROM refund_requests WHERE order_id = 'ORD100'").fetchone()[0]
            assert refund_count == 2
