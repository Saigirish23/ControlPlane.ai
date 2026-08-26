# ControlPlane MCP Integration Guide

This guide documents how an external Model Context Protocol (MCP) proxy or gateway should integrate with **ControlPlane.ai** to enforce consequence-aware governance on AI tool calls.

---

## 1. What ControlPlane Does

ControlPlane is a **consequence-aware runtime governance engine** for enterprise AI systems.

Instead of applying uniform, static policy to every tool invocation, ControlPlane:
1. Calculates the **consequence tier** (`LOW`, `MEDIUM`, `HIGH`) of a requested action based on business domain, reversibility, and data sensitivity.
2. Selects an **adaptive evaluation depth** (`FAST`, `DEEP`, `HIGH_ASSURANCE`).
3. Intercepts the tool call via the **Execution Rail**.
4. Produces a structured, explainable decision: `PASS`, `MODIFY`, `VERIFY`, `BLOCK`, or `HUMAN_APPROVAL`.
5. Ensures high-consequence irreversible external actions (e.g. ₹8,00,000 bank transfer) are **never executed directly** without human approval.

---

## 2. Architecture

```
                               AI CLIENT / AGENT
                                      │
                                      ▼
                             AI GENERATES TOOL CALL
                          transfer_money(amount=800000)
                                      │
                                      ▼
                             ┌─────────────────┐
                             │    MCP PROXY    │
                             └────────┬────────┘
                                      │  POST /execution-rail
                                      ▼
                         ┌───────────────────────────┐
                         │      CONTROLPLANE.AI      │
                         │                           │
                         │ • Consequence Engine      │
                         │ • Execution Rail          │
                         │ • Policy & Safety Checks  │
                         └────────────┬──────────────┘
                                      │
                                      ▼
                         ExecutionRailResult Response
                     { allowed: false, decision: HUMAN_APPROVAL }
                                      │
                                      ▼
                             ┌─────────────────┐
                             │    MCP PROXY    │
                             └────────┬────────┘
                                      │
                 ┌────────────────────┴────────────────────┐
                 │                                         │
        [allowed == true]                         [allowed == false]
                 │                                         │
                 ▼                                         ▼
         ┌───────────────┐                       ┌───────────────────┐
         │ Invoke Target │                       │ Pause Execution / │
         │  MCP Server   │                       │ Request Approval  │
         └───────────────┘                       └───────────────────┘
```

---

## 3. ControlPlane API Contract

### Endpoint: `POST /execution-rail`

Intercepts and evaluates an AI-generated tool call before execution.

#### Request Schema (`ToolCallRequest`)

```json
{
  "tool": "transfer_money",
  "parameters": {
    "amount": 800000,
    "currency": "INR",
    "beneficiary": "vendor_account_987654"
  },
  "user_context": {
    "user_role": "finance_operator",
    "user_id": "USR-FIN-001",
    "session_id": "sess-mcp-456"
  },
  "interaction_context": {
    "domain": "FINANCE",
    "action_type": "EXTERNAL_ACTION",
    "reversible": false,
    "data_sensitivity": "HIGH"
  },
  "request_id": "req-mcp-trace-001",
  "metadata": {
    "mcp_server": "banking-tools",
    "client_id": "agent-orchestrator"
  }
}
```

#### Field Specifications

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `tool` | `string` | **Yes** | Exact name of the MCP tool being called. |
| `parameters` | `object` | No (default: `{}`) | Key-value arguments passed to the tool. |
| `user_context` | `object` | No | Metadata about the initiating user or agent (`user_role`, `user_id`, `session_id`). |
| `interaction_context` | `object` | No | Explicit consequence signals (`domain`, `action_type`, `reversible`, `data_sensitivity`). |
| `request_id` | `string` | No | Idempotency or trace ID provided by MCP proxy. |
| `metadata` | `object` | No | Free-form telemetry or proxy headers. |

#### Response Schema (`ExecutionRailResult`)

```json
{
  "allowed": false,
  "decision": "HUMAN_APPROVAL",
  "reason": "High-consequence irreversible finance action requires human approval",
  "tool": "transfer_money",
  "modified_parameters": null,
  "consequence_tier": "HIGH",
  "requires_human": true,
  "request_id": "req-mcp-trace-001"
}
```

---

## 4. Decision Semantics & MCP Proxy Behavior

The MCP proxy **must strictly adhere** to the decision contract:

| Decision | Meaning | MCP Proxy Required Behavior |
| :--- | :--- | :--- |
| **`PASS`** | Action is low-consequence and approved. | **Invoke Target Tool**: Forward call to MCP server and return result to agent. |
| **`MODIFY`** | Parameters required sanitization or PII redaction. | **Use Modified Parameters**: Invoke target tool using `modified_parameters` payload. |
| **`VERIFY`** | Action has moderate consequence or uncertainty. | **Hold for Verification**: Do not execute until secondary confirmation or verification checks complete. |
| **`BLOCK`** | Action violates enterprise safety, security, or policy rules. | **Reject Tool Call**: Abort execution immediately; return error/block reason to caller. |
| **`HUMAN_APPROVAL`** | Action carries high real-world consequence or is irreversible. | **Pause Execution**: Route to enterprise approver. **NEVER execute** until authorized approval token is received. |

---

## 5. Consequence Signals

When the MCP proxy constructs `interaction_context`, it may supply:

* **`domain`**: `GENERAL`, `FINANCE`, `HEALTHCARE`, `LEGAL`, `SECURITY`, `INFRASTRUCTURE`
* **`action_type`**:
  * `INFORMATIONAL`: Read-only queries, lookups, summaries.
  * `DECISION`: Calculations, policy evaluations, eligibility checks.
  * `EXTERNAL_ACTION`: State mutations, money transfers, database writes, email dispatch, deletions.
* **`reversible`**: `true` (can be rolled back) / `false` (irreversible financial transfer or deletion).
* **`data_sensitivity`**: `LOW`, `MEDIUM`, `HIGH`.

> **Note**: If `interaction_context` is omitted or partially specified, ControlPlane's internal tool registry automatically enriches known tools (e.g. `transfer_money` defaults to `FINANCE`, `reversible=False`, `HIGH` sensitivity).

---

## 6. High-Consequence Execution Flow

```
1. Agent emits tool call: transfer_money(amount=800000)
2. MCP Proxy intercepts tool call
3. MCP Proxy calls ControlPlane: POST /execution-rail
4. ControlPlane classifies: HIGH consequence -> HUMAN_APPROVAL -> allowed: false
5. MCP Proxy inspects: response.allowed == false, response.requires_human == true
6. MCP Proxy pauses execution and notifies enterprise approval queue
7. Target MCP Server is NOT called
```

---

## 7. Fail-Safe Behavior & Security Principles

The MCP proxy must implement the core governance principle:

$$\text{High-Consequence External Action} + \text{Uncertain Governance State} = \text{\textbf{DO NOT EXECUTE}}$$

* **Timeout**: If ControlPlane does not respond within the configured timeout (e.g. 2000ms), the MCP proxy must **fail closed** for any `EXTERNAL_ACTION` in sensitive domains.
* **HTTP 5xx / Network Error**: The MCP proxy must **not** bypass ControlPlane to invoke the tool directly. It must return a governance failure error to the calling agent.
* **Do Not Duplicate Policy**: The MCP proxy acts strictly as the **protocol and execution boundary**. It should not re-implement policy or consequence logic locally.

---

## 8. Realistic Examples

### Example A: Blocked Financial Transfer

**MCP Proxy Request:**
```http
POST /execution-rail HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "tool": "transfer_money",
  "parameters": {
    "amount": 800000,
    "currency": "INR",
    "beneficiary": "new_beneficiary_987"
  },
  "user_context": {
    "user_role": "finance_operator",
    "user_id": "USR-FIN-001"
  }
}
```

**ControlPlane Response:**
```json
{
  "allowed": false,
  "decision": "HUMAN_APPROVAL",
  "reason": "High-consequence irreversible finance action requires human approval",
  "tool": "transfer_money",
  "modified_parameters": null,
  "consequence_tier": "HIGH",
  "requires_human": true,
  "request_id": null
}
```

---

### Example B: Approved Read Query

**MCP Proxy Request:**
```http
POST /execution-rail HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "tool": "query_database",
  "parameters": {
    "query": "SELECT count(*) FROM orders"
  },
  "user_context": {
    "user_role": "analyst"
  }
}
```

**ControlPlane Response:**
```json
{
  "allowed": true,
  "decision": "PASS",
  "reason": "Low-consequence action approved for execution",
  "tool": "query_database",
  "modified_parameters": null,
  "consequence_tier": "LOW",
  "requires_human": false,
  "request_id": null
}
```

---

## 9. Python SDK Interface (Direct Library Integration)

If the MCP proxy is co-located in Python, it may invoke the runtime directly:

```python
from controlplane.runtime import UnifiedControlPlane
from controlplane.models import ToolCallRequest, UserContext

runtime = UnifiedControlPlane()

tool_call = ToolCallRequest(
    tool="transfer_money",
    parameters={"amount": 800000, "currency": "INR"},
    user_context=UserContext(user_role="finance_operator"),
)

result = await runtime.run_tool_call(tool_call)

if not result.rail_result.allowed:
    print(f"Execution halted: {result.rail_result.reason}")
    # Do not call MCP tool
else:
    # Safe to invoke MCP tool
    pass
```

---

## 10. Known Limitations & Handoff Checklist

1. **Human Approval Workflow**: ControlPlane marks requests as `requires_human: true` and yields `HUMAN_APPROVAL`. The asynchronous storage and webhook resumption of approved requests is managed by the MCP layer's approval queue.
2. **Mock External Systems**: All external tools in this repository use `MockExternalSystem` for safety. Real side-effects will be executed by the target MCP server only after `allowed == true`.
