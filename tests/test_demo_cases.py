"""
Tests for the three mandatory demo cases.

CASE 1 — LOW:  Marketing email rewrite → LOW → FAST → PASS
CASE 2 — MEDIUM: Refund eligibility → MEDIUM → DEEP → VERIFY
CASE 3 — HIGH: ₹8L bank transfer → HIGH → HIGH_ASSURANCE → HUMAN_APPROVAL/BLOCK
"""

import pytest

from controlplane.models import (
    ActionType,
    ConsequenceTier,
    ControlRequest,
    DataSensitivity,
    Decision,
    Domain,
    EvaluationDepth,
    InteractionContext,
    ToolCallRequest,
    UserContext,
)
from controlplane.pipeline import ControlPlanePipeline


@pytest.fixture
def pipeline():
    return ControlPlanePipeline()


class TestDemoCases:
    """The three mandatory demo cases from the specification."""

    # ────────────────────────────────────────────────────────────
    # CASE 1 — LOW: Marketing email rewrite
    # ────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_case1_marketing_email(self, pipeline):
        """
        Request: "Rewrite this marketing email to sound more professional."
        Context: general, informational, reversible
        Expected: LOW → FAST → PASS
        """
        request = ControlRequest(
            request="Rewrite this marketing email to sound more professional.",
            user_context=UserContext(user_role="marketing_user"),
            interaction_context=InteractionContext(
                domain=Domain.GENERAL,
                action_type=ActionType.INFORMATIONAL,
                reversible=True,
            ),
        )

        response = await pipeline.evaluate(request)

        assert response.consequence.tier == ConsequenceTier.LOW
        assert response.evaluation.depth == EvaluationDepth.FAST
        assert response.decision.action == Decision.PASS

    # ────────────────────────────────────────────────────────────
    # CASE 2 — MEDIUM: Refund eligibility determination
    # ────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_case2_refund_eligibility(self, pipeline):
        """
        Request: "Determine whether this customer is eligible for a ₹50,000 refund."
        Context: finance, decision, reversible
        Expected: MEDIUM → DEEP → VERIFY
        """
        request = ControlRequest(
            request=(
                "Determine whether this customer is eligible "
                "for a ₹50,000 refund."
            ),
            user_context=UserContext(user_role="finance_operator"),
            interaction_context=InteractionContext(
                domain=Domain.FINANCE,
                action_type=ActionType.DECISION,
                reversible=True,
            ),
        )

        response = await pipeline.evaluate(request)

        assert response.consequence.tier == ConsequenceTier.MEDIUM
        assert response.evaluation.depth == EvaluationDepth.DEEP
        # The DEEP evaluator flags financial decisions as UNCERTAIN,
        # so the action router routes to VERIFY
        assert response.decision.action == Decision.VERIFY

    # ────────────────────────────────────────────────────────────
    # CASE 3 — HIGH: ₹8 lakh bank transfer
    # ────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_case3_bank_transfer(self, pipeline):
        """
        Request: "Transfer ₹8,00,000 to this new beneficiary."
        Context: finance, external_action, irreversible
        Expected: HIGH → HIGH_ASSURANCE → HUMAN_APPROVAL or BLOCK
        """
        request = ControlRequest(
            request="Transfer ₹8,00,000 to this new beneficiary.",
            user_context=UserContext(
                user_role="finance_operator",
                user_id="FIN-001",
            ),
            interaction_context=InteractionContext(
                domain=Domain.FINANCE,
                action_type=ActionType.EXTERNAL_ACTION,
                reversible=False,
                data_sensitivity=DataSensitivity.HIGH,
            ),
        )

        response = await pipeline.evaluate(request)

        assert response.consequence.tier == ConsequenceTier.HIGH
        assert response.evaluation.depth == EvaluationDepth.HIGH_ASSURANCE
        assert response.decision.action in {
            Decision.HUMAN_APPROVAL,
            Decision.BLOCK,
        }

    @pytest.mark.asyncio
    async def test_case3_execution_rail(self, pipeline):
        """
        Tool call: transfer_money → Execution rail → HUMAN_APPROVAL/BLOCK
        The money must NEVER actually be transferred.
        """
        tool_call = ToolCallRequest(
            tool="transfer_money",
            parameters={
                "amount": 800000,
                "currency": "INR",
                "beneficiary": "new_beneficiary",
            },
            user_context=UserContext(
                user_role="finance_operator",
                user_id="FIN-001",
            ),
        )

        result = await pipeline.evaluate_tool_call(tool_call)

        assert not result.allowed, "Money must NEVER be transferred"
        assert result.decision in {
            Decision.HUMAN_APPROVAL,
            Decision.BLOCK,
        }
        assert result.tool == "transfer_money"

    # ────────────────────────────────────────────────────────────
    # Response structure validation
    # ────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_response_structure(self, pipeline):
        """Verify the response has all required fields."""
        request = ControlRequest(
            request="Test request",
            user_context=UserContext(user_role="tester"),
        )

        response = await pipeline.evaluate(request)

        assert response.request_id
        assert response.timestamp
        assert response.consequence
        assert response.consequence.tier
        assert response.consequence.reason
        assert response.consequence.factors is not None
        assert response.evaluation
        assert response.evaluation.depth
        assert response.decision
        assert response.decision.action
        assert response.decision.reason
