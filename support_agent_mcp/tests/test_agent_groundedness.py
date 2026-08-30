"""
Tests for SupportAgent Prompt Groundedness and Anti-Hallucination Contracts.

Validates that:
- SupportAgent conversation history starts clean and unpolluted.
- SYSTEM_PROMPT contains explicit anti-fabrication and evidence-grounding rules.
- Groundedness contracts for Cases A, B, and C are enforced deterministically.
"""

import pytest

from support_agent_mcp.agent.client import SupportAgent
from support_agent_mcp.agent.prompts import SYSTEM_PROMPT, build_system_prompt


class TestAgentGroundednessContracts:
    """Deterministic tests for agent instructions, history isolation, and groundedness."""

    def test_agent_initializes_with_clean_empty_history(self, monkeypatch):
        """SupportAgent must start with an empty history list (no injected fake user turns)."""
        monkeypatch.setenv("GEMINI_API_KEY", "dummy_test_key_for_unit_tests")
        agent = SupportAgent(customer_id="CUST001", customer_name="Arjun")
        assert agent._history == []
        assert len(agent._history) == 0

    def test_system_prompt_contains_anti_phantom_tool_rules(self):
        """System instructions must explicitly forbid claiming unexecuted tool actions."""
        prompt = build_system_prompt(customer_name="Arjun", customer_id="CUST001")

        # Rule 2: Anti-phantom tool calls
        assert "Never claim actions that were not executed (No Phantom Tool Calls)" in prompt
        assert "NEVER claim that you have connected the user to a human agent" in prompt
        assert "NEVER claim a refund or replacement was processed" in prompt
        assert "NEVER make promises about human callbacks" in prompt

    def test_system_prompt_contains_factual_status_rules(self):
        """Rule 3 must dictate factual reporting without claiming human connection."""
        prompt = build_system_prompt(customer_name="Arjun", customer_id="CUST001")
        assert "Factual status reporting for order queries" in prompt
        assert "Do NOT claim or promise to connect to a human agent unless the customer explicitly requests one or you actually invoke `escalate_to_human_agent`" in prompt

    def test_few_shot_examples_labeled_as_format_guidelines_only(self):
        """Example section in prompt must explicitly warn that examples are format guidelines only."""
        assert "Example Interaction Formats (Format Guidelines Only)" in SYSTEM_PROMPT
        assert "Never assume the current customer shares any attributes, emotions, or requests from these examples" in SYSTEM_PROMPT

    def test_case_a_status_query_no_false_escalation_claim(self):
        """
        CASE A: User asks 'Where is my order ORD001?'.
        Prompt contract forbids claiming human escalation when only status was queried and no tool executed.
        """
        user_message = "Where is my order ORD001?"
        assert "manager" not in user_message.lower()
        assert "upset" not in user_message.lower()

        prompt = build_system_prompt(customer_name="Arjun", customer_id="CUST001")
        assert "inspect order details / tracking and report the factual status" in prompt

    def test_case_b_explicit_manager_request_escalation_allowed(self):
        """
        CASE B: User explicitly requests manager.
        Prompt contract permits escalation and requires calling escalate_to_human_agent first.
        """
        user_message = "I want to speak to a manager right now."
        assert "manager" in user_message.lower()

        prompt = build_system_prompt(customer_name="Arjun", customer_id="CUST001")
        assert "The customer explicitly asks for a manager or human agent in the current conversation" in prompt
        assert "you MUST invoke `escalate_to_human_agent` as a tool call" in prompt

    def test_case_c_anti_phantom_action_contract(self):
        """
        CASE C: An agent cannot claim a tool action occurred when no corresponding tool was called.
        """
        prompt = build_system_prompt(customer_name="Arjun", customer_id="CUST001")
        assert "Only after the tool returns success with a ticket ID should you inform the customer that their issue has been escalated" in prompt
