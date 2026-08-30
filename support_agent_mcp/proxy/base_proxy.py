"""
proxy/base_proxy.py — Abstract forward proxy and interceptor interface.

Architecture
────────────
  Agent ──► ProxyPipeline ──► MCPServer tools ──► SQLite DB
                │
                ├── pre_call_hook(tool_name, args) → HookResult
                └── post_call_hook(tool_name, args, result) → HookResult

Any interceptor (guard, scorer, logger, router) subclasses BaseHook and
implements pre_call_hook / post_call_hook. Hooks are stacked in a pipeline
inside ProxyPipeline — order matters.

To plug in ControlPlane.ai later, just drop a new BaseHook subclass into the
pipeline in proxy/controlplane_hooks.py. The MCP server and agent stay untouched.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ── Hook result ───────────────────────────────────────────────────────────────

class HookAction(str, Enum):
    ALLOW   = "allow"    # Pass through unchanged
    MODIFY  = "modify"   # Continue, but use modified args/result
    BLOCK   = "block"    # Stop execution, return block_response to agent
    ESCALATE = "escalate" # Force escalation regardless of tool outcome


@dataclass
class HookResult:
    action:         HookAction = HookAction.ALLOW
    modified_args:  Optional[Dict[str, Any]] = None   # Used when action=MODIFY (pre-call)
    modified_result: Optional[Dict[str, Any]] = None  # Used when action=MODIFY (post-call)
    block_response: Optional[Dict[str, Any]] = None   # Returned to agent when action=BLOCK
    metadata:       Dict[str, Any] = field(default_factory=dict)  # Scores, flags, etc.
    reason:         Optional[str] = None               # Why this action was taken


# ── Audit record ─────────────────────────────────────────────────────────────

@dataclass
class AuditRecord:
    tool_name:       str
    args:            Dict[str, Any]
    result:          Optional[Dict[str, Any]]
    pre_hook_results:  List[Dict[str, Any]]
    post_hook_results: List[Dict[str, Any]]
    latency_ms:      float
    final_action:    HookAction
    blocked:         bool
    timestamp:       float = field(default_factory=time.time)


# ── Abstract hook base ────────────────────────────────────────────────────────

class BaseHook(ABC):
    """
    Abstract base class for all proxy interceptors.

    Subclass this to create:
    - Guardrails (refund limit checker, toxic content filter)
    - Scorers   (sentiment analyzer, intent classifier)
    - Loggers   (audit trail, metrics emitter)
    - Routers   (model switcher, A/B test gating)

    Both hooks are optional to override — default is ALLOW passthrough.
    """

    #: Human-readable name used in logs and audit records
    name: str = "BaseHook"

    def pre_call_hook(
        self,
        tool_name: str,
        args: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> HookResult:
        """
        Called BEFORE the MCP tool executes.
        Use to: validate args, check policy, score intent, modify args.

        Return HookResult(action=BLOCK, block_response={...}) to prevent execution.
        Return HookResult(action=MODIFY, modified_args={...}) to rewrite args.
        Return HookResult(action=ALLOW) to pass through.
        """
        return HookResult(action=HookAction.ALLOW)

    def post_call_hook(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> HookResult:
        """
        Called AFTER the MCP tool executes.
        Use to: score response, append metadata, redact PII, force escalation.

        Return HookResult(action=MODIFY, modified_result={...}) to rewrite result.
        Return HookResult(action=ESCALATE) to override result with escalation signal.
        Return HookResult(action=ALLOW) to pass through unchanged.
        """
        return HookResult(action=HookAction.ALLOW)


# ── Proxy pipeline ────────────────────────────────────────────────────────────

class ProxyPipeline:
    """
    Runs a list of BaseHook interceptors around every MCP tool call.

    Usage:
        pipeline = ProxyPipeline(hooks=[AuditLogger(), RefundGuard(), SentimentScorer()])
        result   = pipeline.call(tool_fn=get_order_details, args={"order_id": "ORD001"})

    The pipeline short-circuits on the first BLOCK result from any pre-call hook.
    Post-call hooks all run even if a previous one returned MODIFY.
    """

    def __init__(self, hooks: Optional[List[BaseHook]] = None):
        self.hooks: List[BaseHook] = hooks or []
        self.audit_log: List[AuditRecord] = []

    def add_hook(self, hook: BaseHook) -> "ProxyPipeline":
        """Append a hook to the end of the pipeline. Returns self for chaining."""
        self.hooks.append(hook)
        return self

    def call(
        self,
        tool_fn,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute tool_fn(**args) through the full hook pipeline.

        Returns the (possibly modified) result dict.
        On BLOCK, returns the block_response from the blocking hook.
        Appends an AuditRecord to self.audit_log.
        """
        tool_name = tool_fn.__name__
        pre_records: List[Dict[str, Any]] = []
        post_records: List[Dict[str, Any]] = []
        final_action = HookAction.ALLOW
        current_args = dict(args)
        pipeline_context: Dict[str, Any] = {}

        t0 = time.perf_counter()

        # ── Pre-call hooks ────────────────────────────────────────────────────
        for hook in self.hooks:
            try:
                hr = hook.pre_call_hook(tool_name, current_args, context=pipeline_context)
            except TypeError:
                hr = hook.pre_call_hook(tool_name, current_args)

            if hr.metadata:
                pipeline_context.update(hr.metadata)

            pre_records.append({
                "hook": hook.name,
                "action": hr.action.value,
                "reason": hr.reason,
                "metadata": hr.metadata,
            })

            if hr.action == HookAction.MODIFY and hr.modified_args:
                current_args = hr.modified_args

            if hr.action == HookAction.BLOCK:
                latency = (time.perf_counter() - t0) * 1000
                self.audit_log.append(AuditRecord(
                    tool_name=tool_name, args=args, result=None,
                    pre_hook_results=pre_records, post_hook_results=[],
                    latency_ms=latency, final_action=HookAction.BLOCK, blocked=True,
                ))
                block_resp = hr.block_response or {
                    "success": False,
                    "blocked": True,
                    "reason": hr.reason or "Request blocked by proxy policy.",
                }
                # Run post-call hooks so audit loggers record the blocked event
                for post_hook in self.hooks:
                    try:
                        post_hook.post_call_hook(tool_name, current_args, block_resp, context=pipeline_context)
                    except TypeError:
                        post_hook.post_call_hook(tool_name, current_args, block_resp)
                    except Exception:
                        pass
                return block_resp

        # ── Execute tool ──────────────────────────────────────────────────────
        try:
            result: Dict[str, Any] = tool_fn(**current_args)
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            error_result = {"success": False, "error": str(exc)}
            self.audit_log.append(AuditRecord(
                tool_name=tool_name, args=args, result=error_result,
                pre_hook_results=pre_records, post_hook_results=[],
                latency_ms=latency, final_action=HookAction.ALLOW, blocked=False,
            ))
            for post_hook in self.hooks:
                try:
                    post_hook.post_call_hook(tool_name, current_args, error_result, context=pipeline_context)
                except Exception:
                    pass
            return error_result

        current_result = result

        # ── Post-call hooks ───────────────────────────────────────────────────
        for hook in self.hooks:
            try:
                hr = hook.post_call_hook(tool_name, current_args, current_result, context=pipeline_context)
            except TypeError:
                hr = hook.post_call_hook(tool_name, current_args, current_result)

            if hr.metadata:
                pipeline_context.update(hr.metadata)

            post_records.append({
                "hook": hook.name,
                "action": hr.action.value,
                "reason": hr.reason,
                "metadata": hr.metadata,
            })

            if hr.action == HookAction.MODIFY and hr.modified_result:
                current_result = hr.modified_result
                final_action = HookAction.MODIFY

            if hr.action == HookAction.ESCALATE:
                final_action = HookAction.ESCALATE
                current_result = hr.modified_result or current_result

        latency = (time.perf_counter() - t0) * 1000
        self.audit_log.append(AuditRecord(
            tool_name=tool_name, args=args, result=current_result,
            pre_hook_results=pre_records, post_hook_results=post_records,
            latency_ms=latency, final_action=final_action, blocked=False,
        ))

        return current_result
