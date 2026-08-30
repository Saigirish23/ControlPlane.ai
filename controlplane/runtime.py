"""
ControlPlane.AI — Unified Runtime

Single entry point that orchestrates the COMPLETE ControlPlane pipeline:

    REQUEST
     ↓
    CONTEXT EXTRACTION
     ↓
    CONSEQUENCE ENGINE
     ↓
    DEPTH PLANNER
     ↓
    PRE-INFERENCE GATE  ──→  BLOCK / HUMAN_APPROVAL → stop
     ↓
    AI MODEL (stream)
     ↓
    STREAM GUARDRAIL
     ↓
    POST-GENERATION EVALUATION
     ↓
    ACTION ROUTER
     ↓
    FINAL DECISION

For tool calls:

    TOOL CALL → EXECUTION RAIL → DECISION → MOCK EXTERNAL SYSTEM

The application should not need to know the internals.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from controlplane.action_router import ActionRouter
from controlplane.audit import AuditLogger
from controlplane.consequence_engine import ConsequenceEngine
from controlplane.context_extractor import ContextExtractor, RequestContext
from controlplane.cost import CostEvaluator
from controlplane.depth_planner import DepthPlanner
from controlplane.evaluators.base import EvalResult
from controlplane.evaluators.deep_evaluator import DeepEvaluator
from controlplane.evaluators.fast_evaluator import FastEvaluator
from controlplane.evaluators.high_assurance import HighAssuranceEvaluator
from controlplane.execution_rail import ExecutionRail, MockExternalSystem
from controlplane.models import (
    ActionType,
    CheckResult,
    CheckStatus,
    ConsequenceResult,
    ConsequenceTier,
    ControlRequest,
    ControlResponse,
    CostResult,
    DataSensitivity,
    Decision,
    DecisionResult,
    Domain,
    EvaluationDepth,
    EvaluationResult,
    ExecutionRailResult,
    InteractionContext,
    PerformanceResult,
    ResponsibilityResult,
    ToolCallRequest,
    UserContext,
)
from controlplane.performance import PerformanceEvaluator
from controlplane.responsibility import ResponsibilityEvaluator
from controlplane.stream_guardrail import StreamGuardrailManager, StreamResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────


@dataclass
class PreInferenceResult:
    """Result from the pre-inference decision gate."""

    allowed: bool
    consequence: ConsequenceResult
    depth: EvaluationDepth
    eval_result: EvalResult
    decision: Optional[DecisionResult] = None  # set if blocked
    ctx: Optional[RequestContext] = None


@dataclass
class RuntimeResult:
    """Complete result from the unified runtime pipeline."""

    request_id: str
    consequence: ConsequenceResult
    depth: EvaluationDepth
    model_executed: bool
    stream_result: Optional[StreamResult]
    response_text: str
    performance: PerformanceResult
    cost: CostResult
    responsibility: ResponsibilityResult
    decision: DecisionResult
    audit_entry: Optional[Any] = None

    @property
    def blocked_pre_inference(self) -> bool:
        return not self.model_executed and self.stream_result is None


@dataclass
class ToolCallResult:
    """Result from an execution-rail governed tool call."""

    request_id: str
    tool: str
    consequence_tier: ConsequenceTier
    rail_result: ExecutionRailResult
    execution_result: Dict[str, Any]
    external_executed: bool


# ─────────────────────────────────────────────
# Mock model streams
# ─────────────────────────────────────────────


async def mock_model_stream(
    prompt: str,
    include_pii: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Mock model stream for testing without external APIs.
    Yields word-by-word tokens like a real LLM stream.
    """
    import asyncio

    if include_pii:
        text = (
            f"Responding to: '{prompt}'. "
            "Based on our analysis, the customer John Doe with SSN 123-45-6789 "
            "and credit card 4111-1111-1111-1111 is eligible for the refund. "
            "The password for the admin account is 'secret123'. "
            "Please process accordingly."
        )
    else:
        text = (
            f"Responding to: '{prompt}'. "
            "Thank you for your request. After careful analysis, "
            "I have rewritten the marketing copy to be more professional "
            "and engaging. The revised version emphasizes key value "
            "propositions while maintaining a warm, approachable tone "
            "suitable for enterprise communications."
        )

    for word in text.split(" "):
        yield word + " "
        await asyncio.sleep(0.005)


async def mock_pii_stream(prompt: str = "") -> AsyncGenerator[str, None]:
    """Stream that deliberately contains PII for violation testing."""
    async for token in mock_model_stream(prompt, include_pii=True):
        yield token


# ─────────────────────────────────────────────
# Unified Runtime
# ─────────────────────────────────────────────


# Type alias for model stream factories
ModelStreamFactory = Callable[[str], AsyncGenerator[str, None]]


class UnifiedControlPlane:
    """
    Unified runtime that integrates:
    - Pre-inference governance (consequence → depth → gate)
    - Model streaming with guardrail inspection
    - Post-generation evaluation
    - Action routing
    - Execution rail for tool calls
    - Audit logging

    Usage:
        cp = UnifiedControlPlane()

        # Text request with streaming
        result = await cp.run(request, model_stream=mock_model_stream)

        # Tool call
        result = await cp.run_tool_call(tool_call)
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
        return self._audit

    # ── Pre-Inference Gate ──────────────────────────────────

    async def _pre_inference_gate(
        self, request: ControlRequest
    ) -> PreInferenceResult:
        """
        Run pre-inference checks and decide whether model execution
        is permitted.

        Returns PreInferenceResult with allowed=False if the request
        should be blocked before reaching the model.
        """
        ctx = self._context_extractor.extract(request)
        consequence = self._consequence_engine.evaluate(ctx)
        depth = self._depth_planner.plan(consequence.tier)

        evaluator = self._evaluators[depth]
        eval_result = await evaluator.evaluate(ctx, request.request)

        # Pre-inference responsibility check on the request text
        responsibility = self._responsibility.evaluate(request.request)

        # Check for hard blocks (injection, safety violations in request)
        has_injection = any(
            c.status == CheckStatus.FAIL and c.category in {"INJECTION", "SAFETY"}
            for c in responsibility.checks
        )
        if has_injection:
            reason_parts = [
                c.reason
                for c in responsibility.checks
                if c.status == CheckStatus.FAIL
                and c.category in {"INJECTION", "SAFETY"}
            ]
            return PreInferenceResult(
                allowed=False,
                consequence=consequence,
                depth=depth,
                eval_result=eval_result,
                ctx=ctx,
                decision=DecisionResult(
                    action=Decision.BLOCK,
                    reason=f"Pre-inference block: {'; '.join(reason_parts)}",
                    requires_human=False,
                ),
            )

        # HIGH consequence with unresolved risk → block pre-inference
        if consequence.tier == ConsequenceTier.HIGH:
            if eval_result.has_failures:
                return PreInferenceResult(
                    allowed=False,
                    consequence=consequence,
                    depth=depth,
                    eval_result=eval_result,
                    ctx=ctx,
                    decision=DecisionResult(
                        action=Decision.BLOCK,
                        reason="HIGH consequence with evaluation failures; model execution prevented",
                        requires_human=True,
                    ),
                )
            if eval_result.has_uncertainty and ctx.is_external_action:
                return PreInferenceResult(
                    allowed=False,
                    consequence=consequence,
                    depth=depth,
                    eval_result=eval_result,
                    ctx=ctx,
                    decision=DecisionResult(
                        action=Decision.HUMAN_APPROVAL,
                        reason="HIGH consequence external action with uncertainty; human approval required before model execution",
                        requires_human=True,
                    ),
                )

        # Model execution is permitted
        return PreInferenceResult(
            allowed=True,
            consequence=consequence,
            depth=depth,
            eval_result=eval_result,
            ctx=ctx,
        )

    # ── Full Pipeline Run ───────────────────────────────────

    async def run(
        self,
        request: ControlRequest,
        model_stream: Optional[ModelStreamFactory] = None,
    ) -> RuntimeResult:
        """
        Execute the full unified pipeline:

        1. Pre-inference gate
        2. Model streaming + guardrail (if allowed)
        3. Post-generation evaluation
        4. Action routing
        5. Audit

        Args:
            request: The ControlRequest to evaluate
            model_stream: Async generator factory that takes a prompt
                          and yields token strings. If None, uses
                          mock_model_stream.
        """
        request_id = str(uuid.uuid4())
        start_time = time.time()

        # ── 1. Pre-Inference Gate ───────────────────────────
        gate = await self._pre_inference_gate(request)

        if not gate.allowed:
            # Request blocked before model execution
            logger.info(
                "Pre-inference BLOCKED: tier=%s depth=%s decision=%s",
                gate.consequence.tier.value,
                gate.depth.value,
                gate.decision.action.value,
            )

            elapsed_ms = (time.time() - start_time) * 1000
            cost = self._cost_evaluator.evaluate(latency_ms=elapsed_ms)

            return RuntimeResult(
                request_id=request_id,
                consequence=gate.consequence,
                depth=gate.depth,
                model_executed=False,
                stream_result=None,
                response_text="",
                performance=PerformanceResult(
                    status=CheckStatus.PASS,
                    reason="Model not executed; no response to evaluate",
                ),
                cost=cost,
                responsibility=self._responsibility.evaluate(request.request),
                decision=gate.decision,
            )

        # ── 2. Model Streaming + Guardrail ──────────────────
        stream_factory = model_stream or mock_model_stream
        stream_manager = StreamGuardrailManager(
            target_chunk_size=30,
            max_concurrent_evaluations=4,
        )

        stream_error: Optional[str] = None
        try:
            token_gen = stream_factory(request.request)
            stream_result = await stream_manager.process_stream(token_gen)
            response_text = stream_result.safe_text
        except Exception as e:
            logger.error("Stream processing failed: %s", e)
            stream_error = str(e)
            # Fail-safe: if stream fails at HIGH consequence, block
            if gate.consequence.tier == ConsequenceTier.HIGH:
                return RuntimeResult(
                    request_id=request_id,
                    consequence=gate.consequence,
                    depth=gate.depth,
                    model_executed=False,
                    stream_result=None,
                    response_text="",
                    performance=PerformanceResult(
                        status=CheckStatus.FAIL,
                        reason=f"Stream processing failed: {e}",
                    ),
                    cost=self._cost_evaluator.evaluate(
                        latency_ms=(time.time() - start_time) * 1000
                    ),
                    responsibility=ResponsibilityResult(
                        status=CheckStatus.FAIL,
                        checks=[
                            CheckResult(
                                name="stream_health",
                                status=CheckStatus.FAIL,
                                category="INFRASTRUCTURE",
                                reason=f"Stream processing failed at HIGH consequence: {e}",
                            )
                        ],
                    ),
                    decision=DecisionResult(
                        action=Decision.BLOCK,
                        reason=f"Stream processing failed at HIGH consequence: {e}",
                        requires_human=True,
                    ),
                )
            stream_result = None
            response_text = ""

        # ── 3. Post-Generation Evaluation ───────────────────
        # Performance evaluation on the response
        performance = self._performance.evaluate(
            request_text=request.request,
            response_text=response_text if response_text else None,
        )
        if stream_error:
            performance.status = CheckStatus.UNCERTAIN
            performance.reason = f"Stream failed mid-generation: {stream_error}"
            performance.evidence.append(performance.reason)

        # Cost telemetry
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

        # Responsibility check on the RESPONSE text (output-side)
        if response_text:
            responsibility = self._responsibility.evaluate(response_text)
        else:
            responsibility = ResponsibilityResult()

        if stream_error:
            responsibility.status = CheckStatus.UNCERTAIN
            responsibility.checks.append(
                CheckResult(
                    name="stream_health",
                    status=CheckStatus.UNCERTAIN,
                    category="INFRASTRUCTURE",
                    reason=f"Stream failed mid-generation: {stream_error}",
                )
            )

        # If stream guardrail flagged violations, reflect in responsibility
        if stream_result and stream_result.has_violations:
            for v in stream_result.violations:
                responsibility.checks.append(
                    CheckResult(
                        name=f"stream_guardrail_{v['type'].lower()}",
                        status=CheckStatus.FAIL,
                        category=v["type"],
                        reason=f"Stream guardrail detected {v['type']} in chunk {v['chunk_id']}: {v['text_preview'][:50]}",
                    )
                )
            if responsibility.status == CheckStatus.PASS:
                responsibility.status = CheckStatus.FAIL

        # ── 4. Action Router ────────────────────────────────
        decision = self._action_router.route(
            ctx=gate.ctx,
            consequence_tier=gate.consequence.tier,
            eval_result=gate.eval_result,
            responsibility=responsibility,
            performance=performance,
            cost=cost,
        )

        # ── 5. Build result ─────────────────────────────────
        result = RuntimeResult(
            request_id=request_id,
            consequence=gate.consequence,
            depth=gate.depth,
            model_executed=True,
            stream_result=stream_result,
            response_text=response_text,
            performance=performance,
            cost=cost,
            responsibility=responsibility,
            decision=decision,
        )

        logger.info(
            "Runtime complete: tier=%s depth=%s model=%s decision=%s (%.1fms)",
            gate.consequence.tier.value,
            gate.depth.value,
            "EXECUTED" if result.model_executed else "BLOCKED",
            decision.action.value,
            elapsed_ms,
        )

        return result

    # ── Tool Call Execution ─────────────────────────────────

    async def run_tool_call(
        self, tool_call: ToolCallRequest
    ) -> ToolCallResult:
        """
        Run a tool call through the execution rail.

        HIGH consequence tool calls are blocked/held for human approval.
        The mock external system is NEVER called if the rail denies it.
        """
        request_id = str(uuid.uuid4())

        # Get consequence tier for audit (using tool registry metadata)
        from controlplane.execution_rail import _TOOL_REGISTRY

        tool_meta = _TOOL_REGISTRY.get(tool_call.tool, {})
        domain = tool_meta.get("domain", tool_call.interaction_context.domain)
        reversible = tool_meta.get(
            "reversible", tool_call.interaction_context.reversible
        )
        data_sensitivity = tool_meta.get(
            "data_sensitivity",
            tool_call.interaction_context.data_sensitivity,
        )

        synthetic_request = ControlRequest(
            request=f"Tool call: {tool_call.tool}({tool_call.parameters})",
            user_context=tool_call.user_context,
            interaction_context=InteractionContext(
                domain=domain,
                action_type=ActionType.EXTERNAL_ACTION,
                reversible=reversible,
                data_sensitivity=data_sensitivity,
            ),
        )
        ctx = self._context_extractor.extract(synthetic_request)
        consequence = self._consequence_engine.evaluate(ctx)

        # Run execution rail
        rail_result = self._execution_rail.evaluate(tool_call)

        # Attempt mock execution (will be blocked if rail denied)
        execution_result = MockExternalSystem.execute(
            tool_name=tool_call.tool,
            parameters=tool_call.parameters,
            rail_result=rail_result,
        )

        logger.info(
            "Tool call: tool=%s tier=%s rail=%s executed=%s",
            tool_call.tool,
            consequence.tier.value,
            rail_result.decision.value,
            execution_result.get("executed", False),
        )

        return ToolCallResult(
            request_id=request_id,
            tool=tool_call.tool,
            consequence_tier=consequence.tier,
            rail_result=rail_result,
            execution_result=execution_result,
            external_executed=execution_result.get("executed", False),
        )

    # ── Full Agent Lifecycle Flow ───────────────────────────

    async def run_agent_flow(
        self,
        request: ControlRequest,
        model_generate_tool_call: Callable[[str], ToolCallRequest],
    ) -> Dict[str, Any]:
        """
        Execute the full agent sequence:
        1. Inbound user request & initial consequence check
        2. AI Model generates a tool call
        3. Execution Rail intercepts the tool call
        4. ControlPlane renders governance decision
        5. Mock External System execution is attempted (strictly blocked if unapproved)

        Returns complete execution trace and decision records.
        """
        request_id = str(uuid.uuid4())
        ctx = self._context_extractor.extract(request)
        initial_consequence = self._consequence_engine.evaluate(ctx)

        # 2. AI Model generates tool call
        tool_call = model_generate_tool_call(request.request)

        # 3. Execution rail governance
        tool_result = await self.run_tool_call(tool_call)

        return {
            "request_id": request_id,
            "user_request": request.request,
            "initial_consequence_tier": initial_consequence.tier.value,
            "tool_generated": tool_call.tool,
            "tool_parameters": tool_call.parameters,
            "rail_decision": tool_result.rail_result.decision.value,
            "requires_human": tool_result.rail_result.decision == Decision.HUMAN_APPROVAL,
            "external_executed": tool_result.external_executed,
            "execution_result": tool_result.execution_result,
            "reason": tool_result.rail_result.reason,
        }
