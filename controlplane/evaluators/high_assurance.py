"""
ControlPlane.AI — High-Assurance Evaluator (HIGH_ASSURANCE Path)

For HIGH consequence interactions. Runs all FAST + DEEP checks plus
stricter policy enforcement and execution controls.

Does NOT automatically require human approval for every HIGH interaction.
Instead: HIGH → HIGH_ASSURANCE controls → if risk unresolved → HUMAN_APPROVAL.
"""

from __future__ import annotations

import logging

from controlplane.context_extractor import RequestContext
from controlplane.evaluators.base import EvalResult, Evaluator
from controlplane.evaluators.deep_evaluator import DeepEvaluator
from controlplane.models import CheckResult, CheckStatus, EvaluationDepth

logger = logging.getLogger(__name__)


class HighAssuranceEvaluator(Evaluator):
    """
    HIGH_ASSURANCE evaluator for HIGH consequence requests.

    Includes all DEEP checks plus:
    - Stricter policy enforcement
    - Execution control checks for external actions
    - Dual-factor responsibility verification
    """

    def __init__(self) -> None:
        self._deep = DeepEvaluator()

    async def evaluate(
        self, ctx: RequestContext, request_text: str
    ) -> EvalResult:
        # Run full DEEP evaluation first
        deep_result = await self._deep.evaluate(ctx, request_text)
        checks = list(deep_result.checks)

        # Additional high-assurance checks
        checks.append(self._check_execution_controls(ctx))
        checks.append(self._check_strict_policy(ctx, request_text))
        checks.append(self._check_dual_responsibility(ctx))

        # Determine overall status
        if any(c.status == CheckStatus.FAIL for c in checks):
            overall = CheckStatus.FAIL
        elif any(c.status == CheckStatus.UNCERTAIN for c in checks):
            overall = CheckStatus.UNCERTAIN
        else:
            overall = CheckStatus.PASS

        return EvalResult(
            depth=EvaluationDepth.HIGH_ASSURANCE,
            overall_status=overall,
            checks=checks,
        )

    def _check_execution_controls(self, ctx: RequestContext) -> CheckResult:
        """
        Verify execution controls are in place for external actions.

        External actions at HIGH consequence must go through the
        execution rail — this check validates the context is suitable.
        """
        if ctx.is_external_action:
            if not ctx.reversible:
                return CheckResult(
                    name="execution_controls",
                    status=CheckStatus.UNCERTAIN,
                    category="EXECUTION",
                    reason=(
                        "Irreversible external action at HIGH consequence; "
                        "execution rail and approval controls required"
                    ),
                )
            return CheckResult(
                name="execution_controls",
                status=CheckStatus.UNCERTAIN,
                category="EXECUTION",
                reason=(
                    "External action at HIGH consequence; "
                    "execution rail controls required"
                ),
            )

        return CheckResult(
            name="execution_controls",
            status=CheckStatus.PASS,
            category="EXECUTION",
            reason="No external action; execution controls not required",
        )

    def _check_strict_policy(
        self, ctx: RequestContext, text: str
    ) -> CheckResult:
        """
        Stricter policy checks for HIGH consequence interactions.

        Applies enhanced scrutiny compared to the DEEP path.
        """
        # Irreversible + sensitive domain = always flag for review
        if not ctx.reversible and ctx.is_sensitive_domain:
            return CheckResult(
                name="strict_policy",
                status=CheckStatus.UNCERTAIN,
                category="POLICY",
                reason=(
                    f"Irreversible action in {ctx.domain.value.lower()} "
                    f"domain requires enhanced policy review"
                ),
            )

        # High data sensitivity + external action
        if (
            ctx.data_sensitivity.value == "HIGH"
            and ctx.is_external_action
        ):
            return CheckResult(
                name="strict_policy",
                status=CheckStatus.UNCERTAIN,
                category="POLICY",
                reason=(
                    "High data sensitivity external action "
                    "requires strict policy enforcement"
                ),
            )

        return CheckResult(
            name="strict_policy",
            status=CheckStatus.PASS,
            category="POLICY",
            reason="Strict policy check passed",
        )

    def _check_dual_responsibility(self, ctx: RequestContext) -> CheckResult:
        """
        Dual-factor responsibility check for HIGH consequence.

        Verifies that the requesting context has sufficient attributes
        for high-consequence operations.
        """
        issues: list[str] = []

        if ctx.user_role == "unknown":
            issues.append("unknown user role")
        if ctx.user_id is None:
            issues.append("no user ID")

        if issues:
            return CheckResult(
                name="dual_responsibility",
                status=CheckStatus.UNCERTAIN,
                category="RESPONSIBILITY",
                reason=(
                    f"Insufficient identity attributes for HIGH consequence: "
                    f"{', '.join(issues)}"
                ),
            )

        return CheckResult(
            name="dual_responsibility",
            status=CheckStatus.PASS,
            category="RESPONSIBILITY",
            reason="Identity attributes sufficient for high-consequence action",
        )
