"""CLI entrypoint for claude-agent-cli using Typer."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

import typer
from rich.table import Table
from typer.core import TyperGroup

from claude_agent_cli.agent import MissingApiKeyError, run_single_turn
from claude_agent_cli.config import (
    CagentConfig,
    detect_claude_cli_defaults,
    get_api_key,
    load_config,
    mask_api_key,
    resolve_model_name,
    save_config,
)
from claude_agent_cli.mcp import discover_all_mcp_servers, render_mcp_table
from claude_agent_cli.repl import run_repl
from claude_agent_cli.sessions import get_sessions, render_sessions_table
from claude_agent_cli.skills import (
    discover_all_skills,
    render_skills_table,
    sync_skills_to_claude_home,
)
from claude_agent_cli.subagents import BUILTIN_AGENTS, get_agent_spec, load_custom_agents
from claude_agent_cli.ui import console, err_console, print_error_card
from claude_agent_cli.usage import create_credit_dashboard, create_recent_activity_table


class DefaultCommandGroup(TyperGroup):
    """Custom TyperGroup that routes any non-command arguments directly to the 'run' command."""

    def resolve_command(self, ctx: typer.Context, args: list[str]) -> tuple[Optional[str], Optional[Any], list[str]]:
        cmd_name = args[0] if args else None
        if not cmd_name:
            return super().resolve_command(ctx, args)
        if cmd_name in self.commands or cmd_name.startswith("-"):
            return super().resolve_command(ctx, args)
        # Not a known subcommand or flag: treat as prompt to 'run'
        cmd = self.get_command(ctx, "run")
        return "run", cmd, args


app = typer.Typer(
    cls=DefaultCommandGroup,
    name="cagent",
    help="CLI coding agent alternative to Claude Code powered by the Claude Agent SDK.",
    no_args_is_help=False,
    invoke_without_command=True,
)

auth_app = typer.Typer(name="auth", help="Manage Anthropic API key authentication.")
config_app = typer.Typer(name="config", help="Manage cagent settings.")
agents_app = typer.Typer(name="agents", help="Inspect and manage specialized agent profiles.")
skills_app = typer.Typer(name="skills", help="Discover and manage agent skills.")
mcp_app = typer.Typer(name="mcp", help="Discover and manage MCP servers.")
sessions_app = typer.Typer(name="sessions", help="View and manage previous agent sessions.")

app.add_typer(auth_app)
app.add_typer(config_app)
app.add_typer(agents_app)
app.add_typer(skills_app)
app.add_typer(mcp_app)
app.add_typer(sessions_app)


def _parse_filter_list(val: Optional[str]) -> Optional[list[str]]:
    """Convert comma-separated list into list of strings."""
    if not val:
        return None
    return [item.strip() for item in val.split(",") if item.strip()]


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    model: Optional[str] = typer.Option(
        None,
        "-m",
        "--model",
        help="Claude model to use (e.g. sonnet, opus, haiku, claude-3-7-sonnet).",
    ),
    agent: Optional[str] = typer.Option(
        None,
        "-a",
        "--agent",
        help="Specialized agent persona (coder, architect, reviewer, tester, debugger).",
    ),
    permission_mode: Optional[str] = typer.Option(
        None,
        "--permission-mode",
        "--mode",
        help="Tool permission mode ('acceptEdits', 'ask', 'bypass').",
    ),
    skills: Optional[str] = typer.Option(
        None,
        "--skills",
        help="Skills to enable ('all', comma-separated list, or 'off').",
    ),
    mcp: Optional[str] = typer.Option(
        None,
        "--mcp",
        help="MCP servers to enable ('all', comma-separated list, or 'off').",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "-k",
        "--api-key",
        help="Anthropic API Key to use (overrides env and saved config).",
    ),
    cwd: Optional[Path] = typer.Option(
        None,
        "--cwd",
        help="Working directory for tool executions.",
    ),
    resume: Optional[str] = typer.Option(
        None,
        "-r",
        "--resume",
        help="Resume conversation with a previous session ID.",
    ),
) -> None:
    """Default callback: If no subcommand was called, launch interactive REPL."""
    if ctx.invoked_subcommand is not None:
        return

    skills_filter = _parse_filter_list(skills)
    mcp_filter = _parse_filter_list(mcp)

    # Check for piped input from stdin when called with no arguments
    piped_input = ""
    if not sys.stdin.isatty():
        try:
            piped_input = sys.stdin.read().strip()
        except Exception:
            piped_input = ""

    if piped_input:
        # User did e.g.: cat file.txt | cagent
        try:
            asyncio.run(
                run_single_turn(
                    prompt=piped_input,
                    api_key=api_key,
                    model=model,
                    agent_name=agent,
                    cwd=cwd,
                    permission_mode=permission_mode,
                    skills_filter=skills_filter,
                    mcp_filter=mcp_filter,
                )
            )
        except MissingApiKeyError as e:
            print_error_card(
                "Authentication Required",
                str(e),
                "Run 'cagent auth login' to save your Anthropic API key.",
            )
            raise typer.Exit(code=1)
        except Exception as e:
            print_error_card("Execution Error", str(e))
            raise typer.Exit(code=1)
        return

    # Launch interactive REPL
    try:
        asyncio.run(
            run_repl(
                api_key=api_key,
                model=model,
                agent_name=agent,
                cwd=cwd,
                permission_mode=permission_mode,
                skills_filter=skills_filter,
                mcp_filter=mcp_filter,
                resume=resume,
            )
        )
    except MissingApiKeyError as e:
        print_error_card(
            "Authentication Required",
            str(e),
            "Run 'cagent auth login' to save your Anthropic API key.",
        )
        raise typer.Exit(code=1)
    except Exception as e:
        print_error_card("Execution Error", str(e))
        raise typer.Exit(code=1)


@app.command("run")
def run_command(
    prompt: List[str] = typer.Argument(..., help="Prompt to execute."),
    model: Optional[str] = typer.Option(None, "-m", "--model", help="Claude model name or alias."),
    agent: Optional[str] = typer.Option(None, "-a", "--agent", help="Specialized agent profile."),
    permission_mode: Optional[str] = typer.Option(None, "--permission-mode", "--mode", help="Permission mode."),
    skills: Optional[str] = typer.Option(None, "--skills", help="Skills to enable ('all', comma-separated list, or 'off')."),
    mcp: Optional[str] = typer.Option(None, "--mcp", help="MCP servers to enable ('all', comma-separated list, or 'off')."),
    api_key: Optional[str] = typer.Option(None, "-k", "--api-key", help="Anthropic API key."),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Working directory."),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream response tokens."),
    thinking: bool = typer.Option(True, "--thinking/--no-thinking", help="Show live thinking/reasoning process."),
) -> None:
    """Execute a single query non-interactively."""
    joined_prompt = " ".join(prompt)

    # Check for piped input from stdin
    piped_input = ""
    if not sys.stdin.isatty():
        try:
            piped_input = sys.stdin.read().strip()
        except Exception:
            piped_input = ""

    if piped_input:
        joined_prompt = f"{joined_prompt}\n\n[Piped Input]:\n{piped_input}"

    skills_filter = _parse_filter_list(skills)
    mcp_filter = _parse_filter_list(mcp)

    try:
        asyncio.run(
            run_single_turn(
                prompt=joined_prompt,
                api_key=api_key,
                model=model,
                agent_name=agent,
                cwd=cwd,
                permission_mode=permission_mode,
                skills_filter=skills_filter,
                mcp_filter=mcp_filter,
                stream=stream,
                show_thinking=thinking,
            )
        )
    except MissingApiKeyError as e:
        print_error_card(
            "Authentication Required",
            str(e),
            "Run 'cagent auth login' to save your Anthropic API key.",
        )
        raise typer.Exit(code=1)
    except Exception as e:
        print_error_card("Execution Error", str(e))
        raise typer.Exit(code=1)


@app.command("chat")
def chat_command(
    model: Optional[str] = typer.Option(None, "-m", "--model"),
    agent: Optional[str] = typer.Option(None, "-a", "--agent"),
    permission_mode: Optional[str] = typer.Option(None, "--permission-mode", "--mode"),
    skills: Optional[str] = typer.Option(None, "--skills", help="Skills to enable."),
    mcp: Optional[str] = typer.Option(None, "--mcp", help="MCP servers to enable."),
    api_key: Optional[str] = typer.Option(None, "-k", "--api-key"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    resume: Optional[str] = typer.Option(None, "-r", "--resume"),
) -> None:
    """Start an interactive chat REPL session."""
    skills_filter = _parse_filter_list(skills)
    mcp_filter = _parse_filter_list(mcp)

    try:
        asyncio.run(
            run_repl(
                api_key=api_key,
                model=model,
                agent_name=agent,
                cwd=cwd,
                permission_mode=permission_mode,
                skills_filter=skills_filter,
                mcp_filter=mcp_filter,
                resume=resume,
            )
        )
    except MissingApiKeyError as e:
        print_error_card(
            "Authentication Required",
            str(e),
            "Run 'cagent auth login' to save your Anthropic API key.",
        )
        raise typer.Exit(code=1)
    except Exception as e:
        print_error_card("Chat Session Error", str(e))
        raise typer.Exit(code=1)


@app.command("credits")
def credits_command() -> None:
    """Display spend recorded by cagent and recent activity."""
    console.print(create_credit_dashboard())
    console.print()
    console.print(create_recent_activity_table())


@app.command("balance")
def balance_command() -> None:
    """Alias for 'credits'."""
    credits_command()


@app.command("usage")
def usage_command() -> None:
    """Alias for 'credits'."""
    credits_command()


# ------------------- SKILLS COMMANDS -------------------


@skills_app.command("list")
def skills_list() -> None:
    """List all discovered skills across system directories and current workspace."""
    all_skills = discover_all_skills(Path.cwd())
    console.print(render_skills_table(all_skills))


@skills_app.command("sync")
def skills_sync() -> None:
    """Re-sync and symlink all system skills into cagent's isolated Claude home."""
    config = load_config()
    linked = sync_skills_to_claude_home(config.claude_home)
    console.print(f"[bold green]✔ Successfully synced {len(linked)} skills into {config.claude_home}/skills[/bold green]")


@skills_app.command("show")
def skills_show(name: str = typer.Argument(..., help="Skill name to inspect.")) -> None:
    """Show detailed SKILL.md instructions for a skill."""
    all_skills = discover_all_skills(Path.cwd())
    if name not in all_skills:
        console.print(f"[red]Skill '{name}' not found.[/red]")
        raise typer.Exit(code=1)

    info = all_skills[name]
    path = Path(info["path"]) / "SKILL.md"
    if path.exists():
        console.print(f"[bold cyan]Skill:[/bold cyan] {name} ([dim]{path}[/dim])\n")
        console.print(path.read_text(encoding="utf-8"))
    else:
        console.print(f"[red]SKILL.md not found at {path}[/red]")


# ------------------- MCP COMMANDS -------------------


@mcp_app.command("list")
def mcp_list() -> None:
    """List all configured MCP servers across Antigravity, Claude, and project settings."""
    all_mcp = discover_all_mcp_servers(Path.cwd())
    console.print(render_mcp_table(all_mcp))


@mcp_app.command("show")
def mcp_show(name: str = typer.Argument(..., help="MCP server name.")) -> None:
    """Show details and command configuration for a specific MCP server."""
    all_mcp = discover_all_mcp_servers(Path.cwd())
    if name not in all_mcp:
        console.print(f"[red]MCP server '{name}' not found.[/red]")
        raise typer.Exit(code=1)

    server = all_mcp[name]
    console.print(f"[bold cyan]MCP Server:[/bold cyan] {name}")
    console.print(json.dumps(server, indent=2))


# ------------------- AUTH COMMANDS -------------------


@auth_app.command("login")
def auth_login(
    key: Optional[str] = typer.Option(None, "--key", "-k", help="API key to save directly.")
) -> None:
    """Save the Anthropic API key cagent uses (Console credits, including Promotional credits)."""
    if not key:
        console.print("[bold cyan]Enter your Anthropic API Key (from platform.claude.com):[/bold cyan]")
        key = typer.prompt("API Key", hide_input=True)

    if not key or not key.strip():
        console.print("[red]API key cannot be empty.[/red]")
        raise typer.Exit(code=1)

    clean_key = key.strip()
    config = load_config()
    config.anthropic_api_key = clean_key
    save_config(config)

    console.print("[bold green]✔ Successfully saved Anthropic API Key![/bold green]")
    console.print(f"Key configured: {mask_api_key(clean_key)}")
    console.print("cagent queries now bill this key's Console org: Promotional credits first, then Credit balance.")


@auth_app.command("status")
def auth_status() -> None:
    """Display current authentication and API key status."""
    active_key = get_api_key()
    config = load_config()

    source = "Not found"
    if os.environ.get("ANTHROPIC_API_KEY"):
        source = "Environment (ANTHROPIC_API_KEY)"
    elif config.anthropic_api_key:
        source = "Config file (~/.claude-agent-cli/config.json)"

    table = Table(title="Authentication Status", border_style="dim")
    table.add_column("Setting", style="bold white")
    table.add_column("Value")

    table.add_row("API Key:", mask_api_key(active_key))
    table.add_row("Key Source:", source)
    table.add_row("Isolated Config Dir:", str(config.claude_home))

    console.print(table)


@auth_app.command("logout")
def auth_logout() -> None:
    """Clear saved Anthropic API Key from configuration."""
    config = load_config()
    config.anthropic_api_key = None
    save_config(config)
    console.print("[yellow]✔ Cleared saved API key from configuration.[/yellow]")


# ------------------- CONFIG COMMANDS -------------------


@config_app.command("show")
def config_show() -> None:
    """Show current configuration settings."""
    config = load_config()
    table = Table(title="cagent Configuration", border_style="dim")
    table.add_column("Setting", style="bold white")
    table.add_column("Value")

    table.add_row("Default Model:", config.default_model)
    table.add_row("Default Agent:", config.default_agent)
    table.add_row("Permission Mode:", config.permission_mode)
    table.add_row("Extended Thinking:", str(config.extended_thinking))
    table.add_row("Effort Level:", str(config.effort))
    table.add_row(
        "Reuse Claude CLI Config:",
        f"{config.reuse_claude_cli_config} ([dim]{config.claude_settings_path}[/dim])",
    )
    table.add_row("Claude Home:", str(config.claude_home))

    console.print(table)


@config_app.command("set-model")
def config_set_model(
    model: str = typer.Argument(..., help="Model name or alias (e.g. opus 5.5, sonnet, haiku).")
) -> None:
    """Set default model for agent runs."""
    config = load_config()
    resolved = resolve_model_name(model)
    config.default_model = resolved
    save_config(config)
    console.print(f"[green]✔ Updated default model to '{resolved}'.[/green]")


@config_app.command("set-effort")
def config_set_effort(
    effort: str = typer.Argument(..., help="Effort level: 'low', 'medium', 'high', 'xhigh', or 'max'.")
) -> None:
    """Set thinking reasoning effort level."""
    valid = ("low", "medium", "high", "xhigh", "max")
    cleaned = effort.lower().strip()
    if cleaned not in valid:
        console.print(f"[red]Invalid effort level. Choose from: {', '.join(valid)}[/red]")
        raise typer.Exit(code=1)
    config = load_config()
    config.effort = cleaned
    save_config(config)
    console.print(f"[green]✔ Updated thinking reasoning effort to '{cleaned}'.[/green]")


@config_app.command("sync-claude")
def config_sync_claude() -> None:
    """Re-sync default model and effort from ~/.claude/settings.json."""
    detected_model, detected_effort = detect_claude_cli_defaults()
    config = load_config()
    config.default_model = detected_model
    config.effort = detected_effort
    save_config(config)
    console.print("[bold green]✔ Successfully synced from ~/.claude/settings.json:[/bold green]")
    console.print(f" - Default Model: [bold cyan]{detected_model}[/bold cyan]")
    console.print(f" - Reasoning Effort: [bold magenta]{detected_effort}[/bold magenta]")


@config_app.command("set-agent")
def config_set_agent(
    agent: str = typer.Argument(..., help="Default agent persona name.")
) -> None:
    """Set default agent persona."""
    config = load_config()
    config.default_agent = agent
    save_config(config)
    console.print(f"[green]✔ Updated default agent persona to '{agent}'.[/green]")


@config_app.command("set-mode")
def config_set_mode(
    mode: str = typer.Argument(..., help="Permission mode: 'acceptEdits', 'ask', or 'bypass'.")
) -> None:
    """Set default tool permission mode."""
    if mode not in ("acceptEdits", "ask", "bypass"):
        console.print("[red]Invalid mode. Must be 'acceptEdits', 'ask', or 'bypass'.[/red]")
        raise typer.Exit(code=1)
    config = load_config()
    config.permission_mode = mode
    save_config(config)
    console.print(f"[green]✔ Updated default permission mode to '{mode}'.[/green]")


# ------------------- AGENTS COMMANDS -------------------


@agents_app.command("list")
def agents_list() -> None:
    """List all available specialized subagents."""
    table = Table(title="Available Agent Profiles", border_style="dim")
    table.add_column("Agent", style="bold cyan", width=12)
    table.add_column("Tools", style="green", width=32)
    table.add_column("Description", style="white")

    for name, spec in BUILTIN_AGENTS.items():
        tools_str = ", ".join(spec["tools"])
        table.add_row(name, tools_str, spec["description"])

    custom = load_custom_agents(Path.cwd())
    for name, spec in custom.items():
        tools_str = ", ".join(spec.get("tools", []))
        table.add_row(f"{name} (custom)", tools_str, spec.get("description", ""))

    console.print(table)


@agents_app.command("show")
def agents_show(
    name: str = typer.Argument(..., help="Agent profile name.")
) -> None:
    """Show detailed prompt and tools for an agent."""
    spec = get_agent_spec(name, Path.cwd())
    if not spec:
        console.print(f"[red]Agent '{name}' not found.[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Agent:[/bold cyan] {name}")
    console.print(f"[bold white]Description:[/bold white] {spec.get('description', '')}")
    console.print(f"[bold green]Tools:[/bold green] {', '.join(spec.get('tools', []))}")
    console.print(f"\n[bold magenta]System Persona Prompt:[/bold magenta]\n{spec.get('prompt', '')}")


# ------------------- SESSIONS COMMANDS -------------------


@sessions_app.command("list")
def sessions_list(
    limit: int = typer.Option(15, "--limit", "-n", help="Number of sessions to show.")
) -> None:
    """List previous conversation sessions in the current workspace."""
    sessions = get_sessions(limit=limit)
    console.print(render_sessions_table(sessions))


@sessions_app.command("resume")
def sessions_resume(
    session_id: str = typer.Argument(..., help="Session ID to resume.")
) -> None:
    """Resume an existing conversation session by ID."""
    try:
        asyncio.run(run_repl(resume=session_id))
    except Exception as e:
        print_error_card("Resume Error", str(e))
        raise typer.Exit(code=1)


def main() -> None:
    """Global CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
