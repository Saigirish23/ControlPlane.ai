"""
ControlPlane.AI — Audit Logging

Structured, privacy-aware audit logging for every ControlPlane decision.

Stores:
- request ID, timestamp
- consequence tier & factors
- evaluation depth & check results
- final decision & reason
- execution rail decision (if applicable)

Does NOT store raw prompts by default (privacy by design).
Provides an event interface for future async consumers (drift monitoring,
aggregate analysis, evaluator calibration).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from controlplane.models import (
    AuditEntry,
    ControlResponse,
    Decision,
    ExecutionRailResult,
)

logger = logging.getLogger(__name__)


class AuditEvent:
    """
    Event structure for the async audit layer.

    Can later support: audit logs, drift monitoring, aggregate bias analysis,
    evaluator calibration, historical cost analysis.
    """

    def __init__(self, event_type: str, data: Dict[str, Any]) -> None:
        self.event_type = event_type
        self.data = data
        self.timestamp = datetime.now(timezone.utc).isoformat()


# Type alias for event handlers
EventHandler = Callable[[AuditEvent], None]


class AuditLogger:
    """
    Audit logger that records every ControlPlane decision.

    Supports:
    - In-memory log (for prototype)
    - Structured JSON logging
    - Event emission for async consumers
    """

    def __init__(self) -> None:
        self._entries: List[AuditEntry] = []
        self._event_handlers: List[EventHandler] = []

    def register_handler(self, handler: EventHandler) -> None:
        """Register an async event consumer."""
        self._event_handlers.append(handler)

    def log_decision(
        self,
        response: ControlResponse,
        execution_rail: Optional[ExecutionRailResult] = None,
    ) -> AuditEntry:
        """Log a ControlPlane decision."""
        entry = AuditEntry(
            request_id=response.request_id,
            timestamp=response.timestamp,
            consequence_tier=response.consequence.tier,
            consequence_factors=response.consequence.factors,
            evaluation_depth=response.evaluation.depth,
            checks_executed=[
                c.name for c in response.evaluation.checks
            ],
            check_results={
                c.name: c.status.value
                for c in response.evaluation.checks
            },
            final_decision=response.decision.action,
            decision_reason=response.decision.reason,
            execution_rail_decision=(
                execution_rail.decision if execution_rail else None
            ),
        )

        self._entries.append(entry)

        # Structured logging
        logger.info(
            "AUDIT: request_id=%s tier=%s depth=%s decision=%s reason=%s",
            entry.request_id,
            entry.consequence_tier.value,
            entry.evaluation_depth.value,
            entry.final_decision.value,
            entry.decision_reason,
        )

        # Emit event for async consumers
        self._emit(
            AuditEvent(
                event_type="control_decision",
                data=entry.model_dump(),
            )
        )

    @property
    def entries(self) -> List[AuditEntry]:
        """Return all audit entries."""
        return self._entries

    def record_entry(self, entry: AuditEntry) -> AuditEntry:
        """Record an explicit AuditEntry directly."""
        self._entries.append(entry)
        logger.info(
            "AUDIT: request_id=%s tier=%s depth=%s decision=%s reason=%s",
            entry.request_id,
            entry.consequence_tier.value,
            entry.evaluation_depth.value,
            entry.final_decision.value,
            entry.decision_reason,
        )
        self._emit(
            AuditEvent(
                event_type="control_decision",
                data=entry.model_dump(),
            )
        )
        return entry

    def get_entries(self) -> List[AuditEntry]:
        """Return all audit entries."""
        return list(self._entries)

    def get_entries_json(self) -> str:
        """Return all audit entries as JSON."""
        return json.dumps(
            [e.model_dump() for e in self._entries], indent=2
        )

    def _emit(self, event: AuditEvent) -> None:
        """Emit an event to all registered handlers."""
        for handler in self._event_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error("Event handler error: %s", e)
