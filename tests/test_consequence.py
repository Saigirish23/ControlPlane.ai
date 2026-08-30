"""Tests for ConsequenceEngine."""

import pytest

from controlplane.consequence_engine import ConsequenceEngine
from controlplane.context_extractor import ContextExtractor, RequestContext
from controlplane.models import (
    ActionType,
    ConsequenceTier,
    ControlRequest,
    DataSensitivity,
    Domain,
    InteractionContext,
    UserContext,
)


from typing import Optional


def _make_ctx(
    domain: Domain = Domain.GENERAL,
    action_type: ActionType = ActionType.INFORMATIONAL,
    reversible: bool = True,
    data_sensitivity: DataSensitivity = DataSensitivity.LOW,
    user_role: str = "user",
    tool_name: Optional[str] = None,
    parameters: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> RequestContext:
    """Helper to build RequestContext from parameters."""
    meta = dict(metadata or {})
    if tool_name:
        meta["tool_name"] = tool_name
    if parameters:
        meta["parameters"] = parameters
    req = ControlRequest(
        request="test",
        user_context=UserContext(user_role=user_role),
        interaction_context=InteractionContext(
            domain=domain,
            action_type=action_type,
            reversible=reversible,
            data_sensitivity=data_sensitivity,
        ),
        metadata=meta,
    )
    return ContextExtractor().extract(req)


class TestConsequenceEngine:
    """Test consequence classification across all combos."""

    def setup_method(self):
        self.engine = ConsequenceEngine()

    # ── HIGH consequence ────────────────────────────────────────

    def test_irreversible_external_action_finance(self):
        ctx = _make_ctx(
            domain=Domain.FINANCE,
            action_type=ActionType.EXTERNAL_ACTION,
            reversible=False,
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.HIGH
        assert "finance" in result.factors
        assert "irreversible" in result.factors
        assert "external_action" in result.factors

    def test_irreversible_external_action_healthcare(self):
        ctx = _make_ctx(
            domain=Domain.HEALTHCARE,
            action_type=ActionType.EXTERNAL_ACTION,
            reversible=False,
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.HIGH

    def test_irreversible_external_action_general(self):
        ctx = _make_ctx(
            domain=Domain.GENERAL,
            action_type=ActionType.EXTERNAL_ACTION,
            reversible=False,
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.HIGH

    def test_high_sensitivity_external_action(self):
        ctx = _make_ctx(
            domain=Domain.GENERAL,
            action_type=ActionType.EXTERNAL_ACTION,
            reversible=True,
            data_sensitivity=DataSensitivity.HIGH,
        )
        result = self.engine.evaluate(ctx)
        # Could be HIGH (high_sensitivity_external) or MEDIUM (reversible_external)
        # The high sensitivity rule has priority 18 < 35, so HIGH wins
        assert result.tier == ConsequenceTier.HIGH

    # ── MEDIUM consequence ──────────────────────────────────────

    def test_finance_decision(self):
        ctx = _make_ctx(
            domain=Domain.FINANCE,
            action_type=ActionType.DECISION,
            reversible=True,
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.MEDIUM

    def test_reversible_external_action(self):
        ctx = _make_ctx(
            domain=Domain.GENERAL,
            action_type=ActionType.EXTERNAL_ACTION,
            reversible=True,
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.MEDIUM

    def test_legal_decision(self):
        ctx = _make_ctx(
            domain=Domain.LEGAL,
            action_type=ActionType.DECISION,
            reversible=True,
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.MEDIUM

    def test_sensitive_informational_high_data(self):
        ctx = _make_ctx(
            domain=Domain.SECURITY,
            action_type=ActionType.INFORMATIONAL,
            reversible=True,
            data_sensitivity=DataSensitivity.HIGH,
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.MEDIUM

    # ── LOW consequence ─────────────────────────────────────────

    def test_general_informational(self):
        ctx = _make_ctx(
            domain=Domain.GENERAL,
            action_type=ActionType.INFORMATIONAL,
            reversible=True,
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.LOW

    def test_general_informational_low_sensitivity(self):
        ctx = _make_ctx(
            domain=Domain.GENERAL,
            action_type=ActionType.INFORMATIONAL,
            reversible=True,
            data_sensitivity=DataSensitivity.LOW,
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.LOW

    # ── Reason and factors ──────────────────────────────────────

    def test_result_has_reason(self):
        ctx = _make_ctx()
        result = self.engine.evaluate(ctx)
        assert result.reason
        assert isinstance(result.reason, str)

    def test_result_has_factors(self):
        ctx = _make_ctx(
            domain=Domain.FINANCE,
            action_type=ActionType.EXTERNAL_ACTION,
            reversible=False,
        )
        result = self.engine.evaluate(ctx)
        assert isinstance(result.factors, list)
        assert len(result.factors) > 0

    # ── Enterprise Policy Overrides (Refund Limits) ─────────────

    def test_refund_within_policy_limit_returns_low(self):
        """ConsequenceEngine directly evaluates a refund within limit (<= ₹200) as LOW tier."""
        ctx = _make_ctx(
            domain=Domain.FINANCE,
            action_type=ActionType.EXTERNAL_ACTION,
            reversible=False,
            data_sensitivity=DataSensitivity.HIGH,
            tool_name="request_refund_or_replacement",
            parameters={"requested_amount": 179.0, "order_id": "ORD002"},
            metadata={"refund_limit": 200.0},
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.LOW
        assert "within_policy_limit" in result.factors
        assert "finance" in result.factors
        assert "within auto-approval policy limit" in result.reason

    def test_refund_above_policy_limit_remains_high(self):
        """ConsequenceEngine directly evaluates a refund exceeding limit (> ₹200) as HIGH tier."""
        ctx = _make_ctx(
            domain=Domain.FINANCE,
            action_type=ActionType.EXTERNAL_ACTION,
            reversible=False,
            data_sensitivity=DataSensitivity.HIGH,
            tool_name="request_refund_or_replacement",
            parameters={"requested_amount": 587.0, "order_id": "ORD004"},
            metadata={"refund_limit": 200.0},
        )
        result = self.engine.evaluate(ctx)
        assert result.tier == ConsequenceTier.HIGH
        assert "finance" in result.factors
        assert "irreversible" in result.factors
        assert "within_policy_limit" not in result.factors
