"""
ControlPlane.AI — Consequence Engine

Determines the consequence tier of an AI interaction using three contextual
signals: reversibility, domain sensitivity, and action type.

The rule system is data-driven: a list of ConsequenceRule objects evaluated
in priority order. Enterprise policies can extend this by appending or
replacing rules without modifying the engine itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from controlplane.context_extractor import RequestContext
from controlplane.models import (
    ActionType,
    ConsequenceResult,
    ConsequenceTier,
    DataSensitivity,
    Domain,
)

logger = logging.getLogger(__name__)


@dataclass
class ConsequenceRule:
    """
    A single rule that, if matched, produces a consequence tier.

    Rules are evaluated in priority order (lower number = higher priority).
    The first matching rule wins.
    """

    name: str
    tier: ConsequenceTier
    priority: int
    match: Callable[[RequestContext], bool]
    reason_template: str
    factors_fn: Callable[[RequestContext], List[str]] = field(
        default_factory=lambda: lambda _ctx: []
    )


def _is_refund_within_policy(ctx: RequestContext) -> bool:
    """Check if request is a refund action within the auto-approval policy limit."""
    if ctx.tool_name != "request_refund_or_replacement":
        return False
    requested_amount = ctx.parameters.get("requested_amount")
    if requested_amount is None:
        return False
    refund_limit = ctx.metadata.get("refund_limit", 200.0)
    try:
        return float(requested_amount) <= float(refund_limit)
    except (ValueError, TypeError):
        return False


def _default_factors(ctx: RequestContext) -> List[str]:
    """Build a list of contributing factors from context."""
    factors: List[str] = []
    if ctx.is_sensitive_domain:
        factors.append(ctx.domain.value.lower())
    factors.append(ctx.action_type.value.lower())
    if not ctx.reversible:
        factors.append("irreversible")
    if ctx.data_sensitivity == DataSensitivity.HIGH:
        factors.append("high_data_sensitivity")
    return factors


def _build_default_rules() -> List[ConsequenceRule]:
    """
    Default consequence rules.

    Design principles:
    - HIGH: irreversible external actions, especially in sensitive domains.
    - MEDIUM: decisions in sensitive domains, reversible external actions,
      sensitive informational requests.
    - LOW: general informational, reversible, non-sensitive, or actions within policy limits.
    """
    return sorted(
        [
            # ── Enterprise Policy Rules (Highest Priority) ──────────
            ConsequenceRule(
                name="refund_within_policy_limit",
                tier=ConsequenceTier.LOW,
                priority=5,
                match=_is_refund_within_policy,
                reason_template=(
                    "Refund amount is within auto-approval policy limit"
                ),
                factors_fn=lambda _ctx: ["finance", "within_policy_limit"],
            ),
            # ── HIGH rules ──────────────────────────────────────────
            ConsequenceRule(
                name="irreversible_external_action_sensitive",
                tier=ConsequenceTier.HIGH,
                priority=10,
                match=lambda ctx: (
                    ctx.is_external_action
                    and not ctx.reversible
                    and ctx.is_sensitive_domain
                ),
                reason_template=(
                    "Irreversible external action in a sensitive "
                    "{domain} domain"
                ),
                factors_fn=_default_factors,
            ),
            ConsequenceRule(
                name="irreversible_external_action_general",
                tier=ConsequenceTier.HIGH,
                priority=15,
                match=lambda ctx: (
                    ctx.is_external_action and not ctx.reversible
                ),
                reason_template="Irreversible external action",
                factors_fn=_default_factors,
            ),
            ConsequenceRule(
                name="high_sensitivity_external_action",
                tier=ConsequenceTier.HIGH,
                priority=18,
                match=lambda ctx: (
                    ctx.is_external_action
                    and ctx.data_sensitivity == DataSensitivity.HIGH
                ),
                reason_template=(
                    "External action involving highly sensitive data"
                ),
                factors_fn=_default_factors,
            ),
            # ── MEDIUM rules ───────────────────────────────────────
            ConsequenceRule(
                name="sensitive_domain_decision",
                tier=ConsequenceTier.MEDIUM,
                priority=30,
                match=lambda ctx: (
                    ctx.is_sensitive_domain and ctx.is_decision
                ),
                reason_template=(
                    "Decision/recommendation in sensitive {domain} domain"
                ),
                factors_fn=_default_factors,
            ),
            ConsequenceRule(
                name="reversible_external_action",
                tier=ConsequenceTier.MEDIUM,
                priority=35,
                match=lambda ctx: (
                    ctx.is_external_action and ctx.reversible
                ),
                reason_template="Reversible external action",
                factors_fn=_default_factors,
            ),
            ConsequenceRule(
                name="sensitive_domain_informational",
                tier=ConsequenceTier.MEDIUM,
                priority=40,
                match=lambda ctx: (
                    ctx.is_sensitive_domain
                    and ctx.action_type == ActionType.INFORMATIONAL
                    and ctx.data_sensitivity
                    in {DataSensitivity.MEDIUM, DataSensitivity.HIGH}
                ),
                reason_template=(
                    "Informational request in sensitive {domain} domain "
                    "with elevated data sensitivity"
                ),
                factors_fn=_default_factors,
            ),
            ConsequenceRule(
                name="high_sensitivity_decision",
                tier=ConsequenceTier.MEDIUM,
                priority=42,
                match=lambda ctx: (
                    ctx.is_decision
                    and ctx.data_sensitivity == DataSensitivity.HIGH
                ),
                reason_template=(
                    "Decision involving highly sensitive data"
                ),
                factors_fn=_default_factors,
            ),
            # ── LOW (default) ──────────────────────────────────────
            ConsequenceRule(
                name="general_informational",
                tier=ConsequenceTier.LOW,
                priority=100,
                match=lambda _ctx: True,  # catch-all
                reason_template="Standard interaction with low consequence",
                factors_fn=_default_factors,
            ),
        ],
        key=lambda r: r.priority,
    )


class ConsequenceEngine:
    """
    Evaluates contextual signals and classifies the consequence tier.

    The engine processes rules in priority order and returns the tier from
    the first matching rule. Custom enterprise rules can be injected via
    the constructor.
    """

    def __init__(
        self, custom_rules: Optional[List[ConsequenceRule]] = None
    ) -> None:
        if custom_rules is not None:
            self._rules = sorted(custom_rules, key=lambda r: r.priority)
        else:
            self._rules = _build_default_rules()

    def evaluate(self, ctx: RequestContext) -> ConsequenceResult:
        """Classify the consequence tier for the given context."""
        for rule in self._rules:
            if rule.match(ctx):
                reason = rule.reason_template.format(
                    domain=ctx.domain.value.lower()
                )
                factors = rule.factors_fn(ctx)
                logger.info(
                    "Consequence: tier=%s rule=%s reason=%s",
                    rule.tier.value,
                    rule.name,
                    reason,
                )
                return ConsequenceResult(
                    tier=rule.tier, reason=reason, factors=factors
                )

        # Should never reach here due to catch-all, but be safe.
        return ConsequenceResult(
            tier=ConsequenceTier.LOW,
            reason="No matching rule; defaulting to LOW",
            factors=[],
        )
