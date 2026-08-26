"""Tests for ExecutionRail."""

import pytest

from controlplane.execution_rail import ExecutionRail, MockExternalSystem
from controlplane.models import (
    Decision,
    Domain,
    InteractionContext,
    ToolCallRequest,
    UserContext,
)


class TestExecutionRail:
    def setup_method(self):
        self.rail = ExecutionRail()

    def test_transfer_money_blocked(self):
        """Financial transfer must NOT be allowed directly."""
        result = self.rail.evaluate(
            ToolCallRequest(
                tool="transfer_money",
                parameters={
                    "amount": 800000,
                    "currency": "INR",
                    "beneficiary": "new_beneficiary",
                },
                user_context=UserContext(user_role="finance_operator"),
            )
        )
        assert not result.allowed
        assert result.decision in {Decision.HUMAN_APPROVAL, Decision.BLOCK}
        assert result.tool == "transfer_money"

    def test_delete_database_blocked(self):
        """Infrastructure deletion must not be allowed."""
        result = self.rail.evaluate(
            ToolCallRequest(
                tool="delete_database",
                parameters={"database": "production"},
                user_context=UserContext(user_role="admin"),
            )
        )
        assert not result.allowed
        assert result.decision in {Decision.HUMAN_APPROVAL, Decision.BLOCK}

    def test_query_database_allowed(self):
        """Safe read-only query should be allowed."""
        result = self.rail.evaluate(
            ToolCallRequest(
                tool="query_database",
                parameters={"query": "SELECT * FROM users LIMIT 10"},
                user_context=UserContext(user_role="analyst"),
            )
        )
        # query_database: general domain, reversible → MEDIUM (reversible external)
        # Decision: VERIFY or PASS
        assert result.decision in {Decision.PASS, Decision.VERIFY}

    def test_unknown_tool_defaults(self):
        """Unknown tool should use the context from the request."""
        result = self.rail.evaluate(
            ToolCallRequest(
                tool="unknown_tool",
                parameters={},
                user_context=UserContext(user_role="user"),
                interaction_context=InteractionContext(
                    domain=Domain.GENERAL, reversible=True
                ),
            )
        )
        # Uses defaults: GENERAL + EXTERNAL_ACTION + reversible → MEDIUM
        assert result.decision in {Decision.VERIFY, Decision.PASS}

    def test_result_has_reason(self):
        result = self.rail.evaluate(
            ToolCallRequest(
                tool="transfer_money",
                parameters={"amount": 100},
                user_context=UserContext(user_role="user"),
            )
        )
        assert result.reason
        assert isinstance(result.reason, str)


class TestMockExternalSystem:
    def test_blocked_tool_not_executed(self):
        """Blocked tool calls must NOT execute."""
        from controlplane.models import ExecutionRailResult

        rail_result = ExecutionRailResult(
            allowed=False,
            decision=Decision.HUMAN_APPROVAL,
            reason="Requires human approval",
            tool="transfer_money",
        )
        result = MockExternalSystem.execute(
            "transfer_money",
            {"amount": 800000},
            rail_result,
        )
        assert result["executed"] is False

    def test_allowed_tool_executes(self):
        from controlplane.models import ExecutionRailResult

        rail_result = ExecutionRailResult(
            allowed=True,
            decision=Decision.PASS,
            reason="Approved",
            tool="query_database",
        )
        result = MockExternalSystem.execute(
            "query_database",
            {"query": "SELECT 1"},
            rail_result,
        )
        assert result["executed"] is True
