"""Tests for DepthPlanner."""

import pytest

from controlplane.depth_planner import DepthPlanner
from controlplane.models import ConsequenceTier, EvaluationDepth


class TestDepthPlanner:
    """Test depth mapping."""

    def setup_method(self):
        self.planner = DepthPlanner()

    def test_low_maps_to_fast(self):
        assert self.planner.plan(ConsequenceTier.LOW) == EvaluationDepth.FAST

    def test_medium_maps_to_deep(self):
        assert self.planner.plan(ConsequenceTier.MEDIUM) == EvaluationDepth.DEEP

    def test_high_maps_to_high_assurance(self):
        assert self.planner.plan(ConsequenceTier.HIGH) == EvaluationDepth.HIGH_ASSURANCE

    def test_all_tiers_mapped(self):
        """Every ConsequenceTier must have a mapping."""
        for tier in ConsequenceTier:
            result = self.planner.plan(tier)
            assert isinstance(result, EvaluationDepth)
