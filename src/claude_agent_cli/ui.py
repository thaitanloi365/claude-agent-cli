"""Rich terminal UI formatting for claude-agent-cli."""

from __future__ import annotations

import asyncio
import difflib
import json
import sys
from typing import Any, Optional
from rich.console import Console, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.status import Status
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from claude_agent_cli.usage import BILLING_URL, QueryUsage

console = Console()
err_console = Console(stderr=True)

RESULT_PREVIEW_LINES = 4
DIFF_PREVIEW_LINES = 30

# Held while a permission menu is open and while an SDK message is drawn, so neither draws over the other.
screen_lock = asyncio.Lock()

_spinner: Optional[Status] = None
_live: Optional[Live] = None
_paused: Optional[RenderableType] = None


def start_spinner(text: str = "Thinking…") -> None:
    """Show a spinner until the next output; no-op when piped or while a reply streams."""
    global _spinner
    if not console.is_terminal or _live is not None:
        return
    label = f"[dim]{text}[/dim]"
    if _spinner is None:
        _spinner = console.status(label, spinner="dots")
        _spinner.start()
    else:
        _spinner.update(label)


def stop_spinner() -> None:
    """Remove the spinner so raw output or a prompt can take the screen."""
    global _spinner
    if _spinner is not None:
        _spinner.stop()
        _spinner = None


def start_live(renderable: RenderableType) -> None:
    """Redraw renderable in place until stop_live; the spinner gives way to it."""
    global _live, _paused
    _paused = None
    stop_spinner()
    _live = Live(renderable, console=console, transient=True, refresh_per_second=12, vertical_overflow="visible")
    _live.start()


def stop_live() -> None:
    """Clear the live area; the caller prints the final content."""
    global _live, _paused
    _paused = None
    if _live is not None:
        _live.stop()
        _live = None


def pause_live() -> None:
    """Hide a streaming reply so a prompt can use the screen; resume_live brings it back."""
    global _paused
    if _live is not None:
        renderable = _live.renderable
        stop_live()
        _paused = renderable


def resume_live() -> None:
    """Redraw a reply hidden by pause_live, if any."""
    if _paused is not None:
        start_live(_paused)


def print_markdown(text: str) -> None:
    """Render a reply as markdown in a terminal, verbatim when piped."""
    if console.is_terminal:
        console.print(Markdown(text))
    else:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


def print_banner(model: str, active_agent: str, permission_mode: str, cwd: str) -> None:
    """Print the startup welcome banner."""
    info_grid = Table.grid(padding=(0, 2))
    info_grid.add_column(style="bold white")
    info_grid.add_column()

    info_grid.add_row("Model:", f"[magenta]{model}[/magenta]")
    info_grid.add_row("Agent:", f"[cyan]{active_agent}[/cyan]")
    info_grid.add_row("Permission:", f"[yellow]{permission_mode}[/yellow]")
    info_grid.add_row("Workspace:", f"[dim]{cwd}[/dim]")
    info_grid.add_row("Balance:", f"[dim]{BILLING_URL}[/dim]")

    panel = Panel(
        info_grid,
        title="[bold cyan]cagent[/bold cyan] [dim]· Claude Agent SDK[/dim]",
        subtitle="[dim]/ commands · @ files · shift+tab mode · Esc interrupts · \\+Enter new line[/dim]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)


def print_turn_cost(usage: QueryUsage, session_spent: Optional[float] = None) -> None:
    """Display a one-line cost footer after a turn."""
    parts = [
        f"${usage.cost_usd:.4f}",
        f"{usage.input_tokens:,} in / {usage.output_tokens:,} out",
    ]
    if usage.cache_read_tokens > 0:
        parts.append(f"{usage.cache_read_tokens:,} cached")
    parts.append(f"{usage.duration_ms / 1000.0:.1f}s")
    if session_spent is not None:
        parts.append(f"session ${session_spent:.4f}")

    console.print(Text("✻ " + " · ".join(parts), style="dim"))
    console.print()


def _tool_preview(args: dict[str, Any]) -> str:
    """Pick the argument a reader needs to recognise the call, as one line."""
    for key in ("command", "file_path", "pattern", "url", "query", "description", "skill", "prompt"):
        value = args.get(key)
        if value:
            lines = str(value).strip().splitlines()
            return lines[0] + (" …" if len(lines) > 1 else "")
    return json.dumps(args, default=str)


def print_tool_call(name: str, args: dict[str, Any]) -> None:
    """Display a tool invocation, with a diff for edits."""
    line = Text.assemble(("● ", "green"), (name, "bold"), (f"({_tool_preview(args)})", "dim"))
    console.print(line, no_wrap=True, overflow="ellipsis")

    if name == "Edit" and "old_string" in args:
        diff = list(
            difflib.unified_diff(
                str(args["old_string"]).splitlines(),
                str(args.get("new_string", "")).splitlines(),
                lineterm="",
                n=1,
            )
        )[2:]  # drop the ---/+++ header
        if len(diff) > DIFF_PREVIEW_LINES:
            diff = diff[:DIFF_PREVIEW_LINES] + [f"… +{len(diff) - DIFF_PREVIEW_LINES} lines"]
        if diff:
            syntax = Syntax("\n".join(diff), "diff", theme="ansi_dark", background_color="default")
            console.print(Padding(syntax, (0, 0, 0, 4)))


def print_tool_result(name: str, result_text: str, is_error: bool = False, show_name: bool = True) -> None:
    """Display a short tool output preview under its call."""
    lines = result_text.strip().splitlines()
    if name == "Read" and not is_error:
        lines = [f"Read {len(lines)} lines"]
    elif len(lines) > RESULT_PREVIEW_LINES:
        lines = lines[:RESULT_PREVIEW_LINES] + [f"… +{len(lines) - RESULT_PREVIEW_LINES} lines"]
    if not lines:
        lines = ["(no output)"]

    label = f"{name}: " if show_name else ""
    # Two columns give wrapped lines a hanging indent under the ⎿.
    grid = Table.grid()
    grid.add_column(width=5)
    grid.add_column(overflow="fold")
    grid.add_row(Text("  ⎿  ", style="dim"), Text(label + "\n".join(lines), style="red" if is_error else "dim"))
    console.print(grid)


def print_thinking(thinking_text: str) -> None:
    """Render thinking / reasoning content."""
    snippet = thinking_text.strip()
    if not snippet:
        return
    text = Text(snippet, style="dim italic")
    panel = Panel(
        text,
        title="[dim]Thinking Process[/dim]",
        border_style="dim",
        padding=(0, 1),
    )
    console.print(panel)


def print_error_card(title: str, message: str, hint: Optional[str] = None) -> None:
    """Display a formatted error card with actionable advice."""
    stop_spinner()
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_row(f"[bold red]{escape(message)}[/bold red]")
    if hint:
        grid.add_row(f"\n[cyan]Tip:[/cyan] {hint}")

    err_console.print(
        Panel(
            grid,
            title=f"[bold red]✖ {title}[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )
