"""
ControlPlane.AI — Depth Planner

Maps ConsequenceTier → EvaluationDepth.

This is deliberately a separate component to keep consequence classification
and evaluation execution decoupled.
"""

from __future__ import annotations

import logging

from controlplane.models import ConsequenceTier, EvaluationDepth

logger = logging.getLogger(__name__)

# Canonical mapping — the single source of truth.
DEPTH_MAP: dict[ConsequenceTier, EvaluationDepth] = {
    ConsequenceTier.LOW: EvaluationDepth.FAST,
    ConsequenceTier.MEDIUM: EvaluationDepth.DEEP,
    ConsequenceTier.HIGH: EvaluationDepth.HIGH_ASSURANCE,
}


class DepthPlanner:
    """Maps a consequence tier to the appropriate evaluation depth."""

    def plan(self, tier: ConsequenceTier) -> EvaluationDepth:
        depth = DEPTH_MAP[tier]
        logger.info("Depth plan: %s → %s", tier.value, depth.value)
        return depth
