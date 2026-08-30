"""
Tests for ControlPlane Audit Fidelity.

Validates that AuditLogger accurately captures:
- ConsequenceTier (LOW, MEDIUM, HIGH)
- EvaluationDepth (FAST, DEEP, HIGH_ASSURANCE)
- Decision (PASS, HUMAN_APPROVAL, BLOCK)
- Authoritative ExecutionRailResult fidelity independent of business tool return data.
"""

import pytest

from controlplane.models import (
    ConsequenceTier,
    Decision,
    EvaluationDepth,
)
from support_agent_mcp.proxy.controlplane_hooks import (
    ControlPlaneAuditLoggerHook,
    build_default_pipeline,
)
from support_agent_mcp.server import (
    escalate_to_human_agent,
    get_order_details,
    request_refund_or_replacement,
)


@pytest.fixture
def clean_pipeline():
    """Builds a test pipeline with audit logger."""
    return build_default_pipeline(verbose_logging=False, refund_limit=200.0)


class TestAuditFidelity:
    """Verify exact telemetry and audit record fidelity across all consequence tiers."""

    def test_low_consequence_audit_fidelity(self, clean_pipeline):
        """LOW consequence tool (get_order_details) logs LOW, FAST, PASS."""
        result = clean_pipeline.call(get_order_details, {"order_id": "ORD001"})
        assert result["success"] is True

        audit_hook = next(h for h in clean_pipeline.hooks if isinstance(h, ControlPlaneAuditLoggerHook))
        assert len(audit_hook.audit_logger.entries) > 0

        latest = audit_hook.audit_logger.entries[-1]
        assert latest.consequence_tier == ConsequenceTier.LOW
        assert latest.evaluation_depth == EvaluationDepth.FAST
        assert latest.final_decision == Decision.PASS
        assert "ORD001" in str(latest.metadata.get("args"))

    def test_medium_consequence_audit_fidelity(self, clean_pipeline):
        """MEDIUM consequence tool (escalate_to_human_agent) logs MEDIUM, DEEP, PASS."""
        result = clean_pipeline.call(
            escalate_to_human_agent,
            {
                "order_id": "ORD001",
                "customer_id": "CUST001",
                "reason": "Customer inquiry regarding delivery status",
                "urgency": "medium",
            },
        )
        assert result["success"] is True

        audit_hook = next(h for h in clean_pipeline.hooks if isinstance(h, ControlPlaneAuditLoggerHook))
        assert len(audit_hook.audit_logger.entries) > 0

        latest = audit_hook.audit_logger.entries[-1]
        assert latest.consequence_tier == ConsequenceTier.MEDIUM
        assert latest.evaluation_depth == EvaluationDepth.DEEP
        assert latest.final_decision == Decision.PASS

    def test_high_consequence_human_approval_audit_fidelity(self, clean_pipeline):
        """HIGH consequence tool (refund > ₹200) logs HIGH, HIGH_ASSURANCE, HUMAN_APPROVAL."""
        result = clean_pipeline.call(
            request_refund_or_replacement,
            {
                "order_id": "ORD004",
                "customer_id": "CUST004",
                "reason": "Complete order ruined",
                "complaint_type": "late_delivery",
                "requested_amount": 587.0,
            },
        )
        assert result["status"] == "pending_human_review"
        assert result["decision"] == "HUMAN_APPROVAL"

        audit_hook = next(h for h in clean_pipeline.hooks if isinstance(h, ControlPlaneAuditLoggerHook))
        assert len(audit_hook.audit_logger.entries) > 0

        latest = audit_hook.audit_logger.entries[-1]
        assert latest.consequence_tier == ConsequenceTier.HIGH
        assert latest.evaluation_depth == EvaluationDepth.HIGH_ASSURANCE
        assert latest.final_decision == Decision.HUMAN_APPROVAL

    def test_small_refund_within_limit_audit_fidelity(self, clean_pipeline):
        """Refund <= ₹200 is evaluated as LOW tier, FAST depth, and PASS decision."""
        result = clean_pipeline.call(
            request_refund_or_replacement,
            {
                "order_id": "ORD002",
                "customer_id": "CUST002",
                "reason": "Missing drink",
                "complaint_type": "missing_items",
                "requested_amount": 179.0,
            },
        )
        assert result["success"] is True
        assert result["status"] == "approved"

        audit_hook = next(h for h in clean_pipeline.hooks if isinstance(h, ControlPlaneAuditLoggerHook))
        assert len(audit_hook.audit_logger.entries) > 0

        latest = audit_hook.audit_logger.entries[-1]
        assert latest.consequence_tier == ConsequenceTier.LOW
        assert latest.evaluation_depth == EvaluationDepth.FAST
        assert latest.final_decision == Decision.PASS

    def test_audit_does_not_infer_from_business_tool_return_data(self, clean_pipeline):
        """
        Verify audit record is not fooled if a mock business tool returns arbitrary metadata.
        The audit log must use the authoritative ExecutionRailResult.
        """
        def mock_tool(order_id: str):
            return {
                "success": True,
                "consequence_tier": "HIGH",        # Attempted spoof in tool return
                "evaluation_depth": "HIGH_ASSURANCE",
                "decision": "BLOCK",
            }
        mock_tool.__name__ = "get_order_details"  # get_order_details is registered as LOW consequence

        clean_pipeline.call(mock_tool, {"order_id": "ORD001"})

        audit_hook = next(h for h in clean_pipeline.hooks if isinstance(h, ControlPlaneAuditLoggerHook))
        latest = audit_hook.audit_logger.entries[-1]

        # Must record actual LOW/FAST/PASS governance evaluation, NOT the tool return spoof
        assert latest.consequence_tier == ConsequenceTier.LOW
        assert latest.evaluation_depth == EvaluationDepth.FAST
        assert latest.final_decision == Decision.PASS
