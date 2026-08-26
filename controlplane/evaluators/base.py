"""
ControlPlane.AI — Evaluator Base Interface

Defines the abstract evaluator protocol that all evaluation depth paths
(FAST, DEEP, HIGH_ASSURANCE) must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

from controlplane.context_extractor import RequestContext
from controlplane.models import CheckResult, CheckStatus, EvaluationDepth


@dataclass
class EvalResult:
    """Result from an evaluator run."""

    depth: EvaluationDepth
    overall_status: CheckStatus
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.overall_status == CheckStatus.PASS

    @property
    def has_failures(self) -> bool:
        return any(c.status == CheckStatus.FAIL for c in self.checks)

    @property
    def has_uncertainty(self) -> bool:
        return any(c.status == CheckStatus.UNCERTAIN for c in self.checks)


class Evaluator(ABC):
    """Abstract evaluator that all depth paths must implement."""

    @abstractmethod
    async def evaluate(
        self, ctx: RequestContext, request_text: str
    ) -> EvalResult:
        """
        Evaluate the given request in its context.

        Returns an EvalResult with the evaluation depth, overall status,
        and individual check results.
        """
        ...
