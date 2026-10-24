"""Session management utilities for claude-agent-cli."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from rich.table import Table
from claude_agent_sdk import SDKSessionInfo, list_sessions
from claude_agent_cli.config import load_config
from claude_agent_cli.ui import console


def get_sessions(directory: Optional[str] = None, limit: int = 15) -> list[SDKSessionInfo]:
    """Retrieve sessions for the given directory or current workspace."""
    target_dir = directory or str(Path.cwd())
    # Transcripts live in cagent's isolated claude_home, and list_sessions only reads os.environ.
    os.environ["CLAUDE_CONFIG_DIR"] = str(load_config().claude_home)
    try:
        return list_sessions(directory=target_dir, limit=limit)
    except Exception:
        return []


def render_sessions_table(sessions: list[SDKSessionInfo]) -> Table:
    """Render sessions list as a rich table."""
    table = Table(
        title="Saved Agent Sessions",
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Session ID", style="bold yellow", width=38)
    table.add_column("Created / Modified", style="dim", width=18)
    table.add_column("Branch", style="green", width=12)
    table.add_column("Summary / First Prompt", style="white", min_width=25)

    if not sessions:
        table.add_row("-", "-", "-", "No recorded sessions found in this workspace.")
        return table

    for s in sessions:
        ts = s.created_at or s.last_modified
        try:
            time_str = datetime.fromtimestamp(ts / 1000.0).strftime("%b %d %H:%M")
        except Exception:
            time_str = "-"

        summary = s.custom_title or s.first_prompt or s.summary or "Interactive conversation"
        summary = summary.strip().replace("\n", " ")
        if len(summary) > 45:
            summary = summary[:42] + "..."

        branch = s.git_branch or "-"
        table.add_row(s.session_id, time_str, branch, summary)

    return table
