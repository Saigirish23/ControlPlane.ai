"""
ControlPlane.AI — Cost Evaluator

Tracks request-level cost and efficiency telemetry. Identifies obvious
inefficiencies (excessive retries, agent loops, unusually large contexts).

Monetary cost calculation is configurable by model/provider.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from controlplane.models import CostResult

logger = logging.getLogger(__name__)


# Configurable cost per 1M tokens (USD) by model.
# Extend this dict for additional models/providers.
MODEL_COSTS_PER_1M: Dict[str, Dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-3.6-flash": {"input": 0.15, "output": 0.60},
}

# Anomaly detection thresholds
MAX_NORMAL_RETRIES = 2
MAX_NORMAL_MODEL_CALLS = 3
MAX_NORMAL_TOKEN_COUNT = 100_000


class CostEvaluator:
    """
    Evaluates cost and efficiency of a request's resource usage.

    Detects anomalous patterns:
    - Excessive retries
    - Too many model calls (potential agent loop)
    - Unusually large token context
    """

    def __init__(
        self,
        cost_table: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> None:
        self._cost_table = cost_table or MODEL_COSTS_PER_1M

    def evaluate(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = "",
        latency_ms: float = 0.0,
        model_calls: int = 1,
        tool_calls: int = 0,
        retries: int = 0,
    ) -> CostResult:
        """Compute cost metrics and detect anomalies."""
        total_tokens = input_tokens + output_tokens
        estimated_cost = self._estimate_cost(
            model, input_tokens, output_tokens
        )

        anomaly_reasons: List[str] = []

        if retries > MAX_NORMAL_RETRIES:
            anomaly_reasons.append(
                f"Excessive retries ({retries} > {MAX_NORMAL_RETRIES})"
            )

        if model_calls > MAX_NORMAL_MODEL_CALLS:
            anomaly_reasons.append(
                f"Too many model calls ({model_calls} > "
                f"{MAX_NORMAL_MODEL_CALLS}); potential agent loop"
            )

        if total_tokens > MAX_NORMAL_TOKEN_COUNT:
            anomaly_reasons.append(
                f"Unusually large token count ({total_tokens:,} > "
                f"{MAX_NORMAL_TOKEN_COUNT:,})"
            )

        is_anomalous = len(anomaly_reasons) > 0
        if is_anomalous:
            logger.warning("Cost anomaly: %s", "; ".join(anomaly_reasons))

        return CostResult(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=model,
            latency_ms=latency_ms,
            model_calls=model_calls,
            tool_calls=tool_calls,
            retries=retries,
            estimated_cost_usd=estimated_cost,
            is_anomalous=is_anomalous,
            anomaly_reasons=anomaly_reasons,
        )

    def _estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> Optional[float]:
        """Estimate USD cost based on model cost table."""
        if model not in self._cost_table:
            return None
        rates = self._cost_table[model]
        cost = (
            input_tokens * rates["input"] / 1_000_000
            + output_tokens * rates["output"] / 1_000_000
        )
        return round(cost, 6)
