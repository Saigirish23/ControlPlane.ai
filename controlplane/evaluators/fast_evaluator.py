"""
ControlPlane.AI — Fast Evaluator (FAST Path)

Lightweight deterministic checks for LOW consequence interactions.
Demonstrates: LOW CONSEQUENCE → LIGHTWEIGHT CHECKS → LOW OVERHEAD → FAST DECISION.

Checks:
- PII pattern detection
- Prompt injection / malicious pattern detection
- Basic policy checks
- Authorization / context checks
- Format validation
"""

from __future__ import annotations

import logging

from controlplane.context_extractor import RequestContext
from controlplane.evaluators.base import EvalResult, Evaluator
from controlplane.models import CheckResult, CheckStatus, EvaluationDepth
from controlplane.responsibility import ResponsibilityEvaluator

logger = logging.getLogger(__name__)


class FastEvaluator(Evaluator):
    """
    FAST path evaluator for LOW consequence requests.

    All checks are deterministic and regex-based. No LLM calls.
    Target latency: < 10ms.
    """

    def __init__(self) -> None:
        self._responsibility = ResponsibilityEvaluator()

    async def evaluate(
        self, ctx: RequestContext, request_text: str
    ) -> EvalResult:
        checks: list[CheckResult] = []

        # 1. Responsibility checks (PII, injection, safety)
        resp_result = self._responsibility.evaluate(request_text)
        checks.extend(resp_result.checks)

        # 2. Basic policy check — role-based authorization
        checks.append(self._check_authorization(ctx))

        # 3. Format validation — ensure request is non-empty
        checks.append(self._check_format(request_text))

        # If no checks failed explicitly, add a summary PASS
        if not checks:
            checks.append(
                CheckResult(
                    name="fast_path_all",
                    status=CheckStatus.PASS,
                    reason="All fast-path checks passed",
                )
            )

        # Determine overall status
        if any(c.status == CheckStatus.FAIL for c in checks):
            overall = CheckStatus.FAIL
        elif any(c.status == CheckStatus.UNCERTAIN for c in checks):
            overall = CheckStatus.UNCERTAIN
        else:
            overall = CheckStatus.PASS

        return EvalResult(
            depth=EvaluationDepth.FAST,
            overall_status=overall,
            checks=checks,
        )

    def _check_authorization(self, ctx: RequestContext) -> CheckResult:
        """Basic role-based authorization check."""
        # For MVP: external actions require a known role
        if ctx.is_external_action and ctx.user_role == "unknown":
            return CheckResult(
                name="authorization",
                status=CheckStatus.FAIL,
                category="POLICY",
                reason="External action requested by unknown user role",
            )
        return CheckResult(
            name="authorization",
            status=CheckStatus.PASS,
            category="POLICY",
            reason="User role authorized for action type",
        )

    def _check_format(self, text: str) -> CheckResult:
        """Validate request format."""
        if not text or not text.strip():
            return CheckResult(
                name="format",
                status=CheckStatus.FAIL,
                category="FORMAT",
                reason="Empty request text",
            )
        if len(text) > 100_000:
            return CheckResult(
                name="format",
                status=CheckStatus.UNCERTAIN,
                category="FORMAT",
                reason="Unusually large request text",
            )
        return CheckResult(
            name="format",
            status=CheckStatus.PASS,
            category="FORMAT",
            reason="Request format valid",
        )
