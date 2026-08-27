# QuickBite Support Agent with ControlPlane.ai Governance

An enterprise-grade customer support AI agent powered by **Google Gemini** and **FastMCP**, governed by **ControlPlane.ai** runtime consequence engine, execution rails, and adaptive safety guardrails.

---

## Repository Structure

```
ControlPlane.ai/
├── controlplane/                  # Consequence-aware runtime governance engine
│   ├── consequence_engine.py      # Evaluates domain, reversibility, and consequence tier
│   ├── depth_planner.py           # Adaptive depth planning
│   ├── execution_rail.py          # Intercepts tool calls pre-execution
│   ├── action_router.py           # Decisions: PASS, HUMAN_APPROVAL, BLOCK, MODIFY
│   ├── responsibility.py          # PII detection and prompt-injection protection
│   ├── audit.py                   # Structured audit logging
│   ├── runtime.py                 # Unified ControlPlane runtime orchestration
│   └── models.py                  # Strongly typed Pydantic domain models
├── support_agent_mcp/             # Food-delivery customer support application
│   ├── agent/                     # Gemini customer support agent
│   ├── proxy/                     # MCP interceptor and governance pipeline
│   ├── server.py                  # FastMCP tool server
│   ├── db.py                      # SQLite repository layer with seed dataset
│   ├── models.py                  # Support domain models
│   ├── config.py                  # Environment configuration loader
│   ├── cli.py                     # Interactive chat and scenario runner
│   └── tests/                     # Support-agent integration tests
├── docs/                          # ControlPlane docs and AIC problem-statement context
├── tests/                         # ControlPlane unit and integration tests
├── .env.example                   # Configuration template
├── requirements.txt               # Consolidated dependencies
└── pytest.ini                     # Unified test configuration
```

---

## Quick Start

### 1. Environment Setup
```powershell
# Activate virtual environment
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Secrets
Copy `.env.example` to `.env` and set your `GEMINI_API_KEY`:
```ini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash
```

### 3. Run the Agent CLI
```powershell
python support_agent_mcp/cli.py
```
- **Option 1**: Interactive Live Chat with customer personas and live ControlPlane governance inspection (`/audit`, `/stats`, `/tools`, `/switch`).
- **Option 2**: Run Automated Scenario Test Suite (6 end-to-end governance scenarios).

### 4. Run the Full Test Suite
```powershell
# Run all 106 tests across ControlPlane and Support Agent
pytest
```
