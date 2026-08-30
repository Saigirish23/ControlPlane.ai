"""
approval.py — Human Approval Lifecycle for ControlPlane.ai.

Implements the PENDING → EXECUTING → EXECUTED and PENDING → REJECTED
state machine for high-consequence tool calls that require human oversight.

Safety Invariants:
  1. A tool call routed to HUMAN_APPROVAL is NEVER executed until explicitly approved.
  2. The business database is NEVER mutated while a request is in PENDING state.
  3. On approve: tool name + arguments are revalidated against what was originally
     stored; any mismatch causes rejection (anti-tampering).
  4. On approve: ControlPlane governance is re-run to verify the action is still
     permissible (revalidation).
  5. Execution is exactly-once: EXECUTING → EXECUTED is atomic; a second approve
     on an already EXECUTED request is a no-op with an error.
  6. A REJECTED request can never be executed.
  7. A nonexistent request_id returns an error.

State Transitions:
  PENDING   → EXECUTING → EXECUTED     (happy path)
  PENDING   → REJECTED                 (human rejects)
  No other transitions are valid.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

from support_agent_mcp.config import DB_PATH


# ── Approval States ──────────────────────────────────────────────────────────

class ApprovalStatus(str, Enum):
    PENDING   = "PENDING"
    EXECUTING = "EXECUTING"
    EXECUTED  = "EXECUTED"
    REJECTED  = "REJECTED"


# ── Database Setup ───────────────────────────────────────────────────────────

APPROVAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS approval_requests (
    request_id          TEXT PRIMARY KEY,
    tool_name           TEXT NOT NULL,
    tool_args_json      TEXT NOT NULL,
    args_hash           TEXT NOT NULL,
    consequence_tier    TEXT NOT NULL,
    decision            TEXT NOT NULL,
    reason              TEXT NOT NULL,
    user_context_json   TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'PENDING',
    created_at          TEXT NOT NULL,
    resolved_at         TEXT,
    resolved_by         TEXT,
    execution_result    TEXT,
    revalidation_result TEXT,
    notes               TEXT
);
"""


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return a sqlite3 connection for approval storage."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _approval_session(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    conn = _get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_approval_table(db_path: Optional[Path] = None) -> None:
    """Create the approval_requests table if it doesn't exist."""
    with _approval_session(db_path) as conn:
        conn.executescript(APPROVAL_SCHEMA_SQL)


def _hash_args(tool_name: str, args: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash of tool name + arguments."""
    canonical = json.dumps({"tool": tool_name, "args": args}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _ts() -> str:
    return datetime.utcnow().isoformat()


# ── Repository ───────────────────────────────────────────────────────────────

class ApprovalRepository:
    """Low-level CRUD operations on the approval_requests table."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path

    def create(
        self,
        request_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        consequence_tier: str,
        decision: str,
        reason: str,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Insert a new PENDING approval request. Returns the record dict."""
        args_json = json.dumps(tool_args, sort_keys=True, default=str)
        args_hash = _hash_args(tool_name, tool_args)
        user_ctx_json = json.dumps(user_context or {}, default=str)
        now = _ts()

        with _approval_session(self._db_path) as conn:
            conn.execute(
                """INSERT INTO approval_requests
                   (request_id, tool_name, tool_args_json, args_hash,
                    consequence_tier, decision, reason, user_context_json,
                    status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (request_id, tool_name, args_json, args_hash,
                 consequence_tier, decision, reason, user_ctx_json,
                 ApprovalStatus.PENDING.value, now),
            )

        return {
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "args_hash": args_hash,
            "consequence_tier": consequence_tier,
            "decision": decision,
            "reason": reason,
            "status": ApprovalStatus.PENDING.value,
            "created_at": now,
        }

    def get_by_id(self, request_id: str) -> Optional[sqlite3.Row]:
        with _approval_session(self._db_path) as conn:
            return conn.execute(
                "SELECT * FROM approval_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()

    def get_all_pending(self) -> List[sqlite3.Row]:
        with _approval_session(self._db_path) as conn:
            return conn.execute(
                "SELECT * FROM approval_requests WHERE status = ? ORDER BY created_at ASC",
                (ApprovalStatus.PENDING.value,),
            ).fetchall()

    def transition_to_executing(self, request_id: str) -> bool:
        """
        Atomically transition PENDING → EXECUTING.
        Returns True if the transition succeeded, False if the record
        was not in PENDING state (idempotency guard).
        """
        with _approval_session(self._db_path) as conn:
            cursor = conn.execute(
                """UPDATE approval_requests
                   SET status = ?
                   WHERE request_id = ? AND status = ?""",
                (ApprovalStatus.EXECUTING.value, request_id, ApprovalStatus.PENDING.value),
            )
            return cursor.rowcount == 1

    def transition_to_executed(
        self,
        request_id: str,
        execution_result: Dict[str, Any],
        resolved_by: str = "system",
    ) -> bool:
        """
        Atomically transition EXECUTING → EXECUTED.
        Returns True if the transition succeeded.
        """
        with _approval_session(self._db_path) as conn:
            cursor = conn.execute(
                """UPDATE approval_requests
                   SET status = ?, resolved_at = ?, resolved_by = ?,
                       execution_result = ?
                   WHERE request_id = ? AND status = ?""",
                (ApprovalStatus.EXECUTED.value, _ts(), resolved_by,
                 json.dumps(execution_result, default=str),
                 request_id, ApprovalStatus.EXECUTING.value),
            )
            return cursor.rowcount == 1

    def transition_to_rejected(
        self,
        request_id: str,
        resolved_by: str = "human_reviewer",
        notes: Optional[str] = None,
    ) -> bool:
        """
        Atomically transition PENDING → REJECTED.
        Returns True if the transition succeeded.
        """
        with _approval_session(self._db_path) as conn:
            cursor = conn.execute(
                """UPDATE approval_requests
                   SET status = ?, resolved_at = ?, resolved_by = ?, notes = ?
                   WHERE request_id = ? AND status = ?""",
                (ApprovalStatus.REJECTED.value, _ts(), resolved_by, notes,
                 request_id, ApprovalStatus.PENDING.value),
            )
            return cursor.rowcount == 1

    def set_revalidation_result(
        self, request_id: str, revalidation: Dict[str, Any]
    ) -> None:
        """Store the revalidation outcome."""
        with _approval_session(self._db_path) as conn:
            conn.execute(
                "UPDATE approval_requests SET revalidation_result = ? WHERE request_id = ?",
                (json.dumps(revalidation, default=str), request_id),
            )


# ── Default Tool Registry & Revalidation ─────────────────────────────────────

def get_default_tool_registry() -> Dict[str, Callable]:
    """Return mapping of tool name -> callable for all MCP server tools."""
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
    return {
        "get_order_details": get_order_details,
        "track_delivery_partner": track_delivery_partner,
        "request_refund_or_replacement": request_refund_or_replacement,
        "escalate_to_human_agent": escalate_to_human_agent,
        "get_order_history": get_order_history,
        "check_refund_status": check_refund_status,
        "update_delivery_instructions": update_delivery_instructions,
        "list_order_complaints": list_order_complaints,
    }


def default_revalidate_tool_call(tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Default revalidation function:
      1. Checks for prompt injection or malicious text in any string arguments.
      2. Validates tool exists in ExecutionRail.
      3. Validates that the tool parameters are well-formed.
    """
    from controlplane.responsibility import ResponsibilityEvaluator
    from controlplane.models import CheckStatus
    from controlplane.execution_rail import _TOOL_REGISTRY

    # 1. Check if tool is registered
    if tool_name not in _TOOL_REGISTRY:
        return {
            "passed": False,
            "reason": f"Tool '{tool_name}' is not a registered enterprise tool.",
        }

    # 2. Prompt injection / safety check on string values in args
    evaluator = ResponsibilityEvaluator()
    for k, v in tool_args.items():
        if isinstance(v, str):
            resp = evaluator.evaluate(v)
            if resp.status == CheckStatus.FAIL:
                fails = [c.reason for c in resp.checks if c.status == CheckStatus.FAIL]
                return {
                    "passed": False,
                    "reason": f"Security violation in parameter '{k}': {'; '.join(fails)}",
                }

    # 3. Numeric bounds validation (e.g. negative refund amounts)
    if tool_name == "request_refund_or_replacement":
        amt = tool_args.get("requested_amount")
        if amt is not None and amt <= 0:
            return {
                "passed": False,
                "reason": f"Invalid requested_amount ₹{amt}: must be positive.",
            }

    return {
        "passed": True,
        "reason": "Revalidation passed: tool registered, arguments safe, bounds valid.",
    }


# ── Approval Manager ─────────────────────────────────────────────────────────

class ApprovalManager:
    """
    High-level approval lifecycle manager.

    Integrates with the ControlPlane ExecutionRail and the MCP tool registry
    to provide exactly-once, revalidation-guarded human approval execution.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        tool_registry: Optional[Dict[str, Callable]] = None,
        revalidate_fn: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    ):
        self._repo = ApprovalRepository(db_path)
        self._tool_registry = tool_registry if tool_registry is not None else get_default_tool_registry()
        self._revalidate_fn = revalidate_fn or default_revalidate_tool_call
        self._db_path = db_path
        init_approval_table(db_path)

    def persist_pending(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        consequence_tier: str,
        decision: str,
        reason: str,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Persist a tool call that requires human approval.
        Returns the approval record with request_id.
        """
        request_id = f"approval-{uuid.uuid4().hex[:12]}"
        return self._repo.create(
            request_id=request_id,
            tool_name=tool_name,
            tool_args=tool_args,
            consequence_tier=consequence_tier,
            decision=decision,
            reason=reason,
            user_context=user_context,
        )

    def get_pending_requests(self) -> List[Dict[str, Any]]:
        """Return all pending approval requests."""
        rows = self._repo.get_all_pending()
        return [dict(r) for r in rows]

    def get_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Return a single approval request by ID."""
        row = self._repo.get_by_id(request_id)
        return dict(row) if row else None

    def approve(
        self,
        request_id: str,
        approved_by: str = "human_reviewer",
        revalidate_fn: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Approve and execute a pending request.

        Steps:
          1. Load the pending request.
          2. Verify it exists and is in PENDING state.
          3. Transition to EXECUTING (atomic, prevents double-execution).
          4. Revalidate: verify tool name + args hash hasn't changed.
          5. Re-run ControlPlane governance (if revalidate_fn provided).
          6. Execute the tool via the tool registry.
          7. Transition to EXECUTED with the result.

        Returns the execution result dict.
        """
        # 1. Load
        row = self._repo.get_by_id(request_id)
        if row is None:
            return {
                "success": False,
                "error": f"Approval request '{request_id}' not found.",
                "status": "NOT_FOUND",
            }

        record = dict(row)
        current_status = record["status"]

        # 2. Check state
        if current_status == ApprovalStatus.EXECUTED.value:
            return {
                "success": False,
                "error": f"Request '{request_id}' has already been executed.",
                "status": "ALREADY_EXECUTED",
            }
        if current_status == ApprovalStatus.REJECTED.value:
            return {
                "success": False,
                "error": f"Request '{request_id}' has been rejected and cannot be executed.",
                "status": "REJECTED",
            }
        if current_status == ApprovalStatus.EXECUTING.value:
            return {
                "success": False,
                "error": f"Request '{request_id}' is currently being executed.",
                "status": "EXECUTING",
            }
        if current_status != ApprovalStatus.PENDING.value:
            return {
                "success": False,
                "error": f"Request '{request_id}' is in unexpected state: {current_status}",
                "status": current_status,
            }

        # 3. Transition PENDING → EXECUTING (atomic)
        transitioned = self._repo.transition_to_executing(request_id)
        if not transitioned:
            # Race condition: another approver got there first
            return {
                "success": False,
                "error": f"Request '{request_id}' is no longer in PENDING state (race condition).",
                "status": "RACE_CONDITION",
            }

        # 4. Revalidate: verify args hash matches
        tool_name = record["tool_name"]
        tool_args = json.loads(record["tool_args_json"])
        stored_hash = record["args_hash"]
        computed_hash = _hash_args(tool_name, tool_args)

        if computed_hash != stored_hash:
            # Parameter tampering detected — rollback to REJECTED
            self._repo.set_revalidation_result(request_id, {
                "passed": False,
                "reason": "Parameter tampering detected: stored hash does not match computed hash",
            })
            # Force transition back: EXECUTING → (manual) REJECTED
            # We do this directly since the state machine doesn't have EXECUTING → REJECTED
            with _approval_session(self._db_path) as conn:
                conn.execute(
                    """UPDATE approval_requests
                       SET status = ?, resolved_at = ?, resolved_by = ?,
                           notes = ?
                       WHERE request_id = ?""",
                    (ApprovalStatus.REJECTED.value, _ts(), "system",
                     "REJECTED: parameter tampering detected", request_id),
                )
            return {
                "success": False,
                "error": "Parameter tampering detected. Stored arguments hash mismatch.",
                "status": "TAMPERING_DETECTED",
            }

        # 5. Re-run ControlPlane governance (revalidation)
        effective_revalidate = revalidate_fn if revalidate_fn is not None else self._revalidate_fn
        revalidation_result = {"passed": True, "reason": "No revalidation function provided"}
        if effective_revalidate is not None:
            try:
                revalidation_result = effective_revalidate(tool_name, tool_args)
                self._repo.set_revalidation_result(request_id, revalidation_result)
                if not revalidation_result.get("passed", False):
                    # Revalidation failed — reject
                    with _approval_session(self._db_path) as conn:
                        conn.execute(
                            """UPDATE approval_requests
                               SET status = ?, resolved_at = ?, resolved_by = ?,
                                   notes = ?
                               WHERE request_id = ?""",
                            (ApprovalStatus.REJECTED.value, _ts(), "system",
                             f"REJECTED: revalidation failed — {revalidation_result.get('reason', '')}",
                             request_id),
                        )
                    return {
                        "success": False,
                        "error": f"Revalidation failed: {revalidation_result.get('reason', '')}",
                        "status": "REVALIDATION_FAILED",
                    }
            except Exception as e:
                # Revalidation error → fail closed
                with _approval_session(self._db_path) as conn:
                    conn.execute(
                        """UPDATE approval_requests
                           SET status = ?, resolved_at = ?, resolved_by = ?,
                               notes = ?
                           WHERE request_id = ?""",
                        (ApprovalStatus.REJECTED.value, _ts(), "system",
                         f"REJECTED: revalidation exception — {e}", request_id),
                    )
                return {
                    "success": False,
                    "error": f"Revalidation failed with exception: {e}",
                    "status": "REVALIDATION_ERROR",
                }
        else:
            self._repo.set_revalidation_result(request_id, revalidation_result)

        # 6. Execute the tool
        tool_fn = self._tool_registry.get(tool_name)
        if tool_fn is None:
            with _approval_session(self._db_path) as conn:
                conn.execute(
                    """UPDATE approval_requests
                       SET status = ?, resolved_at = ?, resolved_by = ?,
                           notes = ?
                       WHERE request_id = ?""",
                    (ApprovalStatus.REJECTED.value, _ts(), "system",
                     f"REJECTED: tool '{tool_name}' not found in registry", request_id),
                )
            return {
                "success": False,
                "error": f"Tool '{tool_name}' not found in the tool registry.",
                "status": "TOOL_NOT_FOUND",
            }

        try:
            execution_result = tool_fn(**tool_args)
        except Exception as e:
            # Tool execution failed — mark as rejected, not executed
            with _approval_session(self._db_path) as conn:
                conn.execute(
                    """UPDATE approval_requests
                       SET status = ?, resolved_at = ?, resolved_by = ?,
                           execution_result = ?, notes = ?
                       WHERE request_id = ?""",
                    (ApprovalStatus.REJECTED.value, _ts(), "system",
                     json.dumps({"error": str(e)}, default=str),
                     f"REJECTED: tool execution failed — {e}", request_id),
                )
            return {
                "success": False,
                "error": f"Tool execution failed: {e}",
                "status": "EXECUTION_FAILED",
            }

        # 7. Transition EXECUTING → EXECUTED
        transitioned = self._repo.transition_to_executed(
            request_id, execution_result, resolved_by=approved_by
        )
        if not transitioned:
            return {
                "success": False,
                "error": "Failed to mark request as EXECUTED (unexpected state).",
                "status": "STATE_ERROR",
            }

        return {
            "success": True,
            "status": "EXECUTED",
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "execution_result": execution_result,
            "approved_by": approved_by,
        }

    def reject(
        self,
        request_id: str,
        rejected_by: str = "human_reviewer",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Reject a pending request. No tool execution occurs.

        Returns the rejection result dict.
        """
        row = self._repo.get_by_id(request_id)
        if row is None:
            return {
                "success": False,
                "error": f"Approval request '{request_id}' not found.",
                "status": "NOT_FOUND",
            }

        record = dict(row)
        current_status = record["status"]

        if current_status != ApprovalStatus.PENDING.value:
            return {
                "success": False,
                "error": f"Request '{request_id}' is in state '{current_status}' and cannot be rejected.",
                "status": current_status,
            }

        transitioned = self._repo.transition_to_rejected(
            request_id, resolved_by=rejected_by, notes=reason
        )
        if not transitioned:
            return {
                "success": False,
                "error": f"Failed to reject request '{request_id}' (race condition).",
                "status": "RACE_CONDITION",
            }

        return {
            "success": True,
            "status": "REJECTED",
            "request_id": request_id,
            "rejected_by": rejected_by,
            "reason": reason,
        }
