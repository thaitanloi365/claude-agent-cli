"""In-process SDK MCP tools for claude-agent-cli."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict
from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_cli.usage import BILLING_URL, get_month_spent, get_total_spent, load_ledger


@tool(
    "check_credit_balance",
    "Check how much cagent has spent in USD and where the user can see their real Anthropic credit balance.",
    {},
)
async def check_credit_balance_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Return spend recorded by cagent; the real balance lives in the Console."""
    ledger = load_ledger()

    text = (
        f"Spent by cagent this month: ${get_month_spent():.4f} USD\n"
        f"Spent by cagent all time: ${get_total_spent():.4f} USD\n"
        f"Queries Recorded: {len(ledger.records)}\n"
        f"Real balance (Promotional credits, then Credit balance): {BILLING_URL}"
    )
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "get_workspace_summary",
    "Get git branch, status, and repository overview of the current workspace.",
    {},
)
async def get_workspace_summary_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Return git summary for current workspace."""
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        branch = "unknown / non-git"

    try:
        status = subprocess.check_output(
            ["git", "status", "--short"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        status = "unavailable"

    text = f"Current Git Branch: {branch}\nGit Status:\n{status if status else 'clean (no changes)'}"
    return {"content": [{"type": "text", "text": text}]}


def create_inprocess_mcp_server() -> Any:
    """Create the SDK in-process MCP server with custom tools."""
    return create_sdk_mcp_server(
        name="cagent-system-tools",
        version="1.0.0",
        tools=[check_credit_balance_tool, get_workspace_summary_tool],
    )
