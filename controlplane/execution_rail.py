"""
ControlPlane.AI — Execution Rail

Intercepts AI-generated tool calls and runs them through the full
ControlPlane pipeline before allowing execution.

The system NEVER allows an AI-generated external action to execute directly.

Flow:
  AI TOOL CALL → EXECUTION RAIL → CONTEXT + CONSEQUENCE → POLICY + RISK
  → ALLOW / MODIFY / BLOCK / HUMAN_APPROVAL → (mock) EXTERNAL SYSTEM
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from controlplane.context_extractor import ContextExtractor, RequestContext
from controlplane.consequence_engine import ConsequenceEngine
from controlplane.depth_planner import DepthPlanner
from controlplane.models import (
    ActionType,
    ConsequenceResult,
    ConsequenceTier,
    ControlRequest,
    DataSensitivity,
    Decision,
    Domain,
    ExecutionRailResult,
    InteractionContext,
    ToolCallRequest,
    UserContext,
)

logger = logging.getLogger(__name__)


# Mock external system registry — maps tool names to domains and sensitivity
_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "transfer_money": {
        "domain": Domain.FINANCE,
        "action_type": ActionType.EXTERNAL_ACTION,
        "reversible": False,
        "data_sensitivity": DataSensitivity.HIGH,
    },
    "send_email": {
        "domain": Domain.GENERAL,
        "action_type": ActionType.EXTERNAL_ACTION,
        "reversible": False,
        "data_sensitivity": DataSensitivity.MEDIUM,
    },
    "update_patient_record": {
        "domain": Domain.HEALTHCARE,
        "action_type": ActionType.EXTERNAL_ACTION,
        "reversible": True,
        "data_sensitivity": DataSensitivity.HIGH,
    },
    "delete_database": {
        "domain": Domain.INFRASTRUCTURE,
        "action_type": ActionType.EXTERNAL_ACTION,
        "reversible": False,
        "data_sensitivity": DataSensitivity.HIGH,
    },
    "query_database": {
        "domain": Domain.GENERAL,
        "action_type": ActionType.INFORMATIONAL,
        "reversible": True,
        "data_sensitivity": DataSensitivity.LOW,
    },
    "deploy_service": {
        "domain": Domain.INFRASTRUCTURE,
        "action_type": ActionType.EXTERNAL_ACTION,
        "reversible": True,
        "data_sensitivity": DataSensitivity.MEDIUM,
    },
    # ── Food Delivery Support Agent Tools ───────────────────────────
    "get_order_details": {
        "domain": Domain.GENERAL,
        "action_type": ActionType.INFORMATIONAL,
        "reversible": True,
        "data_sensitivity": DataSensitivity.LOW,
    },
    "track_delivery_partner": {
        "domain": Domain.GENERAL,
        "action_type": ActionType.INFORMATIONAL,
        "reversible": True,
        "data_sensitivity": DataSensitivity.MEDIUM,
    },
    "get_order_history": {
        "domain": Domain.GENERAL,
        "action_type": ActionType.INFORMATIONAL,
        "reversible": True,
        "data_sensitivity": DataSensitivity.MEDIUM,
    },
    "check_refund_status": {
        "domain": Domain.FINANCE,
        "action_type": ActionType.INFORMATIONAL,
        "reversible": True,
        "data_sensitivity": DataSensitivity.LOW,
    },
    "list_order_complaints": {
        "domain": Domain.GENERAL,
        "action_type": ActionType.INFORMATIONAL,
        "reversible": True,
        "data_sensitivity": DataSensitivity.LOW,
    },
    "update_delivery_instructions": {
        "domain": Domain.GENERAL,
        "action_type": ActionType.EXTERNAL_ACTION,
        "reversible": True,
        "data_sensitivity": DataSensitivity.LOW,
    },
    "escalate_to_human_agent": {
        "domain": Domain.GENERAL,
        "action_type": ActionType.EXTERNAL_ACTION,
        "reversible": True,
        "data_sensitivity": DataSensitivity.MEDIUM,
    },
    "request_refund_or_replacement": {
        "domain": Domain.FINANCE,
        "action_type": ActionType.EXTERNAL_ACTION,
        "reversible": False,
        "data_sensitivity": DataSensitivity.HIGH,
    },
}


def register_tool(
    name: str,
    domain: Domain = Domain.GENERAL,
    action_type: ActionType = ActionType.EXTERNAL_ACTION,
    reversible: bool = True,
    data_sensitivity: DataSensitivity = DataSensitivity.LOW,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Register or override metadata for a tool in the execution rail."""
    _TOOL_REGISTRY[name] = {
        "domain": domain,
        "action_type": action_type,
        "reversible": reversible,
        "data_sensitivity": data_sensitivity,
        "metadata": metadata or {},
    }


class ExecutionRail:
    """
    Execution rail that intercepts AI tool calls and applies ControlPlane
    governance before allowing any external action.

    All external systems are mocked in the prototype.
    """

    def __init__(self) -> None:
        self._context_extractor = ContextExtractor()
        self._consequence_engine = ConsequenceEngine()
        self._depth_planner = DepthPlanner()

    def evaluate(self, tool_call: ToolCallRequest) -> ExecutionRailResult:
        """
        Evaluate a tool call and return an execution decision.

        This is synchronous and deterministic for the MVP.
        """
        tool_name = tool_call.tool

        # Enrich context from tool registry
        tool_meta = _TOOL_REGISTRY.get(tool_name, {})
        domain = tool_meta.get("domain", tool_call.interaction_context.domain)
        reversible = tool_meta.get(
            "reversible", tool_call.interaction_context.reversible
        )
        data_sensitivity = tool_meta.get(
            "data_sensitivity",
            tool_call.interaction_context.data_sensitivity,
        )

        action_type = tool_meta.get(
            "action_type", tool_call.interaction_context.action_type
        )

        # Build context
        interaction = InteractionContext(
            domain=domain,
            action_type=action_type,
            reversible=reversible,
            data_sensitivity=data_sensitivity,
        )

        request_metadata = dict(tool_call.metadata)
        request_metadata["tool_name"] = tool_name
        request_metadata["parameters"] = tool_call.parameters

        request = ControlRequest(
            request=f"Tool call: {tool_name}({tool_call.parameters})",
            user_context=tool_call.user_context,
            interaction_context=interaction,
            metadata=request_metadata,
        )

        ctx = self._context_extractor.extract(request)
        consequence = self._consequence_engine.evaluate(ctx)
        depth = self._depth_planner.plan(consequence.tier)

        # Decision logic for the execution rail
        decision, reason = self._decide(
            ctx, consequence.tier, tool_call, consequence.reason
        )

        allowed = decision == Decision.PASS

        logger.info(
            "Execution rail: tool=%s consequence=%s decision=%s",
            tool_name,
            consequence.tier.value,
            decision.value,
        )

        return ExecutionRailResult(
            allowed=allowed,
            decision=decision,
            reason=reason,
            tool=tool_name,
            consequence_tier=consequence.tier,
            requires_human=(decision == Decision.HUMAN_APPROVAL),
            request_id=tool_call.request_id,
        )

    def _decide(
        self,
        ctx: RequestContext,
        tier: ConsequenceTier,
        tool_call: ToolCallRequest,
        consequence_reason: Optional[str] = None,
    ) -> tuple[Decision, str]:
        """Determine the execution rail decision."""
        # Informational / read-only queries are safe to execute directly
        if ctx.is_informational:
            return (
                Decision.PASS,
                f"Read-only {ctx.domain.value.lower()} informational query approved for execution",
            )

        # HIGH consequence: require human approval for irreversible actions
        if tier == ConsequenceTier.HIGH:
            if not ctx.reversible:
                return (
                    Decision.HUMAN_APPROVAL,
                    f"High-consequence irreversible {ctx.domain.value.lower()} "
                    f"action requires human approval",
                )
            return (
                Decision.HUMAN_APPROVAL,
                "High-consequence external action requires human approval",
            )

        # MEDIUM consequence:
        if tier == ConsequenceTier.MEDIUM:
            # Reversible support actions (e.g. updating delivery instructions or standard escalation)
            if ctx.reversible:
                return (
                    Decision.PASS,
                    f"Reversible {ctx.domain.value.lower()} action approved for execution",
                )
            return (
                Decision.VERIFY,
                "Medium-consequence action; verification recommended "
                "before execution",
            )

        # LOW consequence: allow
        if consequence_reason:
            return (Decision.PASS, consequence_reason)
        return (
            Decision.PASS,
            "Low-consequence action approved for execution",
        )


class MockExternalSystem:
    """
    Mock external system for prototype demonstration.
    NEVER connects to real systems.
    """

    @staticmethod
    def execute(
        tool_name: str,
        parameters: Dict[str, Any],
        rail_result: ExecutionRailResult,
    ) -> Dict[str, Any]:
        """
        Simulate external system execution.

        Only executes if the rail allowed it.
        """
        if not rail_result.allowed:
            return {
                "executed": False,
                "tool": tool_name,
                "reason": rail_result.reason,
                "decision": rail_result.decision.value,
            }

        # Mock successful execution
        return {
            "executed": True,
            "tool": tool_name,
            "result": f"[MOCK] {tool_name} executed successfully",
            "parameters": parameters,
        }
