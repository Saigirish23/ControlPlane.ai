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


def _make_ctx(
    domain: Domain = Domain.GENERAL,
    action_type: ActionType = ActionType.INFORMATIONAL,
    reversible: bool = True,
    data_sensitivity: DataSensitivity = DataSensitivity.LOW,
    user_role: str = "user",
) -> RequestContext:
    """Helper to build RequestContext from parameters."""
    req = ControlRequest(
        request="test",
        user_context=UserContext(user_role=user_role),
        interaction_context=InteractionContext(
            domain=domain,
            action_type=action_type,
            reversible=reversible,
            data_sensitivity=data_sensitivity,
        ),
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
