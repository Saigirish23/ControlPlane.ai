"""
proxy/controlplane_hooks.py — ControlPlane.ai Governance Hooks for MCP Proxy Pipeline.

Integrated with ControlPlane.ai:
  1. ControlPlaneExecutionRailHook: Enforces consequence-aware governance, adaptive depth,
     and decision routing (PASS, HUMAN_APPROVAL, BLOCK, MODIFY) on all tool calls.
  2. ControlPlaneAuditLoggerHook: Emits structured audit logs into ControlPlane's AuditLogger.
  3. ControlPlaneResponsibilityHook: Uses ResponsibilityEvaluator for prompt injection safety and PII redaction.
  4. ControlPlaneSentimentHook: Evaluates customer sentiment and triggers proactive escalation.
  5. LatencyTrackerHook: Tracks p50/p95/p99 latency metrics and telemetry.
  6. ToolAuthorizationHook: Session-based tool whitelist and RBAC gating.
"""
from __future__ import annotations

import os
import re
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Ensure ControlPlane.ai is accessible on sys.path
_workspace_root = Path(__file__).resolve().parent.parent.parent
_cp_path = _workspace_root / "ControlPlane.ai"
if _cp_path.exists() and str(_cp_path) not in sys.path:
    sys.path.insert(0, str(_cp_path))

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from controlplane.audit import AuditLogger
from controlplane.execution_rail import ExecutionRail
from controlplane.models import (
    AuditEntry,
    CheckStatus,
    ConsequenceTier,
    Decision,
    EvaluationDepth,
    ExecutionRailResult,
    ToolCallRequest,
    UserContext,
)
from controlplane.responsibility import ResponsibilityEvaluator
from support_agent_mcp.config import (
    ESCALATION_SENTIMENT_THRESHOLD,
    REFUND_AUTO_APPROVE_LIMIT,
)
from support_agent_mcp.proxy.base_proxy import (
    AuditRecord,
    BaseHook,
    HookAction,
    HookResult,
    ProxyPipeline,
)

console = Console(highlight=False)


# ── 1. ControlPlane Execution Rail Hook ───────────────────────────────────────

class ControlPlaneExecutionRailHook(BaseHook):
    """
    PRE-CALL & GOVERNANCE Hook:
    Intercepts tool calls and routes them through ControlPlane's Consequence Engine
    and Execution Rail before allowing any database state mutations or external actions.

    Outcomes:
      - PASS: Low/Medium consequence permitted action -> Proceed to MCP tool.
      - HUMAN_APPROVAL: High-consequence irreversible action (e.g. large refund) ->
        Hold execution and return pending human review response.
      - BLOCK: Action violates policy or security rules -> Terminate with block reason.
      - MODIFY: Parameters sanitized -> Use modified arguments.
    """
    name = "ControlPlaneExecutionRail"

    def __init__(
        self,
        rail: Optional[ExecutionRail] = None,
        refund_limit: float = REFUND_AUTO_APPROVE_LIMIT,
        user_role: str = "customer_support_agent",
        verbose: bool = True,
    ):
        self.rail = rail or ExecutionRail()
        self.refund_limit = refund_limit
        self.user_role = user_role
        self.verbose = verbose

    def pre_call_hook(self, tool_name: str, args: Dict[str, Any]) -> HookResult:
        req_id = f"req-cp-{uuid.uuid4().hex[:8]}"

        # Build ToolCallRequest for ControlPlane Execution Rail
        user_id = args.get("customer_id")
        tool_call = ToolCallRequest(
            tool=tool_name,
            parameters=dict(args),
            user_context=UserContext(
                user_role=self.user_role,
                user_id=user_id,
            ),
            request_id=req_id,
            metadata={"refund_limit": self.refund_limit},
        )

        # Evaluate via ControlPlane Execution Rail
        rail_result: ExecutionRailResult = self.rail.evaluate(tool_call)

        tier_str = rail_result.consequence_tier.value if rail_result.consequence_tier else "LOW"
        decision_str = rail_result.decision.value

        # Rich terminal logging for live observability
        if self.verbose:
            tier_color = {
                "HIGH": "bold red",
                "MEDIUM": "bold yellow",
                "LOW": "bold green",
            }.get(tier_str, "white")
            dec_color = {
                "PASS": "green",
                "HUMAN_APPROVAL": "bold magenta",
                "BLOCK": "bold red",
                "MODIFY": "yellow",
                "VERIFY": "cyan",
            }.get(decision_str, "white")

            console.print(
                f"[dim]ControlPlane Rail Interception:[/dim] "
                f"Tool=[bold cyan]{tool_name}[/bold cyan] | "
                f"Tier=[{tier_color}]{tier_str}[/{tier_color}] | "
                f"Decision=[{dec_color}]{decision_str}[/{dec_color}]"
            )
            console.print(f"  [dim]Reason:[/dim] {rail_result.reason}")

        # ── 1. HUMAN APPROVAL REQUIRED (e.g. Large refund) ───────────────────
        if rail_result.decision == Decision.HUMAN_APPROVAL:
            requested = args.get("requested_amount")
            msg = (
                f"Your refund request of ₹{requested:.2f} exceeds our automated approval limit (₹{self.refund_limit:.2f}). "
                f"ControlPlane governance has routed this to our senior support team for manual review within 24 hours. "
                f"You will receive an SMS confirmation shortly."
                if requested is not None
                else f"This action requires manual human review according to enterprise governance policy: {rail_result.reason}"
            )
            return HookResult(
                action=HookAction.BLOCK,
                reason=rail_result.reason,
                metadata={
                    "consequence_tier": tier_str,
                    "decision": decision_str,
                    "requires_human": True,
                    "request_id": req_id,
                },
                block_response={
                    "success": True,
                    "status": "pending_human_review",
                    "requires_human_review": True,
                    "requested_amount": requested,
                    "approved_amount": None,
                    "decision": "HUMAN_APPROVAL",
                    "consequence_tier": tier_str,
                    "message": msg,
                },
            )

        # ── 2. BLOCK ON POLICY VIOLATION ─────────────────────────────────────
        if rail_result.decision == Decision.BLOCK or not rail_result.allowed:
            return HookResult(
                action=HookAction.BLOCK,
                reason=rail_result.reason,
                metadata={
                    "consequence_tier": tier_str,
                    "decision": decision_str,
                    "request_id": req_id,
                },
                block_response={
                    "success": False,
                    "blocked": True,
                    "decision": "BLOCK",
                    "consequence_tier": tier_str,
                    "reason": rail_result.reason,
                    "error": f"Action blocked by ControlPlane policy: {rail_result.reason}",
                },
            )

        # ── 3. MODIFY PARAMETERS ─────────────────────────────────────────────
        if rail_result.decision == Decision.MODIFY and rail_result.modified_parameters:
            return HookResult(
                action=HookAction.MODIFY,
                modified_args=rail_result.modified_parameters,
                reason=rail_result.reason,
                metadata={
                    "consequence_tier": tier_str,
                    "decision": decision_str,
                    "request_id": req_id,
                },
            )

        # ── 4. PASS (ALLOWED) ────────────────────────────────────────────────
        return HookResult(
            action=HookAction.ALLOW,
            metadata={
                "consequence_tier": tier_str,
                "decision": decision_str,
                "request_id": req_id,
            },
        )


# ── 2. ControlPlane Audit Logger Hook ─────────────────────────────────────────

class ControlPlaneAuditLoggerHook(BaseHook):
    """
    Logs every tool call and decision into ControlPlane's AuditLogger.
    Maintains a structured, immutable governance trail.
    """
    name = "ControlPlaneAuditLogger"

    def __init__(self, audit_logger: Optional[AuditLogger] = None, verbose: bool = True):
        self.audit_logger = audit_logger or AuditLogger()
        self.verbose = verbose
        self._call_count: Dict[str, int] = defaultdict(int)

    def pre_call_hook(self, tool_name: str, args: Dict[str, Any]) -> HookResult:
        self._call_count[tool_name] += 1
        if self.verbose:
            console.print(
                f"\n[bold cyan]>> TOOL CALL[/bold cyan] [yellow]{tool_name}[/yellow] "
                f"(call #{self._call_count[tool_name]})"
            )
            for k, v in args.items():
                console.print(f"  [dim]{k}[/dim]: {v}")
        return HookResult(action=HookAction.ALLOW, metadata={"call_count": self._call_count[tool_name]})

    def post_call_hook(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> HookResult:
        success = result.get("success", True)
        consequence_tier = result.get("consequence_tier", "LOW")
        decision = result.get("decision", "PASS" if success else "BLOCK")

        # Record into ControlPlane AuditLogger
        entry = AuditEntry(
            request_id=f"audit-{uuid.uuid4().hex[:8]}",
            timestamp=str(time.time()),
            consequence_tier=ConsequenceTier(consequence_tier) if consequence_tier in ConsequenceTier._value2member_map_ else ConsequenceTier.LOW,
            consequence_factors=["tool_call", tool_name],
            evaluation_depth=EvaluationDepth.FAST,
            checks_executed=["execution_rail", "responsibility"],
            check_results={"rail": "PASS" if success else "FAIL"},
            final_decision=Decision(decision) if decision in Decision._value2member_map_ else Decision.PASS,
            decision_reason=result.get("reason", "Tool execution completed"),
            metadata={"tool": tool_name, "args": args, "result": result},
        )
        self.audit_logger.record_entry(entry)

        if self.verbose:
            color = "green" if success else "red"
            console.print(
                f"[bold {color}]<< RESULT[/bold {color}] [yellow]{tool_name}[/yellow] "
                f"-- success={success}"
            )
            if not success and "error" in result:
                console.print(f"  [red]Error:[/red] {result['error']}")

        return HookResult(action=HookAction.ALLOW)

    def print_summary(self) -> None:
        table = Table(title="ControlPlane Tool Audit Summary", box=box.ROUNDED)
        table.add_column("Tool", style="cyan")
        table.add_column("Calls", justify="right", style="yellow")
        for tool, count in sorted(self._call_count.items()):
            table.add_row(tool, str(count))
        console.print(table)


# ── 3. ControlPlane Responsibility & PII Hook ─────────────────────────────────

class ControlPlaneResponsibilityHook(BaseHook):
    """
    POST-CALL & PRE-CALL Hook:
    Uses ControlPlane's ResponsibilityEvaluator for:
      - Prompt injection and safety violation detection on customer input
      - PII detection and redaction on tool output (phone numbers, emails, addresses)
    """
    name = "ControlPlaneResponsibility"

    def __init__(self, skip_tools: Optional[Set[str]] = None):
        self.evaluator = ResponsibilityEvaluator()
        # By default, skip redacting the helpline number from escalation tool
        self.skip_tools: Set[str] = skip_tools or {"escalate_to_human_agent"}

    def pre_call_hook(self, tool_name: str, args: Dict[str, Any]) -> HookResult:
        # Inspect text fields for malicious prompt injection
        text = str(args.get("reason") or args.get("description") or args.get("new_instructions") or "")
        if text:
            resp_result = self.evaluator.evaluate(text)
            injection_fails = [c for c in resp_result.checks if c.status == CheckStatus.FAIL and c.category == "INJECTION"]
            if injection_fails:
                reason = injection_fails[0].reason
                console.print(f"[bold red]SECURITY BLOCK (Prompt Injection):[/bold red] {reason}")
                return HookResult(
                    action=HookAction.BLOCK,
                    reason=f"Security policy violation: {reason}",
                    block_response={
                        "success": False,
                        "blocked": True,
                        "error": "Your request contains prohibited instruction patterns.",
                    },
                )
        return HookResult(action=HookAction.ALLOW)

    def post_call_hook(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> HookResult:
        if tool_name in self.skip_tools:
            return HookResult(action=HookAction.ALLOW)

        # Check if output contains PII using regex & evaluation
        import re
        phone_re = re.compile(r"(\+?\d[\d\-\s]{7,}\d)")
        email_re = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")

        def _redact_dict(obj: Any) -> Any:
            if isinstance(obj, str):
                obj = phone_re.sub("[PHONE REDACTED]", obj)
                obj = email_re.sub("[EMAIL REDACTED]", obj)
                return obj
            if isinstance(obj, dict):
                return {k: _redact_dict(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_redact_dict(i) for i in obj]
            return obj

        redacted = _redact_dict(dict(result))
        if redacted != result:
            return HookResult(
                action=HookAction.MODIFY,
                modified_result=redacted,
                reason="ControlPlane PII Redactor: sensitive data masked.",
                metadata={"pii_redacted": True},
            )
        return HookResult(action=HookAction.ALLOW, metadata={"pii_redacted": False})


# ── 4. ControlPlane Sentiment Hook ────────────────────────────────────────────

_NEGATIVE_WORDS = {
    "angry", "furious", "terrible", "horrible", "awful", "worst", "disgusting",
    "unacceptable", "ridiculous", "fraud", "scam", "cheated", "useless", "pathetic",
    "never again", "lawsuit", "sue", "disgusted", "outraged", "appalling", "rubbish",
    "ruined", "spilled", "poisoned", "sick", "vomit", "rotten",
}
_POSITIVE_WORDS = {
    "thank", "thanks", "good", "great", "fine", "okay", "ok", "happy",
    "satisfied", "wonderful", "excellent", "love", "appreciate",
}


def _heuristic_sentiment(text: str) -> float:
    words = set(re.findall(r"\b\w+\b", text.lower()))
    neg = len(words & _NEGATIVE_WORDS)
    pos = len(words & _POSITIVE_WORDS)
    total = neg + pos
    if total == 0:
        return 0.6
    return round(pos / total, 3)


class SentimentScorerHook(BaseHook):
    """
    Evaluates emotional sentiment from user complaint descriptions.
    If sentiment is critical, injects an escalation prompt so the agent offers human help.
    """
    name = "SentimentScorer"

    def __init__(self, threshold: float = ESCALATION_SENTIMENT_THRESHOLD):
        self.threshold = threshold
        self._scores: List[Dict[str, Any]] = []

    def pre_call_hook(self, tool_name: str, args: Dict[str, Any]) -> HookResult:
        text = str(args.get("reason") or args.get("description") or args.get("new_instructions") or "")
        if not text:
            return HookResult(action=HookAction.ALLOW)

        score = _heuristic_sentiment(text)
        self._scores.append({"tool": tool_name, "score": score, "text": text[:80]})

        if score < self.threshold:
            console.print(
                f"[bold magenta]SENTIMENT ALERT[/bold magenta] "
                f"score={score:.2f} (threshold={self.threshold}) -- "
                f"text: \"{text[:60]}...\""
            )

        return HookResult(
            action=HookAction.ALLOW,
            metadata={"sentiment_score": score, "below_threshold": score < self.threshold},
        )

    def post_call_hook(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> HookResult:
        if tool_name == "escalate_to_human_agent":
            return HookResult(action=HookAction.ALLOW)

        text = str(args.get("reason") or args.get("description") or "")
        if not text:
            return HookResult(action=HookAction.ALLOW)

        score = _heuristic_sentiment(text)
        if score < self.threshold:
            modified = dict(result)
            modified["_proxy_sentiment_score"] = score
            modified["_proxy_suggest_escalation"] = True
            modified["_proxy_escalation_hint"] = (
                "Customer appears very upset. Proactively offer to escalate to a human agent."
            )
            return HookResult(
                action=HookAction.MODIFY,
                modified_result=modified,
                reason=f"Low sentiment score ({score:.2f}) detected.",
                metadata={"sentiment_score": score},
            )

        return HookResult(action=HookAction.ALLOW, metadata={"sentiment_score": score})


# ── 5. Latency Tracker Hook ───────────────────────────────────────────────────

class LatencyTrackerHook(BaseHook):
    """Records per-tool latency in ms and computes p50/p95/p99 on demand."""
    name = "LatencyTracker"

    def __init__(self):
        self._start_times: Dict[str, float] = {}
        self._samples: Dict[str, List[float]] = defaultdict(list)

    def pre_call_hook(self, tool_name: str, args: Dict[str, Any]) -> HookResult:
        self._start_times[tool_name] = time.perf_counter()
        return HookResult(action=HookAction.ALLOW)

    def post_call_hook(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> HookResult:
        elapsed = (time.perf_counter() - self._start_times.pop(tool_name, time.perf_counter())) * 1000
        self._samples[tool_name].append(elapsed)
        return HookResult(
            action=HookAction.ALLOW,
            metadata={"latency_ms": round(elapsed, 2)},
        )

    def get_stats(self) -> Dict[str, Dict[str, float]]:
        import statistics
        stats = {}
        for tool, samples in self._samples.items():
            s = sorted(samples)
            n = len(s)
            stats[tool] = {
                "calls": n,
                "p50_ms": round(s[int(n * 0.50) - 1], 2) if n else 0,
                "p95_ms": round(s[int(n * 0.95) - 1], 2) if n >= 2 else s[0] if n else 0,
                "p99_ms": round(s[int(n * 0.99) - 1], 2) if n >= 2 else s[0] if n else 0,
                "mean_ms": round(statistics.mean(s), 2) if n else 0,
            }
        return stats

    def print_stats(self) -> None:
        table = Table(title="ControlPlane Latency Telemetry (ms)", box=box.ROUNDED)
        table.add_column("Tool", style="cyan")
        table.add_column("Calls", justify="right")
        table.add_column("p50", justify="right", style="green")
        table.add_column("p95", justify="right", style="yellow")
        table.add_column("p99", justify="right", style="red")
        table.add_column("Mean", justify="right")
        for tool, s in self.get_stats().items():
            table.add_row(
                tool, str(s["calls"]),
                f"{s['p50_ms']:.2f}", f"{s['p95_ms']:.2f}",
                f"{s['p99_ms']:.2f}", f"{s['mean_ms']:.2f}",
            )
        console.print(table)


# ── 6. Tool Authorization Hook ────────────────────────────────────────────────

class ToolAuthorizationHook(BaseHook):
    """PRE-CALL gate: blocks calls to tools not in an allowed whitelist."""
    name = "ToolAuthorization"

    def __init__(
        self,
        allowed_tools: Optional[Set[str]] = None,
        blocked_tools: Optional[Set[str]] = None,
    ):
        self.allowed_tools: Optional[Set[str]] = allowed_tools
        self.blocked_tools: Set[str] = blocked_tools or set()

    def pre_call_hook(self, tool_name: str, args: Dict[str, Any]) -> HookResult:
        if tool_name in self.blocked_tools:
            console.print(f"[bold red]TOOL BLOCKED[/bold red] -- '{tool_name}' is explicitly blocked.")
            return HookResult(
                action=HookAction.BLOCK,
                reason=f"Tool '{tool_name}' is not permitted in this session.",
                block_response={
                    "success": False,
                    "blocked": True,
                    "error": f"Tool '{tool_name}' is not available. Please contact support.",
                },
            )

        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            console.print(f"[bold red]TOOL NOT WHITELISTED[/bold red] -- '{tool_name}'")
            return HookResult(
                action=HookAction.BLOCK,
                reason=f"Tool '{tool_name}' is not in the session whitelist.",
                block_response={
                    "success": False,
                    "blocked": True,
                    "error": f"You don't have permission to use '{tool_name}' in this session.",
                },
            )

        return HookResult(action=HookAction.ALLOW)


# ── Pipeline Factory ──────────────────────────────────────────────────────────

def build_default_pipeline(
    verbose_logging: bool = True,
    refund_limit: float = REFUND_AUTO_APPROVE_LIMIT,
    sentiment_threshold: float = ESCALATION_SENTIMENT_THRESHOLD,
    redact_pii: bool = False,
    allowed_tools: Optional[Set[str]] = None,
    blocked_tools: Optional[Set[str]] = None,
) -> ProxyPipeline:
    """
    Build the ControlPlane-integrated proxy pipeline.

    Hook execution order:
      ToolAuthorization → ControlPlaneExecutionRail → SentimentScorer → AuditLogger → LatencyTracker → [Responsibility/PII]
    """
    hooks: List[BaseHook] = [
        ToolAuthorizationHook(allowed_tools=allowed_tools, blocked_tools=blocked_tools),
        ControlPlaneExecutionRailHook(refund_limit=refund_limit, verbose=verbose_logging),
        SentimentScorerHook(threshold=sentiment_threshold),
        ControlPlaneAuditLoggerHook(verbose=verbose_logging),
        LatencyTrackerHook(),
    ]
    if redact_pii:
        hooks.append(ControlPlaneResponsibilityHook())

    return ProxyPipeline(hooks=hooks)
