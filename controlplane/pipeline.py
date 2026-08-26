"""
ControlPlane.AI — Pipeline Orchestrator

Single entry point that orchestrates the full ControlPlane decision pipeline:

  ContextExtractor → ConsequenceEngine → DepthPlanner → Evaluator
  → (Performance + Cost + Responsibility) → ActionRouter → Decision

Works independently of the API layer.
"""

from __future__ import annotations

import logging
import time

from controlplane.action_router import ActionRouter
from controlplane.audit import AuditLogger
from controlplane.consequence_engine import ConsequenceEngine
from controlplane.context_extractor import ContextExtractor
from controlplane.cost import CostEvaluator
from controlplane.depth_planner import DepthPlanner
from controlplane.evaluators.deep_evaluator import DeepEvaluator
from controlplane.evaluators.fast_evaluator import FastEvaluator
from controlplane.evaluators.high_assurance import HighAssuranceEvaluator
from controlplane.execution_rail import ExecutionRail
from controlplane.models import (
    ControlRequest,
    ControlResponse,
    EvaluationDepth,
    EvaluationResult,
    ExecutionRailResult,
    ToolCallRequest,
)
from controlplane.performance import PerformanceEvaluator
from controlplane.responsibility import ResponsibilityEvaluator

logger = logging.getLogger(__name__)


class ControlPlanePipeline:
    """
    Full ControlPlane decision pipeline.

    Orchestrates all components in the correct order and produces a
    structured, explainable ControlResponse.
    """

    def __init__(self) -> None:
        self._context_extractor = ContextExtractor()
        self._consequence_engine = ConsequenceEngine()
        self._depth_planner = DepthPlanner()
        self._action_router = ActionRouter()
        self._performance = PerformanceEvaluator()
        self._cost_evaluator = CostEvaluator()
        self._responsibility = ResponsibilityEvaluator()
        self._audit = AuditLogger()
        self._execution_rail = ExecutionRail()

        # Evaluators by depth
        self._evaluators = {
            EvaluationDepth.FAST: FastEvaluator(),
            EvaluationDepth.DEEP: DeepEvaluator(),
            EvaluationDepth.HIGH_ASSURANCE: HighAssuranceEvaluator(),
        }

    @property
    def audit(self) -> AuditLogger:
        """Access the audit logger for inspection."""
        return self._audit

    async def evaluate(self, request: ControlRequest) -> ControlResponse:
        """
        Run the full ControlPlane pipeline for a request.

        Returns a ControlResponse with consequence classification,
        evaluation results, and a final decision.
        """
        start_time = time.time()

        # 1. Extract context
        ctx = self._context_extractor.extract(request)

        # 2. Classify consequence
        consequence = self._consequence_engine.evaluate(ctx)

        # 3. Plan evaluation depth
        depth = self._depth_planner.plan(consequence.tier)

        # 4. Run the appropriate evaluator
        evaluator = self._evaluators[depth]
        eval_result = await evaluator.evaluate(ctx, request.request)

        evaluation = EvaluationResult(
            depth=depth,
            checks=eval_result.checks,
            overall_status=eval_result.overall_status,
        )

        # 5. Responsibility check on the request text
        responsibility = self._responsibility.evaluate(request.request)

        # 6. Performance evaluation (pre-response)
        performance = self._performance.evaluate(
            request_text=request.request
        )

        # 7. Cost telemetry
        elapsed_ms = (time.time() - start_time) * 1000
        cost = self._cost_evaluator.evaluate(
            latency_ms=elapsed_ms,
            model=request.metadata.get("model", ""),
            input_tokens=request.metadata.get("input_tokens", 0),
            output_tokens=request.metadata.get("output_tokens", 0),
            model_calls=request.metadata.get("model_calls", 1),
            tool_calls=request.metadata.get("tool_calls", 0),
            retries=request.metadata.get("retries", 0),
        )

        # 8. Route to decision
        decision = self._action_router.route(
            ctx=ctx,
            consequence_tier=consequence.tier,
            eval_result=eval_result,
            responsibility=responsibility,
            performance=performance,
            cost=cost,
        )

        # 9. Build response
        response = ControlResponse(
            consequence=consequence,
            evaluation=evaluation,
            performance=performance,
            cost=cost,
            responsibility=responsibility,
            decision=decision,
        )

        # 10. Audit log
        self._audit.log_decision(response)

        logger.info(
            "Pipeline complete: tier=%s depth=%s decision=%s (%.1fms)",
            consequence.tier.value,
            depth.value,
            decision.action.value,
            elapsed_ms,
        )

        return response

    async def evaluate_tool_call(
        self, tool_call: ToolCallRequest
    ) -> ExecutionRailResult:
        """
        Evaluate an AI-generated tool call through the execution rail.

        Returns an ExecutionRailResult indicating whether the tool call
        is allowed to proceed.
        """
        result = self._execution_rail.evaluate(tool_call)
        logger.info(
            "Execution rail: tool=%s decision=%s",
            tool_call.tool,
            result.decision.value,
        )
        return result
