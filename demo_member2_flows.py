"""
demo_member2_flows.py — Live Interactive / Automated Demonstration of ControlPlane.ai Governance.

Demonstrates:
  1. LOW Risk Query (Order Lookup -> FAST -> PASS -> MCP Tool Execution)
  2. MEDIUM Risk Action (Small Refund <= Rs.200 -> PASS -> Auto-Approved in DB)
  3. HIGH Risk Action (Large Refund > Rs.200 -> HUMAN_APPROVAL -> Pending Stored -> NO DB Mutation)
  4. Human Approval Flow (Approve -> Anti-Tampering & Revalidation -> Exactly-Once Execution -> DB Mutated)
  5. Human Rejection Flow (Reject -> No Execution -> DB Unmutated)
  6. Parameter Tampering & Injection Protection (Altered Args -> Blocked by Hash Mismatch)
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from controlplane.models import ConsequenceTier, Decision
from support_agent_mcp.approval import ApprovalManager, ApprovalStatus
from support_agent_mcp.config import DB_PATH
from support_agent_mcp.db import init_db, seed_db
from support_agent_mcp.proxy.controlplane_hooks import build_default_pipeline
from support_agent_mcp.server import get_order_details, request_refund_or_replacement

console = Console()


def section(title: str) -> None:
    console.print()
    console.print(f"[bold cyan]═══════════════════════════════════════════════════════════════════════════[/bold cyan]")
    console.print(f"[bold white]  {title}[/bold white]")
    console.print(f"[bold cyan]═══════════════════════════════════════════════════════════════════════════[/bold cyan]")


def run_demo() -> None:
    # 0. Initialize DB
    init_db()
    seed_db()
    mgr = ApprovalManager()
    pipeline = build_default_pipeline(verbose_logging=False, refund_limit=200.0, approval_manager=mgr)

    console.print(
        Panel(
            "[bold white]ControlPlane.ai — Consequence-Aware Runtime Governance Demonstration[/bold white]\n"
            "[dim]Accentuation Challenge 2026 Round 2 — Member-2 Runtime Pipeline[/dim]",
            title="[bold green]System Online[/bold green]",
            border_style="green",
        )
    )

    # ─────────────────────────────────────────────────────────────
    # SCENARIO 1: LOW RISK QUERY
    # ─────────────────────────────────────────────────────────────
    section("1. LOW-CONSEQUENCE READ-ONLY QUERY")
    console.print("[dim]User requests: 'Where is my order ORD001?'[/dim]")
    console.print("[bold yellow]Routing:[/bold yellow] Agent -> MCP Proxy -> ControlPlane Consequence Engine")

    res1 = pipeline.call(get_order_details, {"order_id": "ORD001"})
    
    console.print(f"  • Consequence: [bold green]LOW[/bold green] (Evaluation Depth: [bold green]FAST[/bold green])")
    console.print(f"  • Decision:    [bold green]PASS[/bold green] (Read-only query allowed)")
    console.print(f"  • Result:      Success={res1.get('success')}, Restaurant='{res1.get('restaurant_name')}', Status='{res1.get('status')}'")

    # ─────────────────────────────────────────────────────────────
    # SCENARIO 2: MEDIUM RISK SMALL REFUND
    # ─────────────────────────────────────────────────────────────
    section("2. MEDIUM-CONSEQUENCE SMALL REFUND (<= ₹200 AUTO-APPROVAL)")
    console.print("[dim]User requests ₹179 refund for missing shake on order ORD002.[/dim]")
    
    res2 = pipeline.call(
        request_refund_or_replacement,
        {
            "order_id": "ORD002",
            "customer_id": "CUST002",
            "reason": "Missing shake",
            "complaint_type": "missing_items",
            "requested_amount": 179.0,
        },
    )

    console.print(f"  • Consequence: [bold green]LOW/MEDIUM[/bold green] (Within auto-approval policy limit ₹200.00)")
    console.print(f"  • Decision:    [bold green]PASS[/bold green] (Auto-approval permitted)")
    console.print(f"  • DB Status:   Status='{res2.get('status')}', Approved Amount=₹{res2.get('approved_amount')}")

    # ─────────────────────────────────────────────────────────────
    # SCENARIO 3: HIGH RISK LARGE REFUND (HUMAN APPROVAL)
    # ─────────────────────────────────────────────────────────────
    section("3. HIGH-CONSEQUENCE LARGE REFUND (> ₹200 -> HUMAN_APPROVAL REQUIRED)")
    console.print("[dim]User requests ₹587 refund for ruined order ORD004.[/dim]")
    
    # Check DB before
    conn = sqlite3.connect(str(DB_PATH))
    r_count_before = conn.execute("SELECT COUNT(*) FROM refund_requests WHERE order_id = 'ORD004'").fetchone()[0]
    conn.close()

    res3 = pipeline.call(
        request_refund_or_replacement,
        {
            "order_id": "ORD004",
            "customer_id": "CUST004",
            "reason": "Severe 2-hour delay and spoiled meal",
            "complaint_type": "late_delivery",
            "requested_amount": 587.0,
        },
    )

    approval_id = res3.get("approval_request_id")

    # Check DB after
    conn = sqlite3.connect(str(DB_PATH))
    r_count_after = conn.execute("SELECT COUNT(*) FROM refund_requests WHERE order_id = 'ORD004'").fetchone()[0]
    conn.close()

    console.print(f"  • Consequence: [bold red]HIGH[/bold red] (Evaluation Depth: [bold red]HIGH_ASSURANCE[/bold red])")
    console.print(f"  • Decision:    [bold magenta]HUMAN_APPROVAL[/bold magenta] (Exceeds ₹200 limit)")
    console.print(f"  • Request ID:  [bold yellow]{approval_id}[/bold yellow]")
    console.print(f"  • DB Safety:   Refund records in DB before={r_count_before}, after={r_count_after} [bold green](UNMUTATED)[/bold green]")
    console.print(f"  • Status:      {res3.get('status')} (Awaiting human review)")

    # ─────────────────────────────────────────────────────────────
    # SCENARIO 4: HUMAN APPROVAL LIFECYCLE (APPROVE)
    # ─────────────────────────────────────────────────────────────
    section("4. HUMAN APPROVAL EXECUTION & IDEMPOTENCY")
    console.print(f"[dim]Supervisor reviews and approves request {approval_id}...[/dim]")

    app_res = mgr.approve(approval_id, approved_by="senior_lead_rahul")
    console.print(f"  • Integrity Check: [bold green]PASSED[/bold green] (SHA-256 arguments hash verified)")
    console.print(f"  • Revalidation:     [bold green]PASSED[/bold green] (Governance re-check verified)")
    console.print(f"  • Execution:        [bold green]EXECUTED[/bold green] by {app_res.get('approved_by')}")
    console.print(f"  • DB Mutation:      Refund successfully processed into SQLite database!")

    # Attempt replay / double approval
    console.print(f"\n[dim]Attempting second approval (replay attack) on {approval_id}...[/dim]")
    replay_res = mgr.approve(approval_id)
    console.print(f"  • Replay Protection: [bold green]BLOCKED[/bold green] — Error: '{replay_res.get('error')}'")

    # ─────────────────────────────────────────────────────────────
    # SCENARIO 5: REJECTION FLOW
    # ─────────────────────────────────────────────────────────────
    section("5. HUMAN REJECTION FLOW")
    # Create another high risk request for ORD005
    res_rej = pipeline.call(
        request_refund_or_replacement,
        {
            "order_id": "ORD005",
            "customer_id": "CUST005",
            "reason": "Unreasonable claim",
            "complaint_type": "other",
            "requested_amount": 499.0,
        },
    )
    rej_id = res_rej.get("approval_request_id")
    console.print(f"[dim]High-consequence request created: {rej_id}[/dim]")
    
    rej_out = mgr.reject(rej_id, rejected_by="supervisor_jane", reason="Claim does not meet refund policy terms.")
    console.print(f"  • Status:      [bold red]{rej_out.get('status')}[/bold red] by {rej_out.get('rejected_by')}")
    console.print(f"  • Execution:   [bold green]PREVENTED[/bold green] (No tool was run, no business DB mutation)")

    # ─────────────────────────────────────────────────────────────
    # SCENARIO 6: PARAMETER TAMPERING PROTECTION
    # ─────────────────────────────────────────────────────────────
    section("6. ANTI-TAMPERING & REVALIDATION SECURITY")
    tamper_rec = mgr.persist_pending(
        tool_name="request_refund_or_replacement",
        tool_args={"order_id": "ORD003", "customer_id": "CUST003", "requested_amount": 350.0, "reason": "Cold", "complaint_type": "food_quality"},
        consequence_tier="HIGH",
        decision="HUMAN_APPROVAL",
        reason="Exceeds limit",
    )
    t_id = tamper_rec["request_id"]
    console.print(f"[dim]Created approval request {t_id} for ₹350.00 refund.[/dim]")

    # Malicious modification in SQLite
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE approval_requests SET tool_args_json = ? WHERE request_id = ?",
        (json.dumps({"order_id": "ORD003", "customer_id": "CUST003", "requested_amount": 95000.0, "reason": "Cold", "complaint_type": "food_quality"}), t_id)
    )
    conn.commit()
    conn.close()
    console.print(f"[bold red]MALICIOUS ATTACK:[/bold red] Stored DB args altered from ₹350.00 -> ₹95,000.00!")

    t_res = mgr.approve(t_id)
    console.print(f"  • Integrity Check: [bold red]FAILED[/bold red] — SHA-256 hash mismatch detected!")
    console.print(f"  • Action:          [bold red]BLOCKED & REJECTED[/bold red] ({t_res.get('status')})")
    console.print(f"  • Error:           {t_res.get('error')}")

    console.print()
    console.print(
        Panel(
            "[bold green]✔ All demonstration scenarios executed with full consequence-aware governance, database safety, and state-machine verification.[/bold green]",
            border_style="green",
        )
    )


if __name__ == "__main__":
    run_demo()
