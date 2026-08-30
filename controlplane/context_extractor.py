"""
ControlPlane.AI — Context Extractor

Receives structured context from the caller, validates, and normalizes it
into a RequestContext used by downstream pipeline stages.

For the MVP, context is provided directly by the caller. The architecture
allows future auto-inference of fields from the raw request text without
requiring changes to downstream components.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from controlplane.models import (
    ActionType,
    ControlRequest,
    DataSensitivity,
    Domain,
    InteractionContext,
    UserContext,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestContext:
    """Normalized, immutable context for a single ControlPlane evaluation."""

    request_text: str
    user_role: str
    user_id: Optional[str]
    domain: Domain
    action_type: ActionType
    reversible: bool
    data_sensitivity: DataSensitivity
    session_id: Optional[str] = None
    tool_name: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_sensitive_domain(self) -> bool:
        """Return True if the domain is a high-stakes enterprise domain."""
        return self.domain in {
            Domain.FINANCE,
            Domain.HEALTHCARE,
            Domain.LEGAL,
            Domain.SECURITY,
            Domain.INFRASTRUCTURE,
        }

    @property
    def is_external_action(self) -> bool:
        return self.action_type == ActionType.EXTERNAL_ACTION

    @property
    def is_decision(self) -> bool:
        return self.action_type == ActionType.DECISION

    @property
    def is_informational(self) -> bool:
        return self.action_type == ActionType.INFORMATIONAL


class ContextExtractor:
    """
    Extracts and normalizes context from a ControlRequest.

    Designed as a class to allow future extension with auto-inference
    capabilities (e.g., domain detection from request text) without
    changing the public interface.
    """

    def extract(self, request: ControlRequest) -> RequestContext:
        """Extract a normalized RequestContext from a ControlRequest."""
        ic: InteractionContext = request.interaction_context
        uc: UserContext = request.user_context

        tool_name = request.metadata.get("tool_name")
        parameters = request.metadata.get("parameters", {})

        ctx = RequestContext(
            request_text=request.request,
            user_role=uc.user_role,
            user_id=uc.user_id,
            domain=ic.domain,
            action_type=ic.action_type,
            reversible=ic.reversible,
            data_sensitivity=ic.data_sensitivity,
            session_id=uc.session_id,
            tool_name=tool_name,
            parameters=parameters,
            metadata=request.metadata,
        )

        logger.debug(
            "Extracted context: domain=%s action_type=%s reversible=%s",
            ctx.domain.value,
            ctx.action_type.value,
            ctx.reversible,
        )
        return ctx
