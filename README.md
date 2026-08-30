# ControlPlane.ai — Consequence-Aware Runtime Governance Layer

An enterprise-grade, consequence-aware runtime governance engine and proxy architecture for AI agents, built for the **Accenture Innovation Challenge 2026 (Round 2: Prototype Development)**.

---

## 🎯 Core Concept

Traditional AI guardrails apply uniform, static evaluation to every interaction. **ControlPlane.ai** dynamically assesses the **business consequence** of an interaction and adjusts its evaluation depth in real-time:

- **LOW Consequence** (e.g., read-only order status) → **FAST Path** (< 0.02 ms deterministic regex & syntax validation).
- **MEDIUM Consequence** (e.g., reversible decision) → **DEEP Path** (semantic safety, groundedness, domain policy).
- **HIGH Consequence** (e.g., irreversible refund or bank transfer) → **HIGH_ASSURANCE Path** (multi-factor policy enforcement, strict execution rails, human approval gating).

---

## 🏛️ System Architecture

```
                    ┌─────────────────────────┐
                    │    INCOMING REQUEST     │
                    │  (Prompt or Tool Call)  │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │    ContextExtractor     │
                    │ (Domain, Reversibility, │
                    │    Data Sensitivity)    │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │    ConsequenceEngine    │
                    │  (LOW / MEDIUM / HIGH)  │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │      DepthPlanner       │
                    │ (FAST / DEEP / HIGH_ASS)│
                    └────────────┬────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
┌───────────────┐        ┌───────────────┐        ┌───────────────┐
│ FastEvaluator │        │ DeepEvaluator │        │ HighAssurance │
│ • Regex PII   │        │ • FAST checks │        │ • DEEP checks │
│ • Injection   │        │ • Semantic    │        │ • Dual Auth   │
│ • Auth Check  │        │ • Domain Pol. │        │ • Exec Rail   │
└───────┬───────┘        └───────┬───────┘        └───────┬───────┘
        │                        │                        │
        └────────────────────────┼────────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │ Performance / Cost Eval │
                    │ • Groundedness Heuristic│
                    │ • Token Cost & Loops    │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │      ActionRouter       │
                    │  PASS / MODIFY / VERIFY │
                    │  BLOCK / HUMAN_APPROVAL │
                    └────────────┬────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
┌───────────────┐        ┌───────────────┐        ┌───────────────┐
│     ALLOW     │        │ REDACT/VERIFY │        │ BLOCK / HUMAN │
│ Execute Model │        │ Modify Output │        │ Prevent Exec, │
│ or Tool Call  │        │ or Flag Note  │        │ Prompt Review │
└───────┬───────┘        └───────┬───────┘        └───────┬───────┘
        │                        │                        │
        └────────────────────────┼────────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │       AuditLogger       │
                    │ Structured JSON Payload │
                    └─────────────────────────┘
```

---

## ⚡ Measured Telemetry & Latency

Benchmarks are reproducible with:

```bash
python scripts/benchmark.py --iterations 10000 --warmup 250
```

Latest verified local run:

| Subsystem / Operation | Latency (p50) | Latency (p95) | Latency (p99) |
|---|:---:|:---:|:---:|
| **ConsequenceEngine.evaluate** | `0.0046 ms` | `0.0061 ms` | `0.0099 ms` |
| **ExecutionRail.evaluate** | `0.0126 ms` | `0.0236 ms` | `0.0390 ms` |
| **FastEvaluator.evaluate** | `0.0136 ms` | `0.0205 ms` | `0.0347 ms` |
| **ResponsibilityEvaluator (PII + Injection)** | `0.0421 ms` | `0.0624 ms` | `0.0988 ms` |
| **Core Governance Sample** | **`0.0280 ms`** | **`0.0444 ms`** | **`0.0777 ms`** |
| *LLM API Inference (Gemini 3.6 Flash)* | *`640.0 ms`* | *`890.0 ms`* | *`1200.0 ms`* |

> **Key Takeaway:** ControlPlane adds **< 0.1ms overhead**, proving runtime safety introduces zero perceptible user latency.

---

## 📂 Repository Structure

```
ControlPlane.ai/
├── controlplane/                  # Core governance engine
│   ├── consequence_engine.py      # Consequence tier classification (LOW/MED/HIGH)
│   ├── depth_planner.py           # Evaluation depth planner (FAST/DEEP/HIGH_ASSURANCE)
│   ├── evaluators/                # Pluggable evaluators (fast, deep, high-assurance)
│   ├── execution_rail.py          # Tool call interceptor and decision enforcement
│   ├── action_router.py           # 5-way decision router (PASS, MODIFY, VERIFY, BLOCK, HUMAN)
│   ├── responsibility.py          # Regex scanning for 6 PII types & 5 injection attack patterns
│   ├── stream_guardrail.py        # Async chunked token stream inspector
│   ├── audit.py                   # Privacy-preserving structured JSON audit logger
│   ├── runtime.py                 # UnifiedControlPlane runtime orchestrator
│   ├── api.py                     # FastAPI REST server (/control, /execution-rail)
│   └── demo_runtime.py            # Standalone CLI walkthrough of 4 core scenarios
├── support_agent_mcp/             # Food delivery support agent prototype (QuickBite)
│   ├── agent/client.py            # Gemini 3.6 customer support agent
│   ├── proxy/                     # Interception pipeline wrapping all tool executions
│   ├── server.py                  # 8 support tools with SQLite database backend
│   ├── db.py                      # SQLite database with WAL mode & seed records
│   ├── cli.py                     # Interactive terminal chat & scenario runner
│   └── tests/                     # MCP proxy integration tests
├── docs/                          # Architecture guides, contracts, and handoff specs
│   ├── TEAM_IMPLEMENTATION_STATUS_AND_WORKPLAN.md # Master team implementation plan
│   └── controlplane_mcp_contract.json            # OpenAPI 3.1.0 specification
├── scripts/
│   └── benchmark.py               # Deterministic latency benchmark script
├── tests/                         # 139 unit, integration, lifecycle, and fault-injection tests
├── requirements.txt               # Dependencies
└── pytest.ini                     # Pytest configuration
```

---

## 🚀 Quick Start

### 1. Environment Setup
```bash
# Clone the repository
git clone https://github.com/Saigirish23/ControlPlane.ai.git
cd ControlPlane.ai

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment
Create a `.env` file with your Gemini API key:
```ini
GEMINI_API_KEY=your_google_genai_api_key_here
GEMINI_MODEL=gemini-3.6-flash
```

### 3. Run Core Governance Demo
```bash
python3 -m controlplane.demo_runtime
python3 demo_member2_flows.py
```
*Demonstrates core runtime flows:*
1. Marketing Email Rewrite (LOW → FAST → PASS)
2. Refund Eligibility Decision (MEDIUM → DEEP → VERIFY)
3. High-Value ₹8,00,000 Transfer (HIGH → HIGH_ASSURANCE → HUMAN_APPROVAL → Blocked)
4. Streaming PII Buffer Redaction (Token Stream → Flagged → Redacted)
5. MCP Agent & Human Approval Lifecycle (Pending → Revalidation → Execution / Rejection)

### 4. Run the Full Test Suite
```bash
python3 -m pytest tests/ support_agent_mcp/tests/ -v
```
*(Latest verified suite: 139 unit, integration, lifecycle, and fault-injection tests passing).*

### 5. Run Member 1 Readiness Checks
```bash
python3 scripts/benchmark.py --iterations 10000 --warmup 250
python3 -m pytest tests/ support_agent_mcp/tests/ -q
```

Member 1 readiness status:
- Clean install from `requirements.txt` verified in a fresh virtual environment.
- Gemini defaults normalized to `gemini-3.6-flash`.
- Fault-injection coverage added for prompt injection, stream failures, denied tools, and DB non-mutation.
- Secret scan found no committed `.env` file or key-format matches in the tracked repository.

---

## 👥 Three-Member Workload Distribution

For full details on team member assignments, blocker patches, and the parallel execution plan, refer to:
👉 [docs/TEAM_IMPLEMENTATION_STATUS_AND_WORKPLAN.md](docs/TEAM_IMPLEMENTATION_STATUS_AND_WORKPLAN.md)
