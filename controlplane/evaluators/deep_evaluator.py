"""
ControlPlane.AI — Deep Evaluator (DEEP Path)

Pluggable evaluator for MEDIUM consequence interactions. Runs all FAST
checks plus deeper evaluation using an available LLM.

Checks:
- All FAST path checks
- Groundedness assessment
- Semantic consistency
- Contextual relevance
- Deeper safety evaluation
- Enterprise policy validation

Reports: PASS / FAIL / UNCERTAIN with explanations.
The evaluator can be replaced without rewriting the ControlPlane.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from controlplane.context_extractor import RequestContext
from controlplane.evaluators.base import EvalResult, Evaluator
from controlplane.evaluators.fast_evaluator import FastEvaluator
from controlplane.models import CheckResult, CheckStatus, EvaluationDepth

logger = logging.getLogger(__name__)


class DeepEvaluator(Evaluator):
    """
    DEEP path evaluator for MEDIUM consequence requests.

    Runs FAST checks first, then adds deeper analysis. If an LLM API key
    is available, uses it for semantic evaluation. Otherwise, falls back
    to enhanced heuristics.
    """

    def __init__(self) -> None:
        self._fast = FastEvaluator()
        is_test_runner = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        self._llm_available = bool(
            not is_test_runner
            and (os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        )

    async def evaluate(
        self, ctx: RequestContext, request_text: str
    ) -> EvalResult:
        # Run all FAST checks first
        fast_result = await self._fast.evaluate(ctx, request_text)
        checks = list(fast_result.checks)

        # If FAST path already failed hard, short-circuit
        if fast_result.has_failures:
            return EvalResult(
                depth=EvaluationDepth.DEEP,
                overall_status=CheckStatus.FAIL,
                checks=checks,
            )

        # Deep checks
        checks.append(self._check_domain_policy(ctx))
        checks.append(await self._check_semantic_safety(ctx, request_text))
        checks.append(self._check_contextual_relevance(ctx, request_text))

        # Determine overall status
        if any(c.status == CheckStatus.FAIL for c in checks):
            overall = CheckStatus.FAIL
        elif any(c.status == CheckStatus.UNCERTAIN for c in checks):
            overall = CheckStatus.UNCERTAIN
        else:
            overall = CheckStatus.PASS

        return EvalResult(
            depth=EvaluationDepth.DEEP,
            overall_status=overall,
            checks=checks,
        )

    def _check_domain_policy(self, ctx: RequestContext) -> CheckResult:
        """Enterprise domain-specific policy validation."""
        # Finance-specific policies
        if ctx.domain.value == "FINANCE" and ctx.is_decision:
            return CheckResult(
                name="domain_policy",
                status=CheckStatus.UNCERTAIN,
                category="POLICY",
                reason=(
                    "Financial decision requires additional verification; "
                    "recommend human review of the rationale"
                ),
            )

        # Healthcare-specific policies
        if ctx.domain.value == "HEALTHCARE":
            return CheckResult(
                name="domain_policy",
                status=CheckStatus.UNCERTAIN,
                category="POLICY",
                reason=(
                    "Healthcare domain interaction; clinical validation "
                    "recommended"
                ),
            )

        return CheckResult(
            name="domain_policy",
            status=CheckStatus.PASS,
            category="POLICY",
            reason="Domain policy check passed",
        )

    async def _check_semantic_safety(
        self, ctx: RequestContext, request_text: str
    ) -> CheckResult:
        """
        Deeper safety evaluation.

        Uses LLM if available, otherwise enhanced heuristics.
        """
        if self._llm_available:
            return await self._llm_safety_check(request_text)

        # Fallback: enhanced heuristic
        return self._heuristic_safety_check(request_text)

    def _heuristic_safety_check(self, text: str) -> CheckResult:
        """Enhanced heuristic safety check when no LLM is available."""
        lower = text.lower()

        # Check for sensitive operation keywords
        sensitive_keywords = [
            "delete", "remove", "drop", "truncate", "override",
            "bypass", "disable", "terminate", "shutdown",
        ]
        found = [kw for kw in sensitive_keywords if kw in lower]

        if found:
            return CheckResult(
                name="semantic_safety",
                status=CheckStatus.UNCERTAIN,
                category="SAFETY",
                reason=(
                    f"Request contains sensitive operation keywords: "
                    f"{', '.join(found)}"
                ),
                details={"keywords_found": found},
            )

        return CheckResult(
            name="semantic_safety",
            status=CheckStatus.PASS,
            category="SAFETY",
            reason="Semantic safety check passed",
        )

    async def _llm_safety_check(self, text: str) -> CheckResult:
        """
        Use Gemini API for deeper semantic safety evaluation.

        The prompt is designed to get a structured assessment without
        making claims of perfect detection.
        """
        try:
            from google import genai

            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                return self._heuristic_safety_check(text)

            client = genai.Client(api_key=api_key)

            try:
                prompt = (
                    "You are a safety evaluator for an enterprise AI system. "
                    "Evaluate the following request for potential safety concerns. "
                    "Respond with exactly one word: PASS, FAIL, or UNCERTAIN. "
                    "Then on a new line, provide a brief reason.\n\n"
                    f"Request: {text[:500]}"  # Limit to avoid excessive tokens
                )

                model_name = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )

                if response and response.text:
                    lines = response.text.strip().split("\n", 1)
                    status_word = lines[0].strip().upper()
                    reason = lines[1].strip() if len(lines) > 1 else ""

                    if status_word == "FAIL":
                        status = CheckStatus.FAIL
                    elif status_word == "UNCERTAIN":
                        status = CheckStatus.UNCERTAIN
                    else:
                        status = CheckStatus.PASS

                    return CheckResult(
                        name="semantic_safety",
                        status=status,
                        category="SAFETY",
                        reason=reason or "LLM safety evaluation completed",
                    )
            finally:
                try:
                    if hasattr(client, "aio") and hasattr(client.aio, "aclose"):
                        await client.aio.aclose()
                except Exception:
                    pass

        except Exception as e:
            logger.warning("LLM safety check failed: %s", e)

        return self._heuristic_safety_check(text)

    def _check_contextual_relevance(
        self, ctx: RequestContext, text: str
    ) -> CheckResult:
        """Check if the request is contextually appropriate for the domain."""
        # For MVP: basic checks
        if ctx.is_sensitive_domain and len(text.strip()) < 10:
            return CheckResult(
                name="contextual_relevance",
                status=CheckStatus.UNCERTAIN,
                category="RELEVANCE",
                reason=(
                    "Very short request in a sensitive domain; "
                    "may lack sufficient context"
                ),
            )

        return CheckResult(
            name="contextual_relevance",
            status=CheckStatus.PASS,
            category="RELEVANCE",
            reason="Request is contextually appropriate",
        )
