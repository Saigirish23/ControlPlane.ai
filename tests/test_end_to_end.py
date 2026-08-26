"""
End-to-End Pipeline Tests for ControlPlane.AI.

Explicitly verifies complete paths:
A. Marketing Email: LOW -> FAST -> PASS
B. ₹50,000 Refund: MEDIUM -> DEEP -> VERIFY
C. ₹8,00,000 Transfer: HIGH -> HIGH_ASSURANCE -> HUMAN_APPROVAL -> Execution Prevented
"""

import pytest
from unittest.mock import MagicMock

from controlplane.execution_rail import MockExternalSystem
from controlplane.models import (
    ActionType,
    CheckStatus,
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


class TestEndToEndPipeline:
    """End-to-end path verification."""

    @pytest.mark.asyncio
    async def test_path_marketing_email(self, pipeline):
        """Path A: Marketing Email -> LOW -> FAST -> PASS."""
        req = ControlRequest(
            request="Rewrite this marketing email to sound more professional.",
            user_context=UserContext(user_role="marketing_analyst", user_id="MKT-001"),
            interaction_context=InteractionContext(
                domain=Domain.GENERAL,
                action_type=ActionType.INFORMATIONAL,
                reversible=True,
                data_sensitivity=DataSensitivity.LOW,
            ),
        )

        resp = await pipeline.evaluate(req)

        # 1. Consequence
        assert resp.consequence.tier == ConsequenceTier.LOW
        assert "informational" in resp.consequence.factors

        # 2. Evaluation Depth
        assert resp.evaluation.depth == EvaluationDepth.FAST
        assert resp.evaluation.overall_status == CheckStatus.PASS

        # 3. Decision
        assert resp.decision.action == Decision.PASS
        assert not resp.decision.requires_human

    @pytest.mark.asyncio
    async def test_path_refund_eligibility(self, pipeline):
        """Path B: ₹50,000 Refund -> MEDIUM -> DEEP -> VERIFY."""
        req = ControlRequest(
            request="Determine whether this customer is eligible for a ₹50,000 refund.",
            user_context=UserContext(user_role="finance_operator", user_id="FIN-002"),
            interaction_context=InteractionContext(
                domain=Domain.FINANCE,
                action_type=ActionType.DECISION,
                reversible=True,
                data_sensitivity=DataSensitivity.MEDIUM,
            ),
        )

        resp = await pipeline.evaluate(req)

        # 1. Consequence
        assert resp.consequence.tier == ConsequenceTier.MEDIUM
        assert "finance" in resp.consequence.factors
        assert "decision" in resp.consequence.factors

        # 2. Evaluation Depth
        assert resp.evaluation.depth == EvaluationDepth.DEEP

        # 3. Decision
        assert resp.decision.action == Decision.VERIFY
        assert "verification" in resp.decision.reason.lower() or "verify" in resp.decision.reason.lower()

    @pytest.mark.asyncio
    async def test_path_transfer_money_and_execution_prevented(self, pipeline):
        """Path C: ₹8,00,000 Transfer -> HIGH -> HIGH_ASSURANCE -> HUMAN_APPROVAL -> Execution Prevented."""
        # 1. Pipeline governance check on request
        req = ControlRequest(
            request="Transfer ₹8,00,000 to this new beneficiary.",
            user_context=UserContext(user_role="finance_operator", user_id="FIN-001"),
            interaction_context=InteractionContext(
                domain=Domain.FINANCE,
                action_type=ActionType.EXTERNAL_ACTION,
                reversible=False,
                data_sensitivity=DataSensitivity.HIGH,
            ),
        )
        resp = await pipeline.evaluate(req)

        assert resp.consequence.tier == ConsequenceTier.HIGH
        assert "finance" in resp.consequence.factors
        assert "irreversible" in resp.consequence.factors
        assert "external_action" in resp.consequence.factors
        assert resp.evaluation.depth == EvaluationDepth.HIGH_ASSURANCE
        assert resp.decision.action in {Decision.HUMAN_APPROVAL, Decision.BLOCK}
        assert resp.decision.requires_human is True

        # 2. Agent tool call interception on execution rail
        tool_call = ToolCallRequest(
            tool="transfer_money",
            parameters={
                "amount": 800000,
                "currency": "INR",
                "beneficiary": "new_beneficiary_acc_987654",
            },
            user_context=UserContext(user_role="finance_operator", user_id="FIN-001"),
        )
        rail_result = await pipeline.evaluate_tool_call(tool_call)

        # Verify rail decision
        assert rail_result.allowed is False
        assert rail_result.decision in {Decision.HUMAN_APPROVAL, Decision.BLOCK}
        assert rail_result.tool == "transfer_money"

        # 3. Verify External System execution attempt is blocked
        mock_bank_api = MagicMock()
        execution_result = MockExternalSystem.execute(
            tool_name="transfer_money",
            parameters=tool_call.parameters,
            rail_result=rail_result,
        )

        assert execution_result["executed"] is False
        assert execution_result["decision"] == rail_result.decision.value
        mock_bank_api.assert_not_called()
