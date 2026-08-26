"""Tests for CostEvaluator."""

import pytest

from controlplane.cost import CostEvaluator


class TestCostEvaluator:
    def setup_method(self):
        self.evaluator = CostEvaluator()

    def test_normal_request(self):
        result = self.evaluator.evaluate(
            input_tokens=500,
            output_tokens=200,
            model="gpt-4o-mini",
            latency_ms=150.0,
        )
        assert result.total_tokens == 700
        assert not result.is_anomalous
        assert result.estimated_cost_usd is not None
        assert result.estimated_cost_usd > 0

    def test_excessive_retries_anomaly(self):
        result = self.evaluator.evaluate(
            input_tokens=500,
            output_tokens=200,
            retries=5,
        )
        assert result.is_anomalous
        assert any("retries" in r.lower() for r in result.anomaly_reasons)

    def test_too_many_model_calls_anomaly(self):
        result = self.evaluator.evaluate(
            input_tokens=500,
            output_tokens=200,
            model_calls=10,
        )
        assert result.is_anomalous
        assert any("model calls" in r.lower() for r in result.anomaly_reasons)

    def test_excessive_tokens_anomaly(self):
        result = self.evaluator.evaluate(
            input_tokens=80_000,
            output_tokens=30_000,
        )
        assert result.is_anomalous
        assert any("token" in r.lower() for r in result.anomaly_reasons)

    def test_unknown_model_no_cost(self):
        result = self.evaluator.evaluate(
            input_tokens=500,
            output_tokens=200,
            model="unknown-model",
        )
        assert result.estimated_cost_usd is None

    def test_known_model_cost_calculation(self):
        result = self.evaluator.evaluate(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            model="gpt-4o-mini",
        )
        # gpt-4o-mini: $0.15/1M input + $0.60/1M output = $0.75
        assert result.estimated_cost_usd is not None
        assert abs(result.estimated_cost_usd - 0.75) < 0.01

    def test_zero_tokens(self):
        result = self.evaluator.evaluate()
        assert result.total_tokens == 0
        assert not result.is_anomalous
