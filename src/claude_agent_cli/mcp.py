"""MCP (Model Context Protocol) server discovery and configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich.table import Table

from claude_agent_cli.config import DEFAULT_CONFIG_DIR


def get_mcp_sources(project_root: Optional[Path] = None) -> list[tuple[str, Path]]:
    """Get list of potential MCP config file locations."""
    home = Path.home()
    sources = [
        # 1. Antigravity / Gemini MCP config
        ("Antigravity MCP", home / ".gemini" / "config" / "mcp_config.json"),
        # 2. Claude Code global settings
        ("Claude Global Settings", home / ".claude" / "settings.json"),
        # 3. cagent custom MCP config
        ("cagent Custom Config", DEFAULT_CONFIG_DIR / "mcp.json"),
    ]

    # 4. Project-level configs
    if project_root:
        sources.append(("Project .mcp.json", project_root / ".mcp.json"))
        sources.append(("Project Claude Settings", project_root / ".claude" / "settings.json"))

    return sources


def discover_all_mcp_servers(project_root: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Discover all MCP servers across system configs and current workspace."""
    servers: dict[str, dict[str, Any]] = {}

    for label, config_path in get_mcp_sources(project_root):
        if not config_path.exists():
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Find servers block (either 'mcpServers' or root dict)
            mcp_block = data.get("mcpServers", data if "mcpServers" not in data else {})
            if isinstance(mcp_block, dict):
                for name, raw_cfg in mcp_block.items():
                    if not isinstance(raw_cfg, dict):
                        continue
                    # Skip if already discovered from higher priority or explicit override
                    if name not in servers:
                        server_info = dict(raw_cfg)
                        server_info["_source"] = label
                        server_info["_source_path"] = str(config_path)
                        servers[name] = server_info
        except Exception:
            pass

    return servers


def get_active_mcp_servers(
    filter_names: Optional[list[str]] = None,
    project_root: Optional[Path] = None,
    exclude_disabled: bool = True,
) -> dict[str, dict[str, Any]]:
    """Get sanitized MCP server configurations ready for ClaudeAgentOptions."""
    discovered = discover_all_mcp_servers(project_root)
    active: dict[str, dict[str, Any]] = {}

    for name, s in discovered.items():
        if exclude_disabled and s.get("disabled", False):
            continue

        if filter_names is not None:
            if "all" not in filter_names and name not in filter_names:
                continue

        # Strip internal metadata fields before sending to SDK
        clean_cfg = {k: v for k, v in s.items() if not k.startswith("_")}
        active[name] = clean_cfg

    return active


def render_mcp_table(servers: dict[str, dict[str, Any]]) -> Table:
    """Generate a formatted table of all discovered MCP servers."""
    table = Table(
        title="Configured MCP Servers",
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Server Name", style="bold green", width=18)
    table.add_column("Command / Executable", style="white", min_width=25)
    table.add_column("Args / Details", style="dim", width=25)
    table.add_column("Source", style="cyan", width=22)

    if not servers:
        table.add_row("-", "No MCP servers configured", "-", "-")
        return table

    for name in sorted(servers.keys()):
        s = servers[name]
        cmd = s.get("command", s.get("url", "-"))
        args_list = s.get("args", [])
        args_str = " ".join(args_list) if args_list else "-"
        if len(args_str) > 30:
            args_str = args_str[:27] + "..."

        source = s.get("_source", "Unknown")
        disabled = s.get("disabled", False)
        status = "[red](disabled)[/red]" if disabled else ""

        table.add_row(f"{name} {status}".strip(), str(cmd), args_str, source)

    return table
