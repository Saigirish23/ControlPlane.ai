"""
ControlPlane.AI — Action Router

Receives consequence tier, evaluation results, and policy context.
Returns a structured Decision: PASS, MODIFY, VERIFY, BLOCK, or HUMAN_APPROVAL.

The decision is consequence-aware and policy-driven — NOT threshold-based.
Every decision includes a human-readable reason.
"""

from __future__ import annotations

import logging
from typing import Optional

from controlplane.context_extractor import RequestContext
from controlplane.evaluators.base import EvalResult
from controlplane.models import (
    CheckStatus,
    ConsequenceTier,
    CostResult,
    Decision,
    DecisionResult,
    PerformanceResult,
    ResponsibilityResult,
)

logger = logging.getLogger(__name__)


class ActionRouter:
    """
    Routes ControlPlane evaluations to a final decision.

    Decision logic considers consequence tier, evaluation results,
    and policy requirements — not simple numeric thresholds.
    """

    def route(
        self,
        ctx: RequestContext,
        consequence_tier: ConsequenceTier,
        eval_result: EvalResult,
        responsibility: ResponsibilityResult,
        performance: PerformanceResult,
        cost: CostResult,
    ) -> DecisionResult:
        """
        Determine the final action based on all evaluation signals.

        Priority order:
        1. Hard failures (safety, injection) → BLOCK
        2. PII in output → MODIFY or BLOCK
        3. HIGH consequence + unresolved risk → HUMAN_APPROVAL
        4. MEDIUM + uncertainty → VERIFY
        5. Cost anomaly → VERIFY
        6. All clear → PASS
        """

        reasons: list[str] = []

        # ── 1. BLOCK on hard safety/injection failures ──────────
        safety_failures = [
            c for c in responsibility.checks
            if c.status == CheckStatus.FAIL
            and c.category in {"INJECTION", "SAFETY"}
        ]
        if safety_failures:
            reason = "; ".join(c.reason for c in safety_failures)
            logger.info("BLOCK: %s", reason)
            return DecisionResult(
                action=Decision.BLOCK,
                reason=f"Blocked due to safety/security concern: {reason}",
                requires_human=False,
            )

        # ── 2. PII detected → MODIFY or BLOCK depending on tier ─
        pii_failures = [
            c for c in responsibility.checks
            if c.status == CheckStatus.FAIL and c.category == "PII"
        ]
        if pii_failures:
            pii_reason = "; ".join(c.reason for c in pii_failures)
            if consequence_tier == ConsequenceTier.HIGH:
                logger.info("BLOCK (PII + HIGH): %s", pii_reason)
                return DecisionResult(
                    action=Decision.BLOCK,
                    reason=(
                        f"PII detected in HIGH consequence interaction: "
                        f"{pii_reason}"
                    ),
                    requires_human=True,
                )
            else:
                logger.info("MODIFY (PII): %s", pii_reason)
                return DecisionResult(
                    action=Decision.MODIFY,
                    reason=(
                        f"Response requires PII redaction: {pii_reason}"
                    ),
                    requires_human=False,
                )

        # ── 3. HIGH consequence with unresolved risk → HUMAN ────
        if consequence_tier == ConsequenceTier.HIGH:
            if eval_result.has_failures:
                reason = "HIGH consequence with evaluation failures"
                logger.info("BLOCK (HIGH + failures): %s", reason)
                return DecisionResult(
                    action=Decision.BLOCK,
                    reason=reason,
                    requires_human=True,
                )

            if eval_result.has_uncertainty:
                reason = (
                    "HIGH consequence with unresolved uncertainty; "
                    "human approval required"
                )
                logger.info("HUMAN_APPROVAL: %s", reason)
                return DecisionResult(
                    action=Decision.HUMAN_APPROVAL,
                    reason=reason,
                    requires_human=True,
                )

            # HIGH + external action, even if all pass → HUMAN_APPROVAL
            # because irreversible external actions need human oversight
            if ctx.is_external_action and not ctx.reversible:
                reason = (
                    "Irreversible external action at HIGH consequence; "
                    "policy requires human approval"
                )
                logger.info("HUMAN_APPROVAL: %s", reason)
                return DecisionResult(
                    action=Decision.HUMAN_APPROVAL,
                    reason=reason,
                    requires_human=True,
                )

        # ── 4. MEDIUM + uncertainty → VERIFY ────────────────────
        if consequence_tier == ConsequenceTier.MEDIUM:
            if eval_result.has_failures:
                logger.info("BLOCK (MEDIUM + failures)")
                return DecisionResult(
                    action=Decision.BLOCK,
                    reason="Evaluation failures in MEDIUM consequence interaction",
                    requires_human=False,
                )

            if eval_result.has_uncertainty:
                logger.info("VERIFY (MEDIUM + uncertainty)")
                return DecisionResult(
                    action=Decision.VERIFY,
                    reason=(
                        "Uncertainty in MEDIUM consequence evaluation; "
                        "verification recommended"
                    ),
                    requires_human=False,
                )

        # ── 5. Performance issues → VERIFY ──────────────────────
        if performance.status == CheckStatus.FAIL:
            return DecisionResult(
                action=Decision.VERIFY,
                reason=f"Performance concern: {performance.reason}",
                requires_human=False,
            )

        if performance.status == CheckStatus.UNCERTAIN:
            reasons.append(f"Performance uncertainty: {performance.reason}")

        # ── 6. Cost anomaly → VERIFY ────────────────────────────
        if cost.is_anomalous:
            anomaly_desc = "; ".join(cost.anomaly_reasons)
            return DecisionResult(
                action=Decision.VERIFY,
                reason=f"Cost anomaly detected: {anomaly_desc}",
                requires_human=False,
            )

        # ── 7. All clear → PASS ────────────────────────────────
        if reasons:
            return DecisionResult(
                action=Decision.VERIFY,
                reason="; ".join(reasons),
                requires_human=False,
            )

        logger.info("PASS: all evaluations clear")
        return DecisionResult(
            action=Decision.PASS,
            reason="All evaluations passed; request approved",
            requires_human=False,
        )
