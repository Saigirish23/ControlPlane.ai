"""
server.py — FastMCP Food Delivery Customer Support Server.

Exposes 8 tools that the Gemini support agent can call:
  1. get_order_details(order_id)
  2. track_delivery_partner(order_id)
  3. request_refund_or_replacement(order_id, customer_id, reason, item_ids, requested_amount)
  4. escalate_to_human_agent(order_id, customer_id, reason, urgency)
  5. get_order_history(customer_id)
  6. check_refund_status(order_id)
  7. update_delivery_instructions(order_id, new_instructions)
  8. list_order_complaints(order_id)

All tools query the SQLite DB via the repository layer in db.py.
The server is designed to be wrapped by a forward proxy (proxy/base_proxy.py) — it
never talks to the agent directly in production flow.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import List, Optional

from mcp.server.mcpserver import MCPServer

from support_agent_mcp.config import (
    HELPLINE_GENERAL,
    HELPLINE_REFUNDS,
    HELPLINE_SAFETY,
    REFUND_AUTO_APPROVE_LIMIT,
)
from support_agent_mcp.db import (
    ComplaintRepository,
    DeliveryPartnerRepository,
    EscalationRepository,
    OrderRepository,
    RefundRepository,
    db_session,
)
from support_agent_mcp.models import (
    ComplaintType,
    EscalationUrgency,
    OrderStatus,
    RefundStatus,
)

# ── MCPServer app instance (mcp v2) ──────────────────────────────────────────
mcp = MCPServer(
    name="FoodDeliverySupport",
    instructions=(
        "You are the backend MCP server for a food delivery customer support agent. "
        "You provide tools to look up order details, track delivery partners, process "
        "refunds/replacements, and escalate issues to human agents. "
        "Always return structured, accurate data from the database."
    ),
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _fmt_dt(iso_str: Optional[str]) -> Optional[str]:
    """Format an ISO datetime string to a human-friendly form."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d %b %Y, %I:%M %p UTC")
    except ValueError:
        return iso_str


def _items_summary(items_json: str) -> str:
    """Produce a short readable summary from a JSON items array."""
    try:
        items = json.loads(items_json)
        parts = [f"{i['quantity']}x {i['name']}" for i in items]
        return ", ".join(parts)
    except Exception:
        return "items unavailable"


def _pick_helpline(urgency: EscalationUrgency, reason: str) -> str:
    """Route to the appropriate helpline based on urgency and reason keywords."""
    reason_lower = reason.lower()
    if any(kw in reason_lower for kw in ["safety", "sick", "allergic", "poison", "injury", "hurt"]):
        return HELPLINE_SAFETY
    if any(kw in reason_lower for kw in ["refund", "payment", "charge", "money", "overcharged"]):
        return HELPLINE_REFUNDS
    return HELPLINE_GENERAL


def _eta_minutes(estimated_delivery: Optional[str]) -> Optional[int]:
    """Return remaining minutes to estimated delivery (negative = overdue)."""
    if not estimated_delivery:
        return None
    try:
        eta = datetime.fromisoformat(estimated_delivery)
        delta = eta - datetime.utcnow()
        return int(delta.total_seconds() / 60)
    except Exception:
        return None


# ── Tool 1: Get Order Details ─────────────────────────────────────────────────

@mcp.tool()
def get_order_details(order_id: str) -> dict:
    """
    Retrieve complete details for a customer's order.

    Use this tool when the customer asks about:
    - Order status (where is my order?)
    - What items were ordered
    - Delivery address or special instructions
    - Payment status
    - Estimated or actual delivery time

    Args:
        order_id: The unique order identifier (e.g. 'ORD001').

    Returns:
        A dict with full order information, or an error message if not found.
    """
    row = OrderRepository.get_by_id(order_id.strip().upper())
    if not row:
        return {
            "success": False,
            "error": f"Order '{order_id}' not found. Please verify the order ID.",
        }

    eta_mins = _eta_minutes(row["estimated_delivery"])
    eta_display: Optional[str] = None
    if eta_mins is not None:
        if eta_mins > 0:
            eta_display = f"~{eta_mins} minutes remaining"
        elif eta_mins < 0:
            eta_display = f"⚠️ Overdue by {abs(eta_mins)} minutes"
        else:
            eta_display = "Arriving now"

    return {
        "success": True,
        "order_id": row["order_id"],
        "status": row["status"],
        "status_label": row["status"].replace("_", " ").title(),
        "restaurant_name": row["restaurant_name"],
        "restaurant_address": row["restaurant_address"],
        "items_summary": _items_summary(row["items_json"]),
        "subtotal": row["subtotal"],
        "delivery_fee": row["delivery_fee"],
        "total_amount": row["total_amount"],
        "payment_method": row["payment_method"],
        "payment_status": row["payment_status"],
        "delivery_address": row["delivery_address"],
        "special_instructions": row["special_instructions"],
        "placed_at": _fmt_dt(row["placed_at"]),
        "estimated_delivery": _fmt_dt(row["estimated_delivery"]),
        "estimated_delivery_countdown": eta_display,
        "delivered_at": _fmt_dt(row["delivered_at"]),
        "delivery_partner_id": row["delivery_partner_id"],
    }


# ── Tool 2: Track Delivery Partner ────────────────────────────────────────────

@mcp.tool()
def track_delivery_partner(order_id: str) -> dict:
    """
    Track the live location and status of the delivery partner assigned to an order.

    Use this tool when the customer asks:
    - Who is delivering my order?
    - Where is my delivery partner?
    - How far away is the rider?
    - Can I contact the delivery person?

    Args:
        order_id: The unique order identifier (e.g. 'ORD001').

    Returns:
        Delivery partner info including name, phone, location, ETA, and vehicle type.
        Returns a suitable message if no partner is assigned yet or order is not active.
    """
    order_row = OrderRepository.get_by_id(order_id.strip().upper())
    if not order_row:
        return {
            "success": False,
            "error": f"Order '{order_id}' not found.",
        }

    status = order_row["status"]

    # Orders not yet picked up or already completed
    if status in (OrderStatus.PLACED, OrderStatus.CONFIRMED, OrderStatus.PREPARING):
        return {
            "success": True,
            "order_id": order_id,
            "order_status": status,
            "partner_name": None,
            "message": (
                f"Your order is currently being prepared at {order_row['restaurant_name']}. "
                "A delivery partner will be assigned once the food is ready."
            ),
        }

    if status == OrderStatus.DELIVERED:
        return {
            "success": True,
            "order_id": order_id,
            "order_status": status,
            "message": f"Your order was delivered on {_fmt_dt(order_row['delivered_at'])}. Enjoy your meal! 🍽️",
        }

    if status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        return {
            "success": True,
            "order_id": order_id,
            "order_status": status,
            "message": f"This order has been {status}. No delivery partner is assigned.",
        }

    # Active delivery — fetch partner details
    partner_id = order_row["delivery_partner_id"]
    if not partner_id:
        return {
            "success": True,
            "order_id": order_id,
            "order_status": status,
            "message": "A delivery partner has not been assigned yet. Please check back shortly.",
        }

    partner_row = DeliveryPartnerRepository.get_by_id(partner_id)
    if not partner_row:
        return {
            "success": False,
            "error": "Delivery partner data unavailable. Please contact support.",
        }

    eta_mins = _eta_minutes(order_row["estimated_delivery"])
    eta_display: str
    is_late = False
    if eta_mins is None:
        eta_display = "ETA unavailable"
    elif eta_mins > 0:
        eta_display = f"~{eta_mins} minutes"
    else:
        eta_display = f"Overdue by {abs(eta_mins)} minutes ⚠️"
        is_late = True

    return {
        "success": True,
        "order_id": order_id,
        "order_status": status,
        "partner_name": partner_row["name"],
        "partner_phone": partner_row["phone"],
        "partner_rating": partner_row["rating"],
        "vehicle_type": partner_row["vehicle_type"],
        "current_location": partner_row["current_location"],
        "eta": eta_display,
        "is_late": is_late,
        "message": (
            f"Your delivery partner {partner_row['name']} is on the way! "
            f"They are currently at {partner_row['current_location']} "
            f"and will arrive in {eta_display}."
            + (" We apologise for the delay!" if is_late else "")
        ),
    }


# ── Tool 3: Request Refund or Replacement ─────────────────────────────────────

@mcp.tool()
def request_refund_or_replacement(
    order_id: str,
    customer_id: str,
    reason: str,
    complaint_type: str,
    item_ids: Optional[List[str]] = None,
    requested_amount: Optional[float] = None,
) -> dict:
    """
    Submit a refund or replacement request for a delivered or cancelled order.

    Use this tool when the customer reports:
    - Missing items in the order
    - Wrong items delivered
    - Damaged or spilled food
    - Wants a refund for a cancelled order
    - Food quality issues

    The proxy layer may intercept large refund requests for human approval.

    Args:
        order_id:         The order to raise the request against.
        customer_id:      The customer's ID.
        reason:           Description of why the refund/replacement is needed.
        complaint_type:   One of: late_delivery, wrong_order, missing_items,
                          food_quality, damaged_packaging, payment_issue, other
        item_ids:         Optional list of specific item IDs to refund. If empty/None, full order is considered.
        requested_amount: Optional explicit refund amount. If not provided, calculated from items or full order.

    Returns:
        Refund request status, approved amount, and next steps.
    """
    # Validate order exists
    order_row = OrderRepository.get_by_id(order_id.strip().upper())
    if not order_row:
        return {"success": False, "error": f"Order '{order_id}' not found."}

    # Only allow refunds on delivered or cancelled orders
    status = order_row["status"]
    if status not in (
        OrderStatus.DELIVERED.value, OrderStatus.CANCELLED.value,
        OrderStatus.OUT_FOR_DELIVERY.value,
    ):
        return {
            "success": False,
            "error": (
                f"Refund cannot be requested for an order with status '{status}'. "
                "Refunds are only available for delivered, cancelled, or out-for-delivery orders."
            ),
        }

    # Validate complaint type
    valid_types = [ct.value for ct in ComplaintType]
    if complaint_type not in valid_types:
        complaint_type = ComplaintType.OTHER.value

    # Calculate refund amount if not provided
    items = json.loads(order_row["items_json"])
    item_ids = item_ids or []
    if requested_amount is None:
        if item_ids:
            requested_amount = sum(
                i["total_price"] for i in items if i["item_id"] in item_ids
            )
        else:
            # Full order refund
            requested_amount = order_row["total_amount"]

    # Create complaint record
    complaint_id = f"COMP-{uuid.uuid4().hex[:8].upper()}"
    ComplaintRepository.create(
        complaint_id=complaint_id,
        order_id=order_id.upper(),
        customer_id=customer_id.upper(),
        complaint_type=ComplaintType(complaint_type),
        description=reason,
    )

    # Create refund record
    refund_id = f"REF-{uuid.uuid4().hex[:8].upper()}"
    RefundRepository.create(
        refund_id=refund_id,
        order_id=order_id.upper(),
        customer_id=customer_id.upper(),
        item_ids=item_ids,
        reason=reason,
        requested_amount=requested_amount,
    )

    # Auto-approve if within limit (proxy may override this)
    requires_human = requested_amount > REFUND_AUTO_APPROVE_LIMIT
    if not requires_human:
        approved_amount = requested_amount
        RefundRepository.update(
            refund_id=refund_id,
            status=RefundStatus.APPROVED,
            approved_amount=approved_amount,
            notes="Auto-approved within policy limit.",
        )
        return {
            "success": True,
            "refund_id": refund_id,
            "complaint_id": complaint_id,
            "status": RefundStatus.APPROVED.value,
            "requested_amount": requested_amount,
            "approved_amount": approved_amount,
            "requires_human_review": False,
            "message": (
                f"Your refund of ₹{approved_amount:.2f} has been approved! "
                "It will be credited to your original payment method within 3-5 business days."
            ),
        }
    else:
        return {
            "success": True,
            "refund_id": refund_id,
            "complaint_id": complaint_id,
            "status": RefundStatus.PENDING.value,
            "requested_amount": requested_amount,
            "approved_amount": None,
            "requires_human_review": True,
            "message": (
                f"Your refund request of ₹{requested_amount:.2f} has been submitted and is under review. "
                "Due to the amount involved, it requires approval from our support team. "
                "You will receive an update within 24 hours."
            ),
        }


# ── Tool 4: Escalate to Human Agent ──────────────────────────────────────────

@mcp.tool()
def escalate_to_human_agent(
    order_id: str,
    customer_id: str,
    reason: str,
    urgency: str,
    complaint_id: Optional[str] = None,
) -> dict:
    """
    Escalate a customer issue to a human support agent or helpline.

    Use this tool when:
    - The customer is very upset, angry, or explicitly asks for a human/manager
    - The issue involves safety concerns (allergic reaction, food poisoning)
    - The AI cannot resolve the issue satisfactorily
    - Multiple automated resolution attempts have failed
    - The refund request requires human approval

    Args:
        order_id:     The related order ID.
        customer_id:  The customer's ID.
        reason:       A clear description of why escalation is needed.
        urgency:      One of: low, medium, high, urgent
        complaint_id: Optional — link to an existing complaint record.

    Returns:
        Escalation ticket ID, assigned helpline number, and estimated wait time.
    """
    # Validate order
    order_row = OrderRepository.get_by_id(order_id.strip().upper())
    if not order_row:
        return {"success": False, "error": f"Order '{order_id}' not found."}

    # Validate urgency
    valid_urgencies = [u.value for u in EscalationUrgency]
    if urgency not in valid_urgencies:
        urgency = EscalationUrgency.MEDIUM.value

    urgency_enum = EscalationUrgency(urgency)
    helpline = _pick_helpline(urgency_enum, reason)

    # Estimated wait time by urgency
    wait_map = {
        EscalationUrgency.URGENT: "Under 2 minutes",
        EscalationUrgency.HIGH:   "2–5 minutes",
        EscalationUrgency.MEDIUM: "5–10 minutes",
        EscalationUrgency.LOW:    "10–20 minutes",
    }
    estimated_wait = wait_map[urgency_enum]

    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    EscalationRepository.create(
        ticket_id=ticket_id,
        order_id=order_id.upper(),
        customer_id=customer_id.upper(),
        reason=reason,
        urgency=urgency_enum,
        assigned_helpline=helpline,
        complaint_id=complaint_id,
        notes=f"Escalated via AI agent. Urgency: {urgency}.",
    )

    urgency_emoji = {
        "urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"
    }.get(urgency, "🟡")

    return {
        "success": True,
        "ticket_id": ticket_id,
        "urgency": urgency,
        "urgency_label": f"{urgency_emoji} {urgency.title()}",
        "helpline_number": helpline,
        "estimated_wait": estimated_wait,
        "complaint_id": complaint_id,
        "message": (
            f"I've escalated your issue to our human support team. "
            f"Your ticket ID is **{ticket_id}**. "
            f"Please call **{helpline}** — estimated wait time is {estimated_wait}. "
            "An agent will be with you shortly and will have full context of your order."
        ),
    }


# ── Tool 5: Get Order History ─────────────────────────────────────────────────

@mcp.tool()
def get_order_history(customer_id: str) -> dict:
    """
    Retrieve all past orders for a customer — most recent first.

    Use this tool when the customer:
    - Doesn't remember their order ID
    - Asks "what did I order recently?"
    - Wants to raise a complaint about a past order but can't recall the ID
    - Asks how many orders they've placed

    Args:
        customer_id: The customer's unique ID (e.g. 'CUST001').

    Returns:
        A list of orders with key details (ID, restaurant, status, total, date).
    """
    rows = OrderRepository.get_by_customer(customer_id.strip().upper())
    if not rows:
        return {
            "success": True,
            "customer_id": customer_id,
            "order_count": 0,
            "orders": [],
            "message": "No orders found for this customer.",
        }

    orders = []
    for r in rows:
        eta_mins = _eta_minutes(r["estimated_delivery"])
        countdown = None
        if eta_mins is not None and r["status"] in (
            OrderStatus.OUT_FOR_DELIVERY.value, OrderStatus.PICKED_UP.value
        ):
            countdown = f"~{eta_mins} min remaining" if eta_mins > 0 else f"Overdue by {abs(eta_mins)} min ⚠️"

        orders.append({
            "order_id": r["order_id"],
            "restaurant_name": r["restaurant_name"],
            "items_summary": _items_summary(r["items_json"]),
            "total_amount": r["total_amount"],
            "status": r["status"],
            "status_label": r["status"].replace("_", " ").title(),
            "payment_method": r["payment_method"],
            "payment_status": r["payment_status"],
            "placed_at": _fmt_dt(r["placed_at"]),
            "delivered_at": _fmt_dt(r["delivered_at"]),
            "estimated_delivery_countdown": countdown,
        })

    return {
        "success": True,
        "customer_id": customer_id,
        "order_count": len(orders),
        "orders": orders,
    }


# ── Tool 6: Check Refund Status ───────────────────────────────────────────────

@mcp.tool()
def check_refund_status(order_id: str) -> dict:
    """
    Check the current status of all refund requests associated with an order.

    Use this tool when the customer asks:
    - "What happened to my refund?"
    - "Has my refund been approved?"
    - "When will I get my money back?"
    - "I raised a refund 2 days ago, any update?"

    Args:
        order_id: The order ID to look up refund requests for.

    Returns:
        A list of refund requests with their current status, amounts, and next steps.
    """
    order_row = OrderRepository.get_by_id(order_id.strip().upper())
    if not order_row:
        return {"success": False, "error": f"Order '{order_id}' not found."}

    rows = RefundRepository.get_by_order(order_id.strip().upper())
    if not rows:
        return {
            "success": True,
            "order_id": order_id,
            "refund_count": 0,
            "refunds": [],
            "message": "No refund requests found for this order. Would you like to raise one?",
        }

    refunds = []
    for r in rows:
        status = r["status"]
        next_step: str
        if status == RefundStatus.APPROVED.value:
            next_step = "Credited to your original payment method within 3-5 business days."
        elif status == RefundStatus.PROCESSED.value:
            next_step = "Refund has been fully processed and sent to your payment method."
        elif status == RefundStatus.REJECTED.value:
            next_step = "Refund was rejected. Please contact support for further assistance."
        else:
            next_step = "Under review by our support team. You'll hear back within 24 hours."

        refunds.append({
            "refund_id": r["refund_id"],
            "status": status,
            "status_label": status.title(),
            "requested_amount": r["requested_amount"],
            "approved_amount": r["approved_amount"],
            "reason": r["reason"],
            "created_at": _fmt_dt(r["created_at"]),
            "processed_at": _fmt_dt(r["processed_at"]),
            "notes": r["notes"],
            "next_step": next_step,
        })

    return {
        "success": True,
        "order_id": order_id,
        "refund_count": len(refunds),
        "refunds": refunds,
    }


# ── Tool 7: Update Delivery Instructions ─────────────────────────────────────

@mcp.tool()
def update_delivery_instructions(order_id: str, new_instructions: str) -> dict:
    """
    Update the special delivery instructions for an active order.

    Use this tool when the customer wants to:
    - Change drop-off location note (e.g. "leave at door" → "hand it to me")
    - Add gate code, floor number, or landmark
    - Correct a building name or apartment number
    - Add a note for the delivery partner

    Only allowed while the order is not yet delivered or cancelled.

    Args:
        order_id:         The order to update.
        new_instructions: The new delivery instructions (max 300 chars).

    Returns:
        Confirmation of the update or an error if the order state doesn't allow it.
    """
    order_row = OrderRepository.get_by_id(order_id.strip().upper())
    if not order_row:
        return {"success": False, "error": f"Order '{order_id}' not found."}

    # Disallow update for terminal states
    terminal_statuses = (
        OrderStatus.DELIVERED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REFUNDED.value,
    )
    if order_row["status"] in terminal_statuses:
        return {
            "success": False,
            "error": (
                f"Cannot update delivery instructions — order is already {order_row['status']}. "
                "Please contact support if you have a delivery concern."
            ),
        }

    # Truncate to 300 chars
    instructions = new_instructions.strip()[:300]

    with db_session() as conn:
        conn.execute(
            "UPDATE orders SET special_instructions = ? WHERE order_id = ?",
            (instructions, order_id.strip().upper()),
        )

    return {
        "success": True,
        "order_id": order_id,
        "updated_instructions": instructions,
        "order_status": order_row["status"],
        "message": (
            f"Delivery instructions updated successfully! "
            f"Your delivery partner will see: \"{instructions}\". "
            "Note: If the order has already been picked up, the partner may not receive this in time."
            if order_row["status"] == OrderStatus.OUT_FOR_DELIVERY.value
            else f"Delivery instructions updated to: \"{instructions}\"."
        ),
    }


# ── Tool 8: List Complaints for an Order ─────────────────────────────────────

@mcp.tool()
def list_order_complaints(order_id: str) -> dict:
    """
    List all complaints already filed for a given order.

    Use this tool when:
    - The customer says they already filed a complaint and wants an update
    - Before creating a new complaint, to check if a duplicate exists
    - The customer asks "did you get my complaint?"
    - Checking complaint resolution status

    Args:
        order_id: The order to look up complaints for.

    Returns:
        All complaints filed against this order with their status and resolution.
    """
    order_row = OrderRepository.get_by_id(order_id.strip().upper())
    if not order_row:
        return {"success": False, "error": f"Order '{order_id}' not found."}

    rows = ComplaintRepository.get_by_order(order_id.strip().upper())
    if not rows:
        return {
            "success": True,
            "order_id": order_id,
            "complaint_count": 0,
            "complaints": [],
            "message": "No complaints have been filed for this order.",
        }

    complaints = []
    for r in rows:
        complaints.append({
            "complaint_id": r["complaint_id"],
            "type": r["type"].replace("_", " ").title(),
            "description": r["description"],
            "status": r["status"],
            "status_label": r["status"].title(),
            "created_at": _fmt_dt(r["created_at"]),
            "resolved_at": _fmt_dt(r["resolved_at"]),
            "resolution": r["resolution"],
        })

    return {
        "success": True,
        "order_id": order_id,
        "complaint_count": len(complaints),
        "complaints": complaints,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[MCP] Starting FoodDeliverySupport MCP server...")
    mcp.run(transport="stdio")
