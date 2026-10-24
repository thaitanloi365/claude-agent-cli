"""Usage and spend tracker for claude-agent-cli."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_agent_cli.config import DEFAULT_CONFIG_DIR, ensure_config_dirs

USAGE_FILE = DEFAULT_CONFIG_DIR / "usage.json"
# The real balance is not readable with a normal API key, so point at the Console instead.
BILLING_URL = "https://platform.claude.com/settings/billing"


class QueryUsage(BaseModel):
    """Usage statistics for a single query or turn."""

    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    session_id: Optional[str] = None
    model: str = "claude-3-7-sonnet"
    agent: str = "coder"
    prompt_preview: str = ""
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0


class UsageLedger(BaseModel):
    """Cumulative usage history."""

    records: List[QueryUsage] = Field(default_factory=list)


def load_ledger() -> UsageLedger:
    """Load usage history from disk."""
    ensure_config_dirs()
    if USAGE_FILE.exists():
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return UsageLedger(**data)
        except Exception:
            return UsageLedger()
    return UsageLedger()


def save_ledger(ledger: UsageLedger) -> None:
    """Save usage history to disk."""
    ensure_config_dirs()
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(ledger.model_dump(mode="json"), f, indent=2)


def record_usage(
    *,
    session_id: Optional[str],
    model: str,
    agent: str,
    prompt: str,
    duration_ms: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cost_usd: float = 0.0,
) -> QueryUsage:
    """Record a completed query turn into the ledger."""
    ledger = load_ledger()
    preview = prompt.strip().replace("\n", " ")
    if len(preview) > 60:
        preview = preview[:57] + "..."

    entry = QueryUsage(
        session_id=session_id,
        model=model,
        agent=agent,
        prompt_preview=preview,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cost_usd=cost_usd,
    )
    ledger.records.append(entry)
    save_ledger(ledger)
    return entry


def get_total_spent() -> float:
    """Calculate total USD spent across all recorded queries."""
    ledger = load_ledger()
    return sum(r.cost_usd for r in ledger.records)


def get_session_spent(session_id: str) -> float:
    """USD recorded for one session, e.g. to seed the running total on resume."""
    return sum(r.cost_usd for r in load_ledger().records if r.session_id == session_id)


def get_month_spent() -> float:
    """USD spent by cagent since the start of the current calendar month."""
    month = datetime.now().strftime("%Y-%m")
    return sum(r.cost_usd for r in load_ledger().records if r.timestamp.startswith(month))


def get_token_totals() -> dict[str, int]:
    """Calculate aggregate token consumption."""
    ledger = load_ledger()
    return {
        "input_tokens": sum(r.input_tokens for r in ledger.records),
        "output_tokens": sum(r.output_tokens for r in ledger.records),
        "cache_read_tokens": sum(r.cache_read_tokens for r in ledger.records),
        "cache_creation_tokens": sum(r.cache_creation_tokens for r in ledger.records),
        "total_tokens": sum(
            r.input_tokens + r.output_tokens + r.cache_read_tokens + r.cache_creation_tokens
            for r in ledger.records
        ),
    }


def create_credit_dashboard() -> Panel:
    """Generate a rich Panel with spend recorded by cagent."""
    tokens = get_token_totals()

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold white", justify="left")
    grid.add_column(justify="left")

    grid.add_row("This Month:", f"[bold]${get_month_spent():.4f} USD[/bold]")
    grid.add_row("All Time:", f"${get_total_spent():.4f} USD")
    grid.add_row(
        "Token Usage:",
        f"Input: [dim]{tokens['input_tokens']:,}[/dim] | Output: [dim]{tokens['output_tokens']:,}[/dim] | Cache: [dim]{tokens['cache_read_tokens']:,}[/dim]",
    )
    grid.add_row("", "")
    grid.add_row("Real Balance:", f"[cyan]{BILLING_URL}[/cyan]")
    grid.add_row("", "[dim]Promotional credits are spent first, then Credit balance.[/dim]")
    grid.add_row("", "[dim]Other apps on the same Console org draw from the same balance.[/dim]")

    return Panel(
        grid,
        title="[bold blue]cagent spend[/bold blue]",
        border_style="blue",
        subtitle=f"[dim]{len(load_ledger().records)} queries logged[/dim]",
    )


def create_recent_activity_table(limit: int = 8) -> Table:
    """Generate a table showing recent queries and costs."""
    ledger = load_ledger()
    records = list(reversed(ledger.records))[:limit]

    table = Table(
        title="Recent Activity",
        border_style="dim",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Time", style="dim", width=16)
    table.add_column("Agent", style="cyan", width=10)
    table.add_column("Prompt Preview", style="white", min_width=25)
    table.add_column("Tokens (In/Out)", justify="right", style="dim", width=16)
    table.add_column("Cost", justify="right", style="bold green", width=10)

    if not records:
        table.add_row("-", "-", "No queries logged yet", "-", "$0.0000")
        return table

    for r in records:
        try:
            dt = datetime.fromisoformat(r.timestamp)
            time_str = dt.strftime("%b %d %H:%M")
        except Exception:
            time_str = r.timestamp[:16]

        tokens_str = f"{r.input_tokens:,}/{r.output_tokens:,}"
        cost_str = f"${r.cost_usd:.4f}"
        table.add_row(time_str, r.agent, r.prompt_preview, tokens_str, cost_str)

    return table
