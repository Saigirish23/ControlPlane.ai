"""
approval_cli.py — Human-in-the-loop CLI for ControlPlane.ai Approval Requests.

Provides interactive review, listing, approval, and rejection of high-consequence
tool calls intercepted by ControlPlane.

Usage:
  python3 -m support_agent_mcp.approval_cli list
  python3 -m support_agent_mcp.approval_cli approve <request_id> [--reviewer <name>]
  python3 -m support_agent_mcp.approval_cli reject <request_id> [--reviewer <name>] [--reason <text>]
  python3 -m support_agent_mcp.approval_cli show <request_id>
  python3 -m support_agent_mcp.approval_cli interactive
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from support_agent_mcp.approval import ApprovalManager, ApprovalStatus

console = Console()


def list_pending_requests(manager: Optional[ApprovalManager] = None) -> int:
    """Display all pending approval requests in a rich table."""
    mgr = manager or ApprovalManager()
    pending = mgr.get_pending_requests()

    if not pending:
        console.print(
            Panel(
                "[green]No pending approval requests.[/green]\n"
                "All high-consequence actions have been resolved or none are pending.",
                title="[bold]ControlPlane Approval Queue[/bold]",
                border_style="green",
            )
        )
        return 0

    table = Table(
        title=f"ControlPlane Pending Approvals ({len(pending)} pending)",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("Request ID", style="bold yellow")
    table.add_column("Tool Name", style="bold white")
    table.add_column("Key Parameters", style="white")
    table.add_column("Consequence", justify="center")
    table.add_column("Created At", style="dim")

    for req in pending:
        try:
            args = json.loads(req["tool_args_json"])
            args_summary = ", ".join(f"{k}={v}" for k, v in args.items())
            if len(args_summary) > 45:
                args_summary = args_summary[:42] + "..."
        except Exception:
            args_summary = req["tool_args_json"][:45]

        tier = req["consequence_tier"]
        tier_color = "bold red" if tier == "HIGH" else "bold yellow"

        table.add_row(
            req["request_id"],
            req["tool_name"],
            args_summary,
            f"[{tier_color}]{tier}[/{tier_color}]",
            req["created_at"][:19],
        )

    console.print(table)
    return len(pending)


def show_request_details(request_id: str, manager: Optional[ApprovalManager] = None) -> None:
    """Show detailed information for a specific approval request."""
    mgr = manager or ApprovalManager()
    req = mgr.get_request(request_id)
    if not req:
        console.print(f"[bold red]Error:[/bold red] Request ID '{request_id}' not found.")
        return

    status = req["status"]
    status_color = {
        "PENDING": "yellow",
        "EXECUTING": "cyan",
        "EXECUTED": "green",
        "REJECTED": "red",
    }.get(status, "white")

    info = [
        f"[bold]Request ID:[/bold]       {req['request_id']}",
        f"[bold]Tool Name:[/bold]        {req['tool_name']}",
        f"[bold]Status:[/bold]           [{status_color}]{status}[/{status_color}]",
        f"[bold]Consequence Tier:[/bold] {req['consequence_tier']}",
        f"[bold]Decision:[/bold]         {req['decision']}",
        f"[bold]Reason:[/bold]           {req['reason']}",
        f"[bold]Created At:[/bold]       {req['created_at']}",
        f"[bold]Arguments Hash:[/bold]   {req['args_hash'][:16]}...",
        f"[bold]Arguments JSON:[/bold]   {req['tool_args_json']}",
    ]

    if req.get("resolved_at"):
        info.append(f"[bold]Resolved At:[/bold]      {req['resolved_at']}")
    if req.get("resolved_by"):
        info.append(f"[bold]Resolved By:[/bold]      {req['resolved_by']}")
    if req.get("revalidation_result"):
        info.append(f"[bold]Revalidation:[/bold]     {req['revalidation_result']}")
    if req.get("execution_result"):
        info.append(f"[bold]Execution Result:[/bold] {req['execution_result']}")
    if req.get("notes"):
        info.append(f"[bold]Notes:[/bold]            {req['notes']}")

    console.print(
        Panel(
            "\n".join(info),
            title=f"[bold]Approval Request: {request_id}[/bold]",
            border_style=status_color,
        )
    )


def approve_request_cli(
    request_id: str,
    reviewer: str = "human_reviewer",
    manager: Optional[ApprovalManager] = None,
) -> bool:
    """Approve a pending request."""
    mgr = manager or ApprovalManager()
    console.print(f"[dim]Attempting approval for [bold]{request_id}[/bold] by {reviewer}...[/dim]")
    result = mgr.approve(request_id, approved_by=reviewer)

    if result.get("success"):
        console.print(
            Panel(
                f"[bold green]SUCCESS: Request {request_id} APPROVED and EXECUTED.[/bold green]\n\n"
                f"Tool: {result.get('tool_name')}\n"
                f"Status: {result.get('status')}\n"
                f"Execution Output: {json.dumps(result.get('execution_result', {}), indent=2)}",
                title="[bold green]Approval Succeeded[/bold green]",
                border_style="green",
            )
        )
        return True
    else:
        console.print(
            Panel(
                f"[bold red]FAILED: Approval rejected or blocked.[/bold red]\n\n"
                f"Error: {result.get('error')}\n"
                f"Status: {result.get('status')}",
                title="[bold red]Approval Error[/bold red]",
                border_style="red",
            )
        )
        return False


def reject_request_cli(
    request_id: str,
    reviewer: str = "human_reviewer",
    reason: Optional[str] = None,
    manager: Optional[ApprovalManager] = None,
) -> bool:
    """Reject a pending request."""
    mgr = manager or ApprovalManager()
    console.print(f"[dim]Rejecting [bold]{request_id}[/bold] by {reviewer}...[/dim]")
    result = mgr.reject(request_id, rejected_by=reviewer, reason=reason)

    if result.get("success"):
        console.print(
            Panel(
                f"[bold red]REQUEST REJECTED: {request_id}[/bold red]\n\n"
                f"Status: {result.get('status')}\n"
                f"Rejected By: {result.get('rejected_by')}\n"
                f"Reason: {result.get('reason') or 'No reason provided.'}\n\n"
                "[dim]No tool execution or database modification was performed.[/dim]",
                title="[bold red]Rejection Succeeded[/bold red]",
                border_style="red",
            )
        )
        return True
    else:
        console.print(
            Panel(
                f"[bold red]FAILED to reject request.[/bold red]\n\n"
                f"Error: {result.get('error')}\n"
                f"Status: {result.get('status')}",
                title="[bold red]Rejection Error[/bold red]",
                border_style="red",
            )
        )
        return False


def run_interactive_session(manager: Optional[ApprovalManager] = None) -> None:
    """Run an interactive approval queue review session."""
    mgr = manager or ApprovalManager()
    console.print(
        Panel(
            "[bold cyan]ControlPlane.ai — Human Approval Review Console[/bold cyan]\n"
            "Review and resolve high-consequence pending tool calls.",
            border_style="cyan",
        )
    )

    while True:
        count = list_pending_requests(mgr)
        if count == 0:
            console.print("\n[dim]Queue is empty. Exiting interactive mode.[/dim]")
            break

        console.print("\n[bold]Actions:[/bold] [A]pprove, [R]eject, [S]how Details, [Q]uit")
        choice = input("Enter choice [A/R/S/Q]: ").strip().upper()

        if choice == "Q":
            break
        elif choice in ("A", "R", "S"):
            req_id = input("Enter Request ID: ").strip()
            if choice == "S":
                show_request_details(req_id, mgr)
            elif choice == "A":
                reviewer = input("Enter your reviewer name [default: supervisor]: ").strip() or "supervisor"
                approve_request_cli(req_id, reviewer=reviewer, manager=mgr)
            elif choice == "R":
                reviewer = input("Enter your reviewer name [default: supervisor]: ").strip() or "supervisor"
                reason = input("Enter rejection reason: ").strip()
                reject_request_cli(req_id, reviewer=reviewer, reason=reason, manager=mgr)
        else:
            console.print("[yellow]Invalid option. Please choose A, R, S, or Q.[/yellow]")


def main() -> None:
    parser = argparse.ArgumentParser(description="ControlPlane.ai Approval Review CLI")
    subparsers = parser.add_subparsers(dest="command")

    # list
    subparsers.add_parser("list", help="List all pending approval requests")

    # show
    show_parser = subparsers.add_parser("show", help="Show details of a request")
    show_parser.add_argument("request_id", help="The request ID to inspect")

    # approve
    approve_parser = subparsers.add_parser("approve", help="Approve and execute a request")
    approve_parser.add_argument("request_id", help="The request ID to approve")
    approve_parser.add_argument("--reviewer", default="human_reviewer", help="Reviewer identifier")

    # reject
    reject_parser = subparsers.add_parser("reject", help="Reject a request")
    reject_parser.add_argument("request_id", help="The request ID to reject")
    reject_parser.add_argument("--reviewer", default="human_reviewer", help="Reviewer identifier")
    reject_parser.add_argument("--reason", default="Rejected by administrator", help="Rejection reason")

    # interactive
    subparsers.add_parser("interactive", help="Run interactive review console")

    args = parser.parse_args()

    if args.command == "list" or args.command is None:
        list_pending_requests()
    elif args.command == "show":
        show_request_details(args.request_id)
    elif args.command == "approve":
        approve_request_cli(args.request_id, reviewer=args.reviewer)
    elif args.command == "reject":
        reject_request_cli(args.request_id, reviewer=args.reviewer, reason=args.reason)
    elif args.command == "interactive":
        run_interactive_session()


if __name__ == "__main__":
    main()
