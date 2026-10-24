"""Interactive tool permission management for claude-agent-cli."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Optional, Set

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ToolPermissionContext,
)
from claude_agent_sdk.types import PermissionRuleValue
from prompt_toolkit.shortcuts.choice_input import ChoiceInput
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from claude_agent_cli.ui import console, pause_live, resume_live, screen_lock, start_spinner, stop_spinner


def normalize_permission_mode(mode: Optional[str]) -> str:
    """Normalize user-friendly permission mode names to SDK literals."""
    if not mode:
        return "acceptEdits"
    cleaned = mode.strip()
    if cleaned in ("bypass", "bypassPermissions", "yes", "all", "force"):
        return "bypassPermissions"
    if cleaned in ("ask", "default", "interactive", "prompt"):
        return "default"
    if cleaned in ("acceptEdits", "accept_edits", "edits"):
        return "acceptEdits"
    if cleaned in ("auto",):
        return "auto"
    if cleaned in ("dontAsk", "dont_ask"):
        return "dontAsk"
    if cleaned in ("plan",):
        return "plan"
    return cleaned


def _format_tool_details(name: str, tool_input: dict[str, Any]) -> str:
    """Format input preview for permission display."""
    if name == "Bash" and "command" in tool_input:
        return f"command: {tool_input['command']}"
    if name in ("Write", "Edit", "Read") and "file_path" in tool_input:
        return f"file: {tool_input['file_path']}"
    if name in ("Grep", "Glob") and "pattern" in tool_input:
        return f"pattern: {tool_input['pattern']}"
    return json.dumps(tool_input, default=str)[:120]


class _ToolWideRule(PermissionUpdate):
    """PermissionUpdate whose rules may cover a whole tool."""

    def to_dict(self) -> dict[str, Any]:
        # shortcut: the SDK sends rule_content=None as "ruleContent": null and the CLI drops the rule; remove once the SDK omits it.
        data = super().to_dict()
        data["rules"] = [{k: v for k, v in rule.items() if v is not None} for rule in data["rules"]]
        return data


async def _ask(tool_name: str) -> str:
    """Arrow-key or number choice; returns y, a, d or c."""
    app = ChoiceInput(
        message=f"Allow {tool_name}?",
        options=[
            ("y", "Yes"),
            ("a", f"Yes, and don't ask again for {tool_name} this session"),
            ("d", "No"),
            ("c", "No, and stop this turn"),
        ],
        default="y",
    )._create_application()
    # shortcut: private API, ChoiceInput has no option to clear the menu once answered; drop if it gains one.
    app.erase_when_done = True
    return await app.run_async()


def create_permission_callback(
    permission_mode: str | Callable[[], str] = "acceptEdits",
    is_interactive: bool = True,
) -> Callable:
    """Build an async permission callback for ClaudeAgentOptions.can_use_tool."""
    session_allowed_tools: Set[str] = set()

    def _get_current_mode() -> str:
        if callable(permission_mode):
            return normalize_permission_mode(permission_mode())
        return normalize_permission_mode(permission_mode)

    async def can_use_tool_callback(
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        norm_mode = _get_current_mode()

        # Check session-approved tools
        if tool_name in session_allowed_tools:
            return PermissionResultAllow()

        # No bypassPermissions branch: the CLI already allows everything then and only calls back
        # when a hook or ask rule demands a prompt, such as a hook that confirms outbound posts.

        # Mode: acceptEdits -> auto-allow standard code edits & read operations
        if norm_mode == "acceptEdits":
            if tool_name in ("Edit", "Write", "Read", "Grep", "Glob", "Skill"):
                return PermissionResultAllow()

        # Mode: dontAsk -> auto-deny any unapproved action
        if norm_mode == "dontAsk":
            return PermissionResultDeny(
                message=f"Mode is dontAsk: tool '{tool_name}' requires permission and was automatically denied.",
                interrupt=False,
            )

        # Mode: plan -> allow read-only tools, deny modifications
        if norm_mode == "plan":
            if tool_name in ("Read", "Grep", "Glob"):
                return PermissionResultAllow()
            return PermissionResultDeny(
                message=f"Plan mode is active: tool '{tool_name}' cannot make modifications.",
                interrupt=False,
            )

        # Non-interactive fallback: If not a TTY or running in batch mode
        if not is_interactive or not sys.stdin.isatty():
            return PermissionResultDeny(
                message=f"Non-interactive environment: tool '{tool_name}' requires permission but terminal input is unavailable.",
                interrupt=False,
            )

        async with screen_lock:
            # Another prompt may have answered "always" for this tool while we waited.
            if tool_name in session_allowed_tools:
                return PermissionResultAllow()
            # A subagent can ask while the main reply is still streaming; keep it off the menu.
            pause_live()
            try:
                return await _prompt(tool_name, tool_input, context)
            finally:
                resume_live()

    async def _prompt(
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        stop_spinner()

        # Interactive Terminal Prompt
        details = _format_tool_details(tool_name, tool_input)
        reason = context.decision_reason or context.title or context.description or "Action requires confirmation"

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", width=12)
        grid.add_column(style="white")

        grid.add_row("Tool:", f"[bold white]{tool_name}[/bold white]")
        grid.add_row("Action:", f"[yellow]{escape(details)}[/yellow]")
        if reason:
            grid.add_row("Reason:", f"[dim]{escape(reason)}[/dim]")

        panel = Panel(
            grid,
            title="[bold yellow]🔒 Tool Permission Request[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        )

        console.print()
        console.print(panel)

        try:
            choice = await _ask(tool_name)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Permission prompt cancelled.[/yellow]")
            return PermissionResultDeny(message="Permission prompt cancelled by user.", interrupt=True)

        if choice == "y":
            console.print(f"[dim]Allowed {tool_name}.[/dim]")
            start_spinner(f"Running {tool_name}…")
            return PermissionResultAllow()

        if choice == "a":
            session_allowed_tools.add(tool_name)
            console.print(f"[dim]Allowing {tool_name} for the rest of this session.[/dim]")
            start_spinner(f"Running {tool_name}…")
            # Tell the CLI too, or it keeps asking and every call fires PermissionRequest hooks.
            rule = _ToolWideRule(
                type="addRules", rules=[PermissionRuleValue(tool_name)], behavior="allow", destination="session"
            )
            return PermissionResultAllow(updated_permissions=[rule])

        if choice == "c":
            console.print("[yellow]✖ Turn stopped.[/yellow]")
            return PermissionResultDeny(message="Turn cancelled by user.", interrupt=True)

        console.print(f"[red]✖ Denied {tool_name}.[/red]")
        start_spinner()
        return PermissionResultDeny(message="User denied permission to execute this tool.", interrupt=False)

    return can_use_tool_callback
