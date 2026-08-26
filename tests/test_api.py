"""
Tests for FastAPI API endpoints:
- POST /control
- POST /execution-rail
- GET  /health
"""

import pytest
from fastapi.testclient import TestClient

from controlplane.api import app
from controlplane.models import ConsequenceTier, Decision, EvaluationDepth


@pytest.fixture
def client():
    return TestClient(app)


class TestControlPlaneAPI:
    """Validate HTTP API contract and responses."""

    def test_health_endpoint(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "service": "controlplane.ai"}

    def test_post_control_case1_marketing_email(self, client):
        """Marketing email: LOW -> FAST -> PASS."""
        payload = {
            "request": "Rewrite this marketing email to sound more professional.",
            "user_context": {"user_role": "marketing_user", "user_id": "MKT-01"},
            "interaction_context": {
                "domain": "GENERAL",
                "action_type": "INFORMATIONAL",
                "reversible": True,
                "data_sensitivity": "LOW",
            },
            "metadata": {"model": "gpt-4o-mini", "input_tokens": 120, "output_tokens": 80},
        }
        response = client.post("/control", json=payload)
        assert response.status_code == 200
        data = response.json()

        # Check structure
        assert "request_id" in data
        assert "timestamp" in data
        assert "consequence" in data
        assert "evaluation" in data
        assert "performance" in data
        assert "cost" in data
        assert "responsibility" in data
        assert "decision" in data

        # Check values
        assert data["consequence"]["tier"] == ConsequenceTier.LOW.value
        assert data["evaluation"]["depth"] == EvaluationDepth.FAST.value
        assert data["decision"]["action"] == Decision.PASS.value
        assert data["decision"]["reason"]

    def test_post_control_case2_refund(self, client):
        """Refund: MEDIUM -> DEEP -> VERIFY."""
        payload = {
            "request": "Determine whether this customer is eligible for a ₹50,000 refund.",
            "user_context": {"user_role": "finance_operator", "user_id": "FIN-002"},
            "interaction_context": {
                "domain": "FINANCE",
                "action_type": "DECISION",
                "reversible": True,
                "data_sensitivity": "MEDIUM",
            },
        }
        response = client.post("/control", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["consequence"]["tier"] == ConsequenceTier.MEDIUM.value
        assert data["evaluation"]["depth"] == EvaluationDepth.DEEP.value
        assert data["decision"]["action"] == Decision.VERIFY.value
        assert data["decision"]["reason"]
        assert len(data["consequence"]["factors"]) > 0

    def test_post_control_case3_transfer(self, client):
        """₹8L transfer: HIGH -> HIGH_ASSURANCE -> HUMAN_APPROVAL."""
        payload = {
            "request": "Transfer ₹8,00,000 to this new beneficiary.",
            "user_context": {"user_role": "finance_operator", "user_id": "FIN-001"},
            "interaction_context": {
                "domain": "FINANCE",
                "action_type": "EXTERNAL_ACTION",
                "reversible": False,
                "data_sensitivity": "HIGH",
            },
        }
        response = client.post("/control", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["consequence"]["tier"] == ConsequenceTier.HIGH.value
        assert data["evaluation"]["depth"] == EvaluationDepth.HIGH_ASSURANCE.value
        assert data["decision"]["action"] in {
            Decision.HUMAN_APPROVAL.value,
            Decision.BLOCK.value,
        }
        assert data["decision"]["requires_human"] is True

    def test_post_execution_rail_transfer_blocked(self, client):
        """₹8L transfer tool call via POST /execution-rail is intercepted & blocked."""
        payload = {
            "tool": "transfer_money",
            "parameters": {
                "amount": 800000,
                "currency": "INR",
                "beneficiary": "new_beneficiary",
            },
            "user_context": {"user_role": "finance_operator", "user_id": "FIN-001"},
            "interaction_context": {
                "domain": "FINANCE",
                "action_type": "EXTERNAL_ACTION",
                "reversible": False,
                "data_sensitivity": "HIGH",
            },
        }
        response = client.post("/execution-rail", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["allowed"] is False
        assert data["decision"] in {
            Decision.HUMAN_APPROVAL.value,
            Decision.BLOCK.value,
        }
        assert data["tool"] == "transfer_money"
        assert "reason" in data and len(data["reason"]) > 0
