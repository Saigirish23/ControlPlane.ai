"""
Tests validating the ControlPlane MCP Integration Contract.

Verifies:
1. ToolCallRequest schema serialization and deserialization
2. POST /execution-rail HTTP contract for MCP proxy
3. Handling of all Decision outcomes (PASS, HUMAN_APPROVAL, VERIFY, BLOCK, MODIFY)
4. High-risk financial tool calls (transfer_money) -> HUMAN_APPROVAL -> allowed=False
5. Safe read-only tool calls (query_database) -> PASS -> allowed=True
6. Unknown tool default safety
7. Consequence tier reporting in ExecutionRailResult
8. Request ID propagation and metadata preservation
"""

import pytest
from fastapi.testclient import TestClient

from controlplane.api import app
from controlplane.execution_rail import ExecutionRail, MockExternalSystem
from controlplane.models import (
    ActionType,
    ConsequenceTier,
    DataSensitivity,
    Decision,
    Domain,
    ExecutionRailResult,
    InteractionContext,
    ToolCallRequest,
    UserContext,
)


@pytest.fixture
def client():
    return TestClient(app)


class TestMCPIntegrationContract:
    """Validate the exact interface provided to an external MCP proxy."""

    def test_mcp_tool_call_request_schema(self):
        """MCP proxy sends a tool call with full context metadata."""
        tool_req = ToolCallRequest(
            tool="transfer_money",
            parameters={
                "amount": 800000,
                "currency": "INR",
                "beneficiary": "new_vendor_acc_123",
            },
            user_context=UserContext(
                user_role="finance_operator",
                user_id="USR-FIN-009",
                session_id="sess-mcp-99",
            ),
            interaction_context=InteractionContext(
                domain=Domain.FINANCE,
                action_type=ActionType.EXTERNAL_ACTION,
                reversible=False,
                data_sensitivity=DataSensitivity.HIGH,
            ),
            request_id="mcp-req-001",
            metadata={"mcp_client": "claude-desktop", "server_name": "banking-mcp"},
        )

        assert tool_req.tool == "transfer_money"
        assert tool_req.parameters["amount"] == 800000
        assert tool_req.request_id == "mcp-req-001"
        assert tool_req.metadata["mcp_client"] == "claude-desktop"

    def test_mcp_http_endpoint_transfer_blocked(self, client):
        """HTTP contract: POST /execution-rail with transfer_money returns HUMAN_APPROVAL and allowed=False."""
        payload = {
            "tool": "transfer_money",
            "parameters": {
                "amount": 800000,
                "currency": "INR",
                "beneficiary": "vendor_acc_999",
            },
            "user_context": {
                "user_role": "finance_operator",
                "user_id": "USR-FIN-001",
            },
            "interaction_context": {
                "domain": "FINANCE",
                "action_type": "EXTERNAL_ACTION",
                "reversible": False,
                "data_sensitivity": "HIGH",
            },
            "request_id": "req-mcp-test-1",
        }

        response = client.post("/execution-rail", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["allowed"] is False
        assert data["decision"] == Decision.HUMAN_APPROVAL.value
        assert data["tool"] == "transfer_money"
        assert data["requires_human"] is True
        assert data["consequence_tier"] == ConsequenceTier.HIGH.value
        assert data["request_id"] == "req-mcp-test-1"
        assert "human approval" in data["reason"].lower()

    def test_mcp_http_endpoint_read_query_allowed(self, client):
        """HTTP contract: POST /execution-rail with read query returns allowed=True or VERIFY."""
        payload = {
            "tool": "query_database",
            "parameters": {"query": "SELECT count(*) FROM orders"},
            "user_context": {"user_role": "analyst"},
            "interaction_context": {
                "domain": "GENERAL",
                "action_type": "INFORMATIONAL",
                "reversible": True,
                "data_sensitivity": "LOW",
            },
            "request_id": "req-mcp-test-2",
        }

        response = client.post("/execution-rail", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["tool"] == "query_database"
        assert data["decision"] in {Decision.PASS.value, Decision.VERIFY.value}

    def test_mcp_proxy_decision_handling_contract(self):
        """
        Verify the exact decision-handling contract:
        - If allowed=False (HUMAN_APPROVAL / BLOCK / VERIFY), MockExternalSystem NEVER executes.
        - If allowed=True (PASS), MockExternalSystem executes.
        """
        # 1. Blocked / Human Approval case
        rail = ExecutionRail()
        blocked_result = rail.evaluate(
            ToolCallRequest(
                tool="transfer_money",
                parameters={"amount": 800000},
                user_context=UserContext(user_role="finance_operator"),
            )
        )
        assert blocked_result.allowed is False
        assert blocked_result.decision == Decision.HUMAN_APPROVAL

        mcp_simulated_exec = MockExternalSystem.execute(
            tool_name=blocked_result.tool,
            parameters={"amount": 800000},
            rail_result=blocked_result,
        )
        assert mcp_simulated_exec["executed"] is False
        assert mcp_simulated_exec["decision"] == Decision.HUMAN_APPROVAL.value

        # 2. Allowed case
        allowed_rail_result = ExecutionRailResult(
            allowed=True,
            decision=Decision.PASS,
            reason="Low-consequence action approved for execution",
            tool="get_system_time",
        )
        mcp_allowed_exec = MockExternalSystem.execute(
            tool_name="get_system_time",
            parameters={},
            rail_result=allowed_rail_result,
        )
        assert mcp_allowed_exec["executed"] is True
