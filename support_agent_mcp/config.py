"""
config.py — Central configuration for the support agent MCP environment.
Loads API keys and app settings from environment variables or a .env file.
"""
import os
from pathlib import Path

# Auto-load .env if present (checks package directory and workspace root)
try:
    from dotenv import load_dotenv
    _pkg_env = Path(__file__).parent / ".env"
    _root_env = Path(__file__).parent.parent / ".env"
    if _root_env.exists():
        load_dotenv(_root_env)
    elif _pkg_env.exists():
        load_dotenv(_pkg_env)
except ImportError:
    pass


# ── Project root ────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "data" / "support_db.sqlite"

# ── Gemini API ───────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# ── MCP Server ───────────────────────────────────────────────────────────────
MCP_SERVER_HOST: str = os.getenv("MCP_HOST", "127.0.0.1")
MCP_SERVER_PORT: int = int(os.getenv("MCP_PORT", "8765"))

# ── Proxy / Guard settings ───────────────────────────────────────────────────
# Maximum refund amount allowed without human approval
REFUND_AUTO_APPROVE_LIMIT: float = float(os.getenv("REFUND_AUTO_APPROVE_LIMIT", "200.0"))
# Time window in minutes to reject duplicate refund requests (idempotency guard)
REFUND_IDEMPOTENCY_WINDOW_MINUTES: int = int(os.getenv("REFUND_IDEMPOTENCY_WINDOW_MINUTES", "10"))
# Minimum sentiment score below which we force escalation (0.0 = very negative, 1.0 = positive)
ESCALATION_SENTIMENT_THRESHOLD: float = float(os.getenv("ESCALATION_SENTIMENT_THRESHOLD", "0.35"))
# Enable detailed proxy audit logging
PROXY_AUDIT_LOG: bool = os.getenv("PROXY_AUDIT_LOG", "true").lower() == "true"

# ── Helpline numbers ─────────────────────────────────────────────────────────
HELPLINE_GENERAL: str = os.getenv("HELPLINE_GENERAL", "+1-800-FOOD-001")
HELPLINE_REFUNDS: str = os.getenv("HELPLINE_REFUNDS", "+1-800-FOOD-002")
HELPLINE_SAFETY:  str = os.getenv("HELPLINE_SAFETY",  "+1-800-FOOD-003")
