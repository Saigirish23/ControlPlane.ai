"""Tests for ActionRouter."""

import pytest

from controlplane.action_router import ActionRouter
from controlplane.context_extractor import ContextExtractor
from controlplane.evaluators.base import EvalResult
from controlplane.models import (
    ActionType,
    CheckResult,
    CheckStatus,
    ConsequenceTier,
    ControlRequest,
    CostResult,
    Decision,
    Domain,
    EvaluationDepth,
    InteractionContext,
    PerformanceResult,
    ResponsibilityResult,
    UserContext,
)


def _ctx(
    domain=Domain.GENERAL,
    action_type=ActionType.INFORMATIONAL,
    reversible=True,
):
    req = ControlRequest(
        request="test",
        user_context=UserContext(user_role="user"),
        interaction_context=InteractionContext(
            domain=domain, action_type=action_type, reversible=reversible
        ),
    )
    return ContextExtractor().extract(req)


def _eval_result(
    status=CheckStatus.PASS,
    checks=None,
    depth=EvaluationDepth.FAST,
):
    return EvalResult(
        depth=depth,
        overall_status=status,
        checks=checks or [],
    )


def _responsibility(status=CheckStatus.PASS, checks=None):
    return ResponsibilityResult(status=status, checks=checks or [])


def _performance(status=CheckStatus.PASS, reason=""):
    return PerformanceResult(status=status, reason=reason)


def _cost(is_anomalous=False, reasons=None):
    return CostResult(is_anomalous=is_anomalous, anomaly_reasons=reasons or [])


class TestActionRouter:
    def setup_method(self):
        self.router = ActionRouter()

    def test_low_all_pass(self):
        """LOW + all PASS → PASS."""
        result = self.router.route(
            ctx=_ctx(),
            consequence_tier=ConsequenceTier.LOW,
            eval_result=_eval_result(),
            responsibility=_responsibility(),
            performance=_performance(),
            cost=_cost(),
        )
        assert result.action == Decision.PASS

    def test_injection_blocks(self):
        """Injection detected → BLOCK regardless of tier."""
        checks = [
            CheckResult(
                name="injection",
                status=CheckStatus.FAIL,
                category="INJECTION",
                reason="Ignore instructions pattern",
            )
        ]
        result = self.router.route(
            ctx=_ctx(),
            consequence_tier=ConsequenceTier.LOW,
            eval_result=_eval_result(),
            responsibility=_responsibility(
                status=CheckStatus.FAIL, checks=checks
            ),
            performance=_performance(),
            cost=_cost(),
        )
        assert result.action == Decision.BLOCK

    def test_pii_low_modifies(self):
        """PII detected at LOW → MODIFY."""
        checks = [
            CheckResult(
                name="pii_email",
                status=CheckStatus.FAIL,
                category="PII",
                reason="Email detected",
            )
        ]
        result = self.router.route(
            ctx=_ctx(),
            consequence_tier=ConsequenceTier.LOW,
            eval_result=_eval_result(),
            responsibility=_responsibility(
                status=CheckStatus.FAIL, checks=checks
            ),
            performance=_performance(),
            cost=_cost(),
        )
        assert result.action == Decision.MODIFY

    def test_pii_high_blocks(self):
        """PII detected at HIGH → BLOCK."""
        checks = [
            CheckResult(
                name="pii_email",
                status=CheckStatus.FAIL,
                category="PII",
                reason="Email detected",
            )
        ]
        result = self.router.route(
            ctx=_ctx(
                domain=Domain.FINANCE,
                action_type=ActionType.EXTERNAL_ACTION,
                reversible=False,
            ),
            consequence_tier=ConsequenceTier.HIGH,
            eval_result=_eval_result(),
            responsibility=_responsibility(
                status=CheckStatus.FAIL, checks=checks
            ),
            performance=_performance(),
            cost=_cost(),
        )
        assert result.action == Decision.BLOCK

    def test_high_uncertainty_human_approval(self):
        """HIGH + uncertainty → HUMAN_APPROVAL."""
        uncertain_checks = [
            CheckResult(
                name="test",
                status=CheckStatus.UNCERTAIN,
                reason="Uncertain",
            )
        ]
        result = self.router.route(
            ctx=_ctx(
                domain=Domain.FINANCE,
                action_type=ActionType.EXTERNAL_ACTION,
                reversible=False,
            ),
            consequence_tier=ConsequenceTier.HIGH,
            eval_result=_eval_result(
                status=CheckStatus.UNCERTAIN, checks=uncertain_checks
            ),
            responsibility=_responsibility(),
            performance=_performance(),
            cost=_cost(),
        )
        assert result.action == Decision.HUMAN_APPROVAL
        assert result.requires_human is True

    def test_high_irreversible_external_human_approval(self):
        """HIGH + irreversible external action → HUMAN_APPROVAL (even all pass)."""
        result = self.router.route(
            ctx=_ctx(
                domain=Domain.FINANCE,
                action_type=ActionType.EXTERNAL_ACTION,
                reversible=False,
            ),
            consequence_tier=ConsequenceTier.HIGH,
            eval_result=_eval_result(),
            responsibility=_responsibility(),
            performance=_performance(),
            cost=_cost(),
        )
        assert result.action == Decision.HUMAN_APPROVAL

    def test_medium_uncertainty_verify(self):
        """MEDIUM + uncertainty → VERIFY."""
        uncertain_checks = [
            CheckResult(
                name="test",
                status=CheckStatus.UNCERTAIN,
                reason="Uncertain",
            )
        ]
        result = self.router.route(
            ctx=_ctx(domain=Domain.FINANCE, action_type=ActionType.DECISION),
            consequence_tier=ConsequenceTier.MEDIUM,
            eval_result=_eval_result(
                status=CheckStatus.UNCERTAIN, checks=uncertain_checks
            ),
            responsibility=_responsibility(),
            performance=_performance(),
            cost=_cost(),
        )
        assert result.action == Decision.VERIFY

    def test_cost_anomaly_verify(self):
        """Cost anomaly → VERIFY."""
        result = self.router.route(
            ctx=_ctx(),
            consequence_tier=ConsequenceTier.LOW,
            eval_result=_eval_result(),
            responsibility=_responsibility(),
            performance=_performance(),
            cost=_cost(
                is_anomalous=True,
                reasons=["Excessive retries (5 > 2)"],
            ),
        )
        assert result.action == Decision.VERIFY

    def test_every_decision_has_reason(self):
        """Every decision must include a human-readable reason."""
        result = self.router.route(
            ctx=_ctx(),
            consequence_tier=ConsequenceTier.LOW,
            eval_result=_eval_result(),
            responsibility=_responsibility(),
            performance=_performance(),
            cost=_cost(),
        )
        assert result.reason
        assert isinstance(result.reason, str)
        assert len(result.reason) > 5
