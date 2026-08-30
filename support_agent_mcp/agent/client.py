"""
agent/client.py — Gemini-powered food delivery customer support agent.

Flow per user turn:
  1. Append user message to conversation history
  2. Call Gemini with system prompt + history + tool declarations
  3. If Gemini returns a function_call → route through ProxyPipeline → send result back
  4. Repeat until Gemini returns a plain text response
  5. Return final text to CLI / caller

The agent is stateful (maintains history) and proxy-aware (all tool calls
go through the pipeline, never directly to the MCP server functions).
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

import google.genai as genai
import google.genai.types as gtypes

from support_agent_mcp.config import GEMINI_API_KEY, GEMINI_MODEL
from support_agent_mcp.agent.prompts import FEW_SHOT_EXAMPLES, build_system_prompt
from support_agent_mcp.proxy.base_proxy import ProxyPipeline
from support_agent_mcp.proxy.controlplane_hooks import build_default_pipeline

# ── Import all tool functions ────────────────────────────────────────────────
from support_agent_mcp.server import (
    check_refund_status,
    escalate_to_human_agent,
    get_order_details,
    get_order_history,
    list_order_complaints,
    request_refund_or_replacement,
    track_delivery_partner,
    update_delivery_instructions,
)


# ── Tool registry ─────────────────────────────────────────────────────────────
# Maps tool name → callable. The proxy wraps each call.

TOOL_REGISTRY: Dict[str, Callable] = {
    "get_order_details":             get_order_details,
    "track_delivery_partner":        track_delivery_partner,
    "request_refund_or_replacement": request_refund_or_replacement,
    "escalate_to_human_agent":       escalate_to_human_agent,
    "get_order_history":             get_order_history,
    "check_refund_status":           check_refund_status,
    "update_delivery_instructions":  update_delivery_instructions,
    "list_order_complaints":         list_order_complaints,
}


# ── Tool declarations for Gemini ──────────────────────────────────────────────
# These must match the MCP tool signatures exactly.

_TOOL_DECLARATIONS = gtypes.Tool(
    function_declarations=[
        gtypes.FunctionDeclaration(
            name="get_order_details",
            description=(
                "Retrieve complete details for a customer's order including status, "
                "items, ETA countdown, payment info, and delivery address."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order ID, e.g. ORD001"},
                },
                "required": ["order_id"],
            },
        ),
        gtypes.FunctionDeclaration(
            name="track_delivery_partner",
            description=(
                "Track the live location, ETA, and contact details of the delivery "
                "partner assigned to an order. Shows late/on-time status."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order ID"},
                },
                "required": ["order_id"],
            },
        ),
        gtypes.FunctionDeclaration(
            name="request_refund_or_replacement",
            description=(
                "Submit a refund or replacement request for a delivered or cancelled order. "
                "Use for missing items, wrong orders, food quality issues, or damaged packaging. "
                "Small refunds may be auto-approved; large ones require human review."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id":         {"type": "string", "description": "The order ID"},
                    "customer_id":      {"type": "string", "description": "The customer ID"},
                    "reason":           {"type": "string", "description": "Why the refund/replacement is needed"},
                    "complaint_type":   {
                        "type": "string",
                        "enum": ["late_delivery", "wrong_order", "missing_items",
                                 "food_quality", "damaged_packaging", "payment_issue", "other"],
                        "description": "Category of the complaint",
                    },
                    "item_ids":         {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific item IDs to refund. Empty list = full order refund.",
                    },
                    "requested_amount": {
                        "type": "number",
                        "description": "Amount to refund in INR. Omit to auto-calculate from items.",
                    },
                },
                "required": ["order_id", "customer_id", "reason", "complaint_type"],
            },
        ),
        gtypes.FunctionDeclaration(
            name="escalate_to_human_agent",
            description=(
                "Escalate the customer's issue to a human support agent or helpline. "
                "Use when customer is very upset, asks for a manager, or the issue "
                "involves safety concerns that cannot be resolved automatically."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id":     {"type": "string", "description": "Related order ID"},
                    "customer_id":  {"type": "string", "description": "Customer ID"},
                    "reason":       {"type": "string", "description": "Clear reason for escalation"},
                    "urgency":      {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                        "description": "Urgency level",
                    },
                    "complaint_id": {
                        "type": "string",
                        "description": "Optional existing complaint ID to link",
                    },
                },
                "required": ["order_id", "customer_id", "reason", "urgency"],
            },
        ),
        gtypes.FunctionDeclaration(
            name="get_order_history",
            description=(
                "Retrieve all past orders for a customer, most recent first. "
                "Use when the customer doesn't know their order ID or asks about recent orders."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string", "description": "The customer's ID"},
                },
                "required": ["customer_id"],
            },
        ),
        gtypes.FunctionDeclaration(
            name="check_refund_status",
            description=(
                "Check the current status of all refund requests for an order. "
                "Use when customer asks about a previously submitted refund."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order ID"},
                },
                "required": ["order_id"],
            },
        ),
        gtypes.FunctionDeclaration(
            name="update_delivery_instructions",
            description=(
                "Update the special delivery instructions for an active order. "
                "Use when customer wants to change drop-off notes, add gate codes, etc. "
                "Only works on orders not yet delivered or cancelled."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id":         {"type": "string", "description": "The order ID"},
                    "new_instructions": {"type": "string", "description": "The new delivery instructions"},
                },
                "required": ["order_id", "new_instructions"],
            },
        ),
        gtypes.FunctionDeclaration(
            name="list_order_complaints",
            description=(
                "List all complaints already filed for a given order. "
                "Use before filing a new complaint to avoid duplicates, or when "
                "customer asks about the status of a complaint they filed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order ID"},
                },
                "required": ["order_id"],
            },
        ),
    ]
)


# ── Support Agent ──────────────────────────────────────────────────────────────

class SupportAgent:
    """
    Stateful Gemini customer support agent with proxy-mediated tool calls.

    Usage:
        agent = SupportAgent(customer_id="CUST001", customer_name="Arjun")
        response = agent.chat("Where is my order ORD001?")
        print(response)
    """

    def __init__(
        self,
        customer_id:   Optional[str] = None,
        customer_name: Optional[str] = None,
        pipeline:      Optional[ProxyPipeline] = None,
        model:         Optional[str] = None,
        api_key:       Optional[str] = None,
        verbose_tools: bool = True,
    ):
        _key = api_key or GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
        if not _key:
            raise ValueError(
                "GEMINI_API_KEY is not set. "
                "Export it as an environment variable or pass api_key= to SupportAgent()."
            )

        self.client = genai.Client(api_key=_key)
        self.model  = model or GEMINI_MODEL

        self.customer_id   = customer_id
        self.customer_name = customer_name
        self.pipeline      = pipeline or build_default_pipeline(verbose_logging=verbose_tools)

        self._system_prompt = build_system_prompt(
            customer_name=customer_name,
            customer_id=customer_id,
        )

        # Conversation history — list of gtypes.Content (starts clean for active session)
        self._history: List[gtypes.Content] = []

        # Track tool calls made in the current session
        self.tool_call_log: List[Dict[str, Any]] = []

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Route a Gemini function call through the proxy pipeline."""
        fn = TOOL_REGISTRY.get(name)
        if fn is None:
            return {"success": False, "error": f"Unknown tool: '{name}'"}
        result = self.pipeline.call(fn, args)
        self.tool_call_log.append({"tool": name, "args": args, "result": result})
        return result

    def _build_config(self) -> gtypes.GenerateContentConfig:
        return gtypes.GenerateContentConfig(
            system_instruction=self._system_prompt,
            tools=[_TOOL_DECLARATIONS],
            temperature=0.3,            # Low temp for factual support responses
            max_output_tokens=512,
            automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(
                disable=True            # We handle tool calls manually via proxy
            ),
        )

    # ── Main chat method ──────────────────────────────────────────────────────

    def chat(self, user_message: str) -> str:
        """
        Send a message to the agent and get a response.
        Handles multi-turn tool calls automatically until Gemini returns plain text.

        Args:
            user_message: The customer's message.

        Returns:
            The agent's final text response.
        """
        # Pre-inference safety check using ControlPlane ResponsibilityEvaluator
        from controlplane.responsibility import ResponsibilityEvaluator
        from controlplane.models import CheckStatus
        
        resp_eval = ResponsibilityEvaluator()
        pre_check = resp_eval.evaluate(user_message)
        hard_fails = [
            c for c in pre_check.checks
            if c.status == CheckStatus.FAIL and c.category in {"INJECTION", "SAFETY"}
        ]
        if hard_fails:
            return (
                "I cannot process this request because it violates safety and security policy: "
                f"{hard_fails[0].reason}"
            )

        # Append user turn
        self._history.append(
            gtypes.Content(role="user", parts=[gtypes.Part(text=user_message)])
        )

        MAX_TOOL_ROUNDS = 5   # Safety cap to prevent infinite loops
        rounds = 0

        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1

            # Generate content with retry on rate limits
            max_retries = 3
            response = None
            for attempt in range(max_retries):
                try:
                    response = self.client.models.generate_content(
                        model=self.model,
                        contents=self._history,
                        config=self._build_config(),
                    )
                    break
                except Exception as e:
                    if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and attempt < max_retries - 1:
                        import time
                        time.sleep(6 * (attempt + 1))
                        continue
                    raise

            candidate = response.candidates[0]
            content   = candidate.content

            # Append Gemini's response turn to history
            self._history.append(content)

            # Collect all function calls from this response
            fn_calls = [
                part.function_call
                for part in content.parts
                if part.function_call is not None
            ]

            if not fn_calls:
                # Plain text response — we're done
                text_parts = [p.text for p in content.parts if p.text]
                return " ".join(text_parts).strip()

            # Execute each tool call through the proxy and collect results
            result_parts: List[gtypes.Part] = []
            for fc in fn_calls:
                args = dict(fc.args) if fc.args else {}
                tool_result = self._execute_tool(fc.name, args)

                result_parts.append(
                    gtypes.Part.from_function_response(
                        name=fc.name,
                        response={"result": tool_result},
                    )
                )

            # Append tool results as a user turn per Gemini API specification
            self._history.append(
                gtypes.Content(role="user", parts=result_parts)
            )

        # Fallback if we hit max rounds
        return (
            "I'm having some trouble completing your request right now. "
            "Please call our helpline for immediate assistance."
        )

    def reset(self) -> None:
        """Clear conversation history and tool call log for a new session."""
        self._history = []
        self.tool_call_log = []

    def get_tool_summary(self) -> List[Dict[str, Any]]:
        """Return a summary of all tool calls made this session."""
        return [
            {"tool": t["tool"], "success": t["result"].get("success", True)}
            for t in self.tool_call_log
        ]
