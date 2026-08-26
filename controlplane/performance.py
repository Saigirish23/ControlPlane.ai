"""
ControlPlane.AI — Performance Evaluator

Evaluates AI response quality signals:
- Groundedness (does the response stay within the supplied context?)
- Contextual relevance (is the response relevant to the request?)
- Semantic consistency (is the response internally consistent?)

For the MVP, this is a lightweight heuristic evaluator. The architecture
supports plugging in an LLM-backed evaluator without changing the interface.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from controlplane.models import CheckStatus, PerformanceResult

logger = logging.getLogger(__name__)


class PerformanceEvaluator:
    """
    Evaluates performance signals for a request-response pair.

    For the MVP, uses simple heuristics. The interface allows swapping in
    an LLM-backed evaluator later.
    """

    def evaluate(
        self,
        request_text: str,
        response_text: Optional[str] = None,
        reference_context: Optional[str] = None,
    ) -> PerformanceResult:
        """
        Evaluate performance of the response against the request.

        If no response_text is available (pre-evaluation), returns a
        neutral PASS with no evidence.
        """
        if response_text is None:
            return PerformanceResult(
                status=CheckStatus.PASS,
                reason="Pre-evaluation; no response to assess",
            )

        evidence: List[str] = []

        groundedness = self._check_groundedness(
            response_text, reference_context, evidence
        )
        relevance = self._check_relevance(
            request_text, response_text, evidence
        )
        consistency = self._check_consistency(response_text, evidence)

        # Overall status: worst of the three
        statuses = [groundedness, relevance, consistency]
        if CheckStatus.FAIL in statuses:
            overall = CheckStatus.FAIL
        elif CheckStatus.UNCERTAIN in statuses:
            overall = CheckStatus.UNCERTAIN
        else:
            overall = CheckStatus.PASS

        reason = "; ".join(evidence) if evidence else "All checks passed"

        return PerformanceResult(
            status=overall,
            reason=reason,
            evidence=evidence,
            groundedness=groundedness,
            relevance=relevance,
            consistency=consistency,
        )

    def _check_groundedness(
        self,
        response: str,
        context: Optional[str],
        evidence: List[str],
    ) -> CheckStatus:
        """Check if response is grounded in provided context."""
        if context is None:
            return CheckStatus.PASS  # No context to check against

        # Heuristic: if response is very long relative to context, flag
        if len(response) > len(context) * 5 and len(context) > 50:
            evidence.append(
                "Response is significantly longer than reference context; "
                "may contain ungrounded content"
            )
            return CheckStatus.UNCERTAIN

        return CheckStatus.PASS

    def _check_relevance(
        self,
        request: str,
        response: str,
        evidence: List[str],
    ) -> CheckStatus:
        """Basic relevance check using keyword overlap."""
        if not response.strip():
            evidence.append("Empty response")
            return CheckStatus.FAIL

        # Simple word overlap heuristic
        req_words = set(request.lower().split())
        resp_words = set(response.lower().split())

        # Remove stop words for a cleaner signal
        stop = {"the", "a", "an", "is", "to", "and", "of", "in", "for", "it",
                "this", "that", "be", "are", "was", "with", "on", "at", "by"}
        req_meaningful = req_words - stop
        resp_meaningful = resp_words - stop

        if req_meaningful and not req_meaningful & resp_meaningful:
            evidence.append("Response has no keyword overlap with request")
            return CheckStatus.UNCERTAIN

        return CheckStatus.PASS

    def _check_consistency(
        self,
        response: str,
        evidence: List[str],
    ) -> CheckStatus:
        """Basic internal consistency check."""
        # Heuristic: check for contradictory statements
        contradiction_pairs = [
            ("yes", "no"),
            ("true", "false"),
            ("correct", "incorrect"),
            ("should", "should not"),
        ]
        lower = response.lower()
        for pos, neg in contradiction_pairs:
            if f" {pos} " in f" {lower} " and f" {neg} " in f" {lower} ":
                evidence.append(
                    f"Potential contradiction: both '{pos}' and '{neg}' "
                    f"appear in response"
                )
                return CheckStatus.UNCERTAIN

        return CheckStatus.PASS
