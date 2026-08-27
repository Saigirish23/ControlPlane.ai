"""
models.py — Pydantic domain models for the food delivery support system.

All models are used for:
  - SQLite ORM-style mapping (via dataclasses + raw SQL)
  - MCP tool input/output validation
  - Gemini agent response parsing
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Enumerations ─────────────────────────────────────────────────────────────

class OrderStatus(str, Enum):
    PLACED           = "placed"
    CONFIRMED        = "confirmed"
    PREPARING        = "preparing"
    PICKED_UP        = "picked_up"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED        = "delivered"
    CANCELLED        = "cancelled"
    REFUNDED         = "refunded"


class ComplaintType(str, Enum):
    LATE_DELIVERY     = "late_delivery"
    WRONG_ORDER       = "wrong_order"
    MISSING_ITEMS     = "missing_items"
    FOOD_QUALITY      = "food_quality"
    DAMAGED_PACKAGING = "damaged_packaging"
    PAYMENT_ISSUE     = "payment_issue"
    OTHER             = "other"


class ComplaintStatus(str, Enum):
    OPEN       = "open"
    IN_REVIEW  = "in_review"
    RESOLVED   = "resolved"
    ESCALATED  = "escalated"


class EscalationUrgency(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
    URGENT = "urgent"


class RefundStatus(str, Enum):
    PENDING   = "pending"
    APPROVED  = "approved"
    REJECTED  = "rejected"
    PROCESSED = "processed"


# ── Core Domain Models ────────────────────────────────────────────────────────

class Customer(BaseModel):
    customer_id:  str
    name:         str
    email:        str
    phone:        str
    address:      str
    created_at:   datetime = Field(default_factory=datetime.utcnow)


class OrderItem(BaseModel):
    item_id:     str
    name:        str
    quantity:    int
    unit_price:  float
    total_price: float
    notes:       Optional[str] = None


class DeliveryPartner(BaseModel):
    partner_id:       str
    name:             str
    phone:            str
    vehicle_type:     str          # e.g. "bicycle", "motorcycle", "car"
    current_location: str          # Human-readable area name for mock
    rating:           float        # 1.0 – 5.0
    is_available:     bool = True


class Order(BaseModel):
    order_id:             str
    customer_id:          str
    restaurant_name:      str
    restaurant_address:   str
    delivery_address:     str
    items:                List[OrderItem]
    subtotal:             float
    delivery_fee:         float
    total_amount:         float
    status:               OrderStatus
    payment_method:       str       # "card", "cash", "wallet"
    payment_status:       str       # "paid", "pending", "failed"
    delivery_partner_id:  Optional[str] = None
    placed_at:            datetime
    estimated_delivery:   Optional[datetime] = None
    delivered_at:         Optional[datetime] = None
    special_instructions: Optional[str] = None


class Complaint(BaseModel):
    complaint_id:  str
    order_id:      str
    customer_id:   str
    type:          ComplaintType
    description:   str
    status:        ComplaintStatus = ComplaintStatus.OPEN
    created_at:    datetime = Field(default_factory=datetime.utcnow)
    resolved_at:   Optional[datetime] = None
    resolution:    Optional[str] = None


class RefundRequest(BaseModel):
    refund_id:       str
    order_id:        str
    customer_id:     str
    item_ids:        List[str]       # Which items to refund; empty = full order
    reason:          str
    requested_amount: float
    approved_amount:  Optional[float] = None
    status:          RefundStatus = RefundStatus.PENDING
    created_at:      datetime = Field(default_factory=datetime.utcnow)
    processed_at:    Optional[datetime] = None
    notes:           Optional[str] = None


class EscalationTicket(BaseModel):
    ticket_id:        str
    order_id:         str
    customer_id:      str
    complaint_id:     Optional[str] = None
    reason:           str
    urgency:          EscalationUrgency
    assigned_helpline: str
    human_agent:      Optional[str] = None    # Name of human agent if assigned
    status:           str = "open"            # "open", "assigned", "closed"
    created_at:       datetime = Field(default_factory=datetime.utcnow)
    notes:            Optional[str] = None


# ── MCP Tool Response Wrappers ────────────────────────────────────────────────

class OrderStatusResponse(BaseModel):
    """Returned by get_order_details tool."""
    order_id:            str
    status:              OrderStatus
    restaurant_name:     str
    items_summary:       str           # e.g. "2x Burger, 1x Fries"
    total_amount:        float
    payment_status:      str
    placed_at:           str           # ISO string
    estimated_delivery:  Optional[str] = None
    delivered_at:        Optional[str] = None
    delivery_address:    str
    special_instructions: Optional[str] = None


class TrackingResponse(BaseModel):
    """Returned by track_delivery_partner tool."""
    order_id:         str
    order_status:     OrderStatus
    partner_name:     Optional[str] = None
    partner_phone:    Optional[str] = None
    partner_rating:   Optional[float] = None
    current_location: Optional[str] = None
    vehicle_type:     Optional[str] = None
    eta_minutes:      Optional[int] = None    # Simulated ETA
    message:          str                      # Human-readable status message


class RefundResponse(BaseModel):
    """Returned by request_refund_or_replacement tool."""
    refund_id:        str
    status:           RefundStatus
    requested_amount: float
    approved_amount:  Optional[float] = None
    message:          str
    requires_human:   bool = False


class EscalationResponse(BaseModel):
    """Returned by escalate_to_human_agent tool."""
    ticket_id:         str
    urgency:           EscalationUrgency
    helpline_number:   str
    estimated_wait:    str      # e.g. "2-5 minutes"
    message:           str
