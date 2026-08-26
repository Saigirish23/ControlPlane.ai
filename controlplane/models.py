"""
ControlPlane.AI — Domain Models

Strongly typed enums and Pydantic models for the entire ControlPlane
decision pipeline. No raw strings in business logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Domain Enums
# ─────────────────────────────────────────────


class ConsequenceTier(str, Enum):
    """Real-world consequence level of an AI interaction."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EvaluationDepth(str, Enum):
    """Depth of evaluation applied based on consequence tier."""

    FAST = "FAST"
    DEEP = "DEEP"
    HIGH_ASSURANCE = "HIGH_ASSURANCE"


class ActionType(str, Enum):
    """What the AI is doing."""

    INFORMATIONAL = "INFORMATIONAL"
    DECISION = "DECISION"
    EXTERNAL_ACTION = "EXTERNAL_ACTION"


class Domain(str, Enum):
    """Business domain of the interaction."""

    GENERAL = "GENERAL"
    FINANCE = "FINANCE"
    HEALTHCARE = "HEALTHCARE"
    LEGAL = "LEGAL"
    SECURITY = "SECURITY"
    INFRASTRUCTURE = "INFRASTRUCTURE"


class Decision(str, Enum):
    """Final action taken by the ControlPlane."""

    PASS = "PASS"
    MODIFY = "MODIFY"
    VERIFY = "VERIFY"
    BLOCK = "BLOCK"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"


class CheckStatus(str, Enum):
    """Result status for an individual check or evaluator."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"


class DataSensitivity(str, Enum):
    """Sensitivity level of data involved."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ─────────────────────────────────────────────
# Request / Context Models
# ─────────────────────────────────────────────


class InteractionContext(BaseModel):
    """Contextual signals about the AI interaction."""

    domain: Domain = Domain.GENERAL
    action_type: ActionType = ActionType.INFORMATIONAL
    reversible: bool = True
    data_sensitivity: DataSensitivity = DataSensitivity.LOW


class UserContext(BaseModel):
    """Information about the requesting user/agent."""

    user_role: str = "unknown"
    user_id: Optional[str] = None
    session_id: Optional[str] = None


class ControlRequest(BaseModel):
    """Inbound request to the ControlPlane."""

    request: str
    user_context: UserContext = Field(default_factory=UserContext)
    interaction_context: InteractionContext = Field(
        default_factory=InteractionContext
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    """An AI-generated tool call intercepted by the execution rail."""

    tool: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    user_context: UserContext = Field(default_factory=UserContext)
    interaction_context: InteractionContext = Field(
        default_factory=InteractionContext
    )
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────
# Result Models
# ─────────────────────────────────────────────


class ConsequenceResult(BaseModel):
    """Output of the ConsequenceEngine."""

    tier: ConsequenceTier
    reason: str
    factors: List[str]


class CheckResult(BaseModel):
    """Result of a single check (PII, injection, policy, etc.)."""

    name: str
    status: CheckStatus
    category: str = ""
    reason: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


class EvaluationResult(BaseModel):
    """Aggregated evaluation output."""

    depth: EvaluationDepth
    checks: List[CheckResult] = Field(default_factory=list)
    overall_status: CheckStatus = CheckStatus.PASS


class PerformanceResult(BaseModel):
    """Performance evaluation (groundedness, relevance, consistency)."""

    status: CheckStatus = CheckStatus.PASS
    reason: str = ""
    evidence: List[str] = Field(default_factory=list)
    groundedness: Optional[CheckStatus] = None
    relevance: Optional[CheckStatus] = None
    consistency: Optional[CheckStatus] = None


class CostResult(BaseModel):
    """Cost and efficiency telemetry for a request."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    latency_ms: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    retries: int = 0
    estimated_cost_usd: Optional[float] = None
    is_anomalous: bool = False
    anomaly_reasons: List[str] = Field(default_factory=list)


class ResponsibilityResult(BaseModel):
    """Responsibility evaluation (PII, injection, safety)."""

    status: CheckStatus = CheckStatus.PASS
    checks: List[CheckResult] = Field(default_factory=list)


class DecisionResult(BaseModel):
    """Final ControlPlane decision."""

    action: Decision
    reason: str
    requires_human: bool = False


class ExecutionRailResult(BaseModel):
    """Result from the execution rail for tool calls."""

    allowed: bool
    decision: Decision
    reason: str
    tool: str = ""
    modified_parameters: Optional[Dict[str, Any]] = None
    consequence_tier: Optional[ConsequenceTier] = None
    requires_human: bool = False
    request_id: Optional[str] = None


# ─────────────────────────────────────────────
# Full Pipeline Response
# ─────────────────────────────────────────────


class ControlResponse(BaseModel):
    """Complete ControlPlane response for a request."""

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    consequence: ConsequenceResult
    evaluation: EvaluationResult
    performance: PerformanceResult = Field(default_factory=PerformanceResult)
    cost: CostResult = Field(default_factory=CostResult)
    responsibility: ResponsibilityResult = Field(
        default_factory=ResponsibilityResult
    )
    decision: DecisionResult
    execution_rail: Optional[ExecutionRailResult] = None


# ─────────────────────────────────────────────
# Audit Models
# ─────────────────────────────────────────────


class AuditEntry(BaseModel):
    """Structured audit log entry for a ControlPlane decision."""

    request_id: str
    timestamp: str
    consequence_tier: ConsequenceTier
    consequence_factors: List[str]
    evaluation_depth: EvaluationDepth
    checks_executed: List[str]
    check_results: Dict[str, str]
    final_decision: Decision
    decision_reason: str
    execution_rail_decision: Optional[Decision] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
