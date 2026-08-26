"""
ControlPlane.AI — Unified Runtime Demonstration

Executes the three mandatory scenarios plus a streaming guardrail violation demo:
1. Marketing Email Rewrite (LOW -> FAST -> PASS)
2. Refund Eligibility (MEDIUM -> DEEP -> VERIFY)
3. ₹8L Transfer (HIGH -> HIGH_ASSURANCE -> HUMAN_APPROVAL -> PREVENTED)
4. Streaming PII Violation Demo (Token Stream -> PII Detected -> MODIFY/BLOCK)

Run:
    python3 -m controlplane.demo_runtime
"""

import asyncio
from controlplane.models import (
    ActionType,
    ControlRequest,
    DataSensitivity,
    Domain,
    InteractionContext,
    ToolCallRequest,
    UserContext,
)
from controlplane.runtime import (
    UnifiedControlPlane,
    mock_model_stream,
    mock_pii_stream,
)


async def run_demo():
    runtime = UnifiedControlPlane()

    print("=" * 60)
    print("CONTROLPLANE RUNTIME DEMO")
    print("=" * 60)

    # ────────────────────────────────────────────────────────────
    # [1] MARKETING EMAIL
    # ────────────────────────────────────────────────────────────
    print("\n[1] MARKETING EMAIL")
    req1 = ControlRequest(
        request="Rewrite this marketing email to sound more professional.",
        user_context=UserContext(user_role="marketing_analyst", user_id="MKT-001"),
        interaction_context=InteractionContext(
            domain=Domain.GENERAL,
            action_type=ActionType.INFORMATIONAL,
            reversible=True,
            data_sensitivity=DataSensitivity.LOW,
        ),
    )
    res1 = await runtime.run(req1, model_stream=mock_model_stream)
    print(f"Consequence:      {res1.consequence.tier.value}")
    print(f"Depth:            {res1.depth.value}")
    print(f"Model:            {'EXECUTED' if res1.model_executed else 'BLOCKED'}")
    print(f"Stream Guardrail: {'PASS' if res1.stream_result and not res1.stream_result.has_violations else 'FLAGGED'}")
    print(f"Final Decision:   {res1.decision.action.value}")
    print(f"Reason:           {res1.decision.reason}")

    # ────────────────────────────────────────────────────────────
    # [2] REFUND DECISION
    # ────────────────────────────────────────────────────────────
    print("\n" + "-" * 60)
    print("\n[2] REFUND DECISION")
    req2 = ControlRequest(
        request="Determine whether this customer is eligible for a ₹50,000 refund.",
        user_context=UserContext(user_role="finance_operator", user_id="FIN-002"),
        interaction_context=InteractionContext(
            domain=Domain.FINANCE,
            action_type=ActionType.DECISION,
            reversible=True,
            data_sensitivity=DataSensitivity.MEDIUM,
        ),
    )
    res2 = await runtime.run(req2, model_stream=mock_model_stream)
    print(f"Consequence:      {res2.consequence.tier.value}")
    print(f"Depth:            {res2.depth.value}")
    print(f"Model:            {'EXECUTED' if res2.model_executed else 'BLOCKED'}")
    print(f"Deep Evaluation:  {res2.responsibility.status.value}")
    print(f"Final Decision:   {res2.decision.action.value}")
    print(f"Reason:           {res2.decision.reason}")

    # ────────────────────────────────────────────────────────────
    # [3] ₹8,00,000 TRANSFER (AGENT LIFECYCLE FLOW)
    # ────────────────────────────────────────────────────────────
    print("\n" + "-" * 60)
    print("\n[3] ₹8,00,000 TRANSFER (FULL AGENT LIFECYCLE FLOW)")
    user_req3 = ControlRequest(
        request="Process high-value vendor settlement of ₹8,00,000.",
        user_context=UserContext(user_role="finance_operator", user_id="FIN-001"),
        interaction_context=InteractionContext(
            domain=Domain.FINANCE,
            action_type=ActionType.EXTERNAL_ACTION,
            reversible=False,
            data_sensitivity=DataSensitivity.HIGH,
        ),
    )

    def agent_generator(prompt: str) -> ToolCallRequest:
        return ToolCallRequest(
            tool="transfer_money",
            parameters={
                "amount": 800000,
                "currency": "INR",
                "beneficiary": "new_beneficiary_987",
            },
            user_context=UserContext(user_role="finance_operator", user_id="FIN-001"),
        )

    res3 = await runtime.run_agent_flow(user_req3, agent_generator)
    print(f"User Request:         {res3['user_request']}")
    print(f"Initial Consequence:  {res3['initial_consequence_tier']}")
    print(f"AI Generated Tool:    {res3['tool_generated']}({res3['tool_parameters']})")
    print(f"Execution Rail:       TRIGGERED ({res3['rail_decision']})")
    print(f"Requires Human:       {res3['requires_human']}")
    print(f"External Execution:   {'PREVENTED' if not res3['external_executed'] else 'ALLOWED'}")
    print(f"Reason:               {res3['reason']}")

    # ────────────────────────────────────────────────────────────
    # [4] STREAMING PII VIOLATION DEMO (HOLDING / RELEASE BUFFER)
    # ────────────────────────────────────────────────────────────
    print("\n" + "-" * 60)
    print("\n[4] STREAMING PII VIOLATION DEMO (HOLDING / RELEASE BUFFER)")
    req4 = ControlRequest(
        request="Lookup refund account details.",
        user_context=UserContext(user_role="support_agent", user_id="SUP-001"),
        interaction_context=InteractionContext(
            domain=Domain.GENERAL,
            action_type=ActionType.INFORMATIONAL,
            reversible=True,
        ),
    )
    res4 = await runtime.run(req4, model_stream=mock_pii_stream)
    print(f"Consequence:        {res4.consequence.tier.value}")
    print(f"Model:              {'EXECUTED' if res4.model_executed else 'BLOCKED'}")
    print(f"Stream Guardrail:   {'VIOLATION DETECTED' if res4.stream_result and res4.stream_result.has_violations else 'PASS'}")
    print(f"Chunks Flagged:     {res4.stream_result.chunks_flagged if res4.stream_result else 0}")
    print(f"Delivered Text:     {res4.response_text[:80]}...")
    print(f"Final Decision:     {res4.decision.action.value}")
    print(f"Reason:             {res4.decision.reason}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_demo())
