"""Interactive REPL for claude-agent-cli."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.history import FileHistory
from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from rich.table import Table

from claude_agent_sdk import RateLimitInfo

from claude_agent_cli.agent import AgentInteractiveSession, MissingApiKeyError
from claude_agent_cli.config import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_HISTORY_FILE,
    CagentConfig,
    ensure_config_dirs,
    load_config,
    resolve_model_name,
)
from claude_agent_cli.permissions import normalize_permission_mode
from claude_agent_cli.mcp import discover_all_mcp_servers, render_mcp_table
from claude_agent_cli.skills import discover_all_skills, render_skills_table
from claude_agent_cli.subagents import BUILTIN_AGENTS
from claude_agent_cli.ui import console, print_banner, print_error_card, screen_lock
from claude_agent_cli.usage import create_credit_dashboard, get_month_spent, get_total_spent

# name -> (argument hint, description); one table drives the menu and /help.
COMMANDS: dict[str, tuple[str, str]] = {
    "/help": ("", "Show commands and shortcuts"),
    "/model": ("[name]", "Show or switch the model"),
    "/agent": ("[name]", "Show or switch the agent persona"),
    "/permission": ("[mode]", "Show or set the permission mode (Shift+Tab cycles)"),
    "/thinking": ("[on|off]", "Show or hide the live thinking stream"),
    "/credits": ("", "Spend recorded by cagent and where to see the real balance"),
    "/cost": ("", "Total cost across all queries"),
    "/skills": ("", "List discovered skills"),
    "/mcp": ("", "List configured MCP servers"),
    "/clear": ("", "Clear the screen"),
    "/exit": ("", "Exit cagent"),
}
ALIASES = {"/?": "/help", "/balance": "/credits", "/usage": "/cost", "/quit": "/exit"}

PERMISSION_CHOICES = [
    ("ask", "Ask before each tool runs"),
    ("acceptEdits", "Auto-accept file edits, ask for the rest"),
    ("plan", "Read-only: Claude plans, nothing changes"),
    ("bypass", "Run every tool without asking"),
]
MODE_LABELS = {
    "default": ("", "ask before tools"),
    "acceptEdits": ("#af87ff", "⏵⏵ accept edits"),
    "plan": ("#5fafaf", "⏸ plan mode"),
    "bypassPermissions": ("#ff5f5f", "⏵⏵ bypass permissions"),
}
SKIP_PATHS = {".git", ".venv", "node_modules", "__pycache__", ".DS_Store"}

MENU_STYLE = {
    "prompt": "bold #00afff",
    "completion-menu": "bg:default",
    "completion-menu.completion": "bg:default #d0d0d0",
    "completion-menu.completion.current": "bg:default #00afff bold",
    "completion-menu.meta.completion": "bg:default #808080",
    "completion-menu.meta.completion.current": "bg:default #00afff",
    "scrollbar.background": "bg:default",
    "scrollbar.button": "bg:#585858",
    "bottom-toolbar": "noreverse #808080",
}


def is_slash_command(text: str) -> bool:
    """True for '/model opus', False for a pasted path like '/var/folders/x.png describe this'."""
    first = text.split(maxsplit=1)[0] if text else ""
    return first.startswith("/") and "/" not in first[1:]


def git_location(work_dir: Path) -> str:
    """'main', 'main (worktree feat)' in a linked worktree, or '' outside a git repo."""

    def git(*args: str) -> Optional[str]:
        try:
            out = subprocess.run(["git", *args], cwd=work_dir, capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout if out.returncode == 0 else None

    paths = git("rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir")
    if not paths or len(paths.splitlines()) != 3:
        return ""  # not a repo, or a bare one
    top, git_dir, common_dir = paths.splitlines()
    branch = (git("branch", "--show-current") or "").strip() or "detached"
    return branch if git_dir == common_dir else f"{branch} (worktree {Path(top).name})"


def limit_countdown(info: Optional[RateLimitInfo], now: Optional[float] = None) -> str:
    """'five hour resets in 2h 05m' from the CLI's latest rate-limit event, '' when unknown or past."""
    if not info or not info.resets_at:
        return ""
    left = int(info.resets_at - (time.time() if now is None else now))
    if left <= 0:
        return ""
    hours, minutes = divmod(left // 60, 60)
    return f"{(info.rate_limit_type or 'limit').replace('_', ' ')} resets in {hours}h {minutes:02d}m"


def _first_sentence(text: str, limit: int = 80) -> str:
    text = text.split(". ")[0].strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ReplCompleter(Completer):
    """Slash commands with descriptions, their arguments, and @file paths, like Claude Code."""

    def __init__(self, work_dir: Path):
        self.sdk_commands: list[dict] = []  # Claude Code commands and skills, filled once the SDK connects
        self.models: list[dict] = []
        self.paths = PathCompleter(
            get_paths=lambda: [str(work_dir)],
            file_filter=lambda path: os.path.basename(path) not in SKIP_PATHS,
            expanduser=True,
        )

    def sdk_command_names(self) -> set[str]:
        return {c["name"] for c in self.sdk_commands if c.get("name")}

    def _commands(self) -> list[tuple[str, str, str]]:
        items = [(name, f"{name} {hint}".strip(), desc) for name, (hint, desc) in COMMANDS.items()]
        for c in self.sdk_commands:
            name = "/" + str(c.get("name", "")).split(" ")[0]
            if name not in COMMANDS and name not in ALIASES:
                # No argumentHint: some run past 100 columns and push the descriptions off screen.
                items.append((name, name, _first_sentence(c.get("description", ""))))
        return items

    def _arguments(self, cmd: str) -> list[tuple[str, str]]:
        if cmd == "/model":
            if self.models:
                return [(m["value"], m.get("description") or m.get("displayName", "")) for m in self.models if m.get("value")]
            return [(alias, resolve_model_name(alias)) for alias in ("opus", "sonnet", "haiku")]
        if cmd == "/agent":
            return [(name, _first_sentence(spec["description"], 60)) for name, spec in BUILTIN_AGENTS.items()]
        if cmd == "/permission":
            return PERMISSION_CHOICES
        if cmd == "/thinking":
            return [("on", "Stream Claude's reasoning live"), ("off", "Hide the reasoning stream")]
        return []

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            needle = text[1:].lower()
            hits = [item for item in self._commands() if needle in item[0][1:].lower()]
            hits.sort(key=lambda item: not item[0][1:].lower().startswith(needle))  # prefix matches first
            for name, display, meta in hits:
                yield Completion(name, -len(text), display=display, display_meta=meta)
            return

        cmd, space, arg = text.partition(" ")
        if space and " " not in arg and cmd.lower() in COMMANDS:
            for value, meta in self._arguments(cmd.lower()):
                if value.lower().startswith(arg.lower()):
                    yield Completion(value, -len(arg), display_meta=meta)
            return

        mention = re.search(r"(?:^|\s)@(\S*)$", text)
        if mention:
            # The fragment is the tail of the line, so PathCompleter's cursor-relative positions still apply.
            yield from self.paths.get_completions(Document(mention.group(1)), complete_event)


def create_key_bindings(cycle_mode: Optional[Callable[[], None]] = None) -> KeyBindings:
    """Enter sends; Esc+Enter, Ctrl+J or a trailing backslash add a new line; Shift+Tab cycles the mode."""
    kb = KeyBindings()

    @kb.add("escape", "enter")
    @kb.add("c-j")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    if cycle_mode:
        @kb.add("s-tab", filter=~has_completions)
        def _cycle(event) -> None:
            cycle_mode()

    @kb.add("enter")
    def _submit(event) -> None:
        buf = event.current_buffer
        state = buf.complete_state
        if state and state.completions and not buf.text.startswith("/"):
            if state.current_completion:  # Enter picks the highlighted @file instead of sending
                buf.complete_state = None
                return
        elif state and state.completions and not state.current_completion and " " not in buf.text:
            buf.apply_completion(state.completions[0])  # Enter runs the top match, so /he sends /help
        if buf.document.text_before_cursor.endswith("\\"):
            buf.delete_before_cursor()
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()

    return kb


@contextmanager
def interrupt_keys(on_interrupt: Callable[[], None]) -> Iterator[None]:
    """While a turn runs, Esc or Ctrl+C calls on_interrupt; other keys are ignored."""
    if not sys.stdin.isatty():
        yield
        return
    inp = create_input()
    loop = asyncio.get_running_loop()

    def flush() -> None:
        # Only a lone Esc is left in the parser after the timeout; Alt+key and arrow keys are parsed already.
        if any(k.key == Keys.Escape for k in inp.flush_keys()):
            on_interrupt()

    def keys_ready() -> None:
        if any(k.key == Keys.ControlC for k in inp.read_keys()):
            on_interrupt()
        loop.call_later(0.05, flush)  # same idea as prompt_toolkit's ttimeoutlen

    with inp.raw_mode(), inp.attach(keys_ready):
        yield


async def run_turn(session: AgentInteractiveSession, text: str) -> None:
    """Run one turn. Esc or Ctrl+C asks Claude to stop; a second press drops the turn."""
    interrupted = False
    turn = asyncio.current_task()
    pending: list[asyncio.Future] = []  # keeps the interrupt task referenced until it finishes

    def request_interrupt() -> None:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            console.print("[yellow]Interrupting… (press again to force)[/yellow]")
            pending.append(asyncio.ensure_future(session.interrupt()))
        elif turn is not None:
            turn.cancel()  # Claude did not stop; drop the turn so the REPL stays usable

    try:
        with interrupt_keys(request_interrupt):
            await session.query(text)
            # A cancelled permission menu restores the terminal on its way out; let it finish first.
            async with screen_lock:
                pass
    except asyncio.CancelledError:
        if turn is not None and hasattr(turn, "uncancel"):
            turn.uncancel()
        console.print("\n[yellow]Query cancelled.[/yellow]")
    except Exception as e:
        print_error_card("Query Failed", str(e))


def print_repl_help(sdk_commands: int = 0) -> None:
    """Print available slash commands."""
    table = Table(
        title="Slash Commands",
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Command", style="bold white", width=27)
    table.add_column("Description", style="dim")

    for name, (hint, desc) in COMMANDS.items():
        names = ", ".join([name] + [alias for alias, target in ALIASES.items() if target == name])
        table.add_row(f"{names} {hint}".strip(), desc)
    if sdk_commands:
        table.add_row("/compact, /init, …", f"{sdk_commands} Claude Code commands and skills; type / to browse")
    table.add_row("@path", "Mention a file; Tab or arrows pick from the menu")
    table.add_row("Shift+Tab", "Cycle permission mode: ask, accept edits, plan")
    table.add_row("Esc / Ctrl+C", "Interrupt Claude while a turn runs")
    table.add_row("Esc+Enter, Ctrl+J, \\+Enter", "New line in the prompt")

    console.print(table)


async def run_repl(
    *,
    config: Optional[CagentConfig] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    agent_name: Optional[str] = None,
    cwd: Optional[Path] = None,
    permission_mode: Optional[str] = None,
    skills_filter: Optional[list[str]] = None,
    mcp_filter: Optional[list[str]] = None,
    resume: Optional[str] = None,
) -> None:
    """Launch the interactive REPL chat session."""
    active_config = config or load_config()
    work_dir = Path(cwd).resolve() if cwd else Path.cwd()
    ensure_config_dirs()

    active_agent = agent_name or active_config.default_agent
    active_model = model or active_config.default_model

    try:
        session = AgentInteractiveSession(
            config=active_config,
            api_key=api_key,
            model=active_model,
            agent_name=active_agent,
            cwd=work_dir,
            permission_mode=permission_mode,
            skills_filter=skills_filter,
            mcp_filter=mcp_filter,
            resume=resume,
        )
    except MissingApiKeyError as e:
        print_error_card(
            "Authentication Required",
            str(e),
            "Run 'cagent auth login' to save your Anthropic API key.",
        )
        return

    # Print welcome banner
    print_banner(resolve_model_name(active_model), active_agent, session.permission_mode, str(work_dir))

    # Connect client
    with console.status("[bold cyan]Connecting to Claude Agent SDK...[/bold cyan]"):
        try:
            await session.start()
        except Exception as e:
            print_error_card("Connection Error", str(e))
            return

    console.print("[green]✔ Agent session connected and ready.[/green]\n")

    completer = ReplCompleter(work_dir)
    try:
        info = await session.client.get_server_info() or {}
        completer.sdk_commands = info.get("commands", [])
        completer.models = info.get("models", [])
    except Exception:
        pass  # the menu still lists cagent's own commands
    month_spent = get_month_spent()

    modes = ["default", "acceptEdits", "plan"]
    if session.permission_mode not in modes:
        modes.append(session.permission_mode)  # bypass stays in the cycle only when the session started in it

    def cycle_mode() -> None:
        current = session.permission_mode
        mode = modes[(modes.index(current) + 1) % len(modes)] if current in modes else modes[0]
        session.permission_mode = mode  # the toolbar redraws before the task runs
        get_app().create_background_task(session.set_permission_mode(mode))

    branch = ""  # refreshed before each prompt: the agent may switch branches, and git is too slow per keystroke

    def bottom_toolbar() -> list[tuple[str, str]]:
        style, label = MODE_LABELS.get(session.permission_mode, ("", session.permission_mode))
        extras = [f"⎇ {branch}" if branch else "", limit_countdown(session.rate_limit)]
        return [
            ("", f" {resolve_model_name(active_model)} · {active_agent} · "),
            (style, label),
            ("", " (shift+tab)"),
            ("", f" · session ${session.session_spent:.4f} · month ${month_spent:.2f}"),
            ("", "".join(f" · {e}" for e in extras if e)),
        ]

    pt_session: PromptSession = PromptSession(
        history=FileHistory(str(DEFAULT_HISTORY_FILE)),
        completer=completer,
        style=Style.from_dict(MENU_STYLE),
        key_bindings=create_key_bindings(cycle_mode),
        bottom_toolbar=bottom_toolbar,
        refresh_interval=60,  # keeps the reset countdown current while idle
    )

    try:
        while True:
            try:
                # Format prompt with active agent name
                prompt_text = [
                    ("class:prompt", f"({active_agent}) ❯ "),
                ]
                branch = git_location(work_dir)
                user_input = await pt_session.prompt_async(prompt_text)
                text = user_input.strip()

                if not text:
                    continue

                # Slash command handling
                if is_slash_command(text):
                    cmd_parts = text.split(maxsplit=1)
                    cmd = ALIASES.get(cmd_parts[0].lower(), cmd_parts[0].lower())
                    arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""

                    if cmd == "/exit":
                        console.print("[dim]Exiting cagent. Goodbye![/dim]")
                        break
                    elif cmd == "/help":
                        print_repl_help(len(completer.sdk_commands))
                        continue
                    elif cmd == "/credits":
                        console.print(create_credit_dashboard())
                        continue
                    elif cmd == "/cost":
                        spent = get_total_spent()
                        console.print(f"[bold]Total spent so far:[/bold] [bold red]${spent:.4f}[/bold red]")
                        continue
                    elif cmd == "/skills":
                        all_skills = discover_all_skills(work_dir)
                        console.print(render_skills_table(all_skills))
                        continue
                    elif cmd == "/mcp":
                        all_mcp = discover_all_mcp_servers(work_dir)
                        console.print(render_mcp_table(all_mcp))
                        continue
                    elif cmd == "/clear":
                        os.system("clear" if os.name != "nt" else "cls")
                        print_banner(resolve_model_name(active_model), active_agent, session.permission_mode, str(work_dir))
                        continue
                    elif cmd == "/model":
                        if arg:
                            new_model = resolve_model_name(arg)
                            try:
                                if session.client:
                                    await session.client.set_model(new_model)
                            except Exception as e:
                                print_error_card("Model Switch Failed", str(e))
                                continue
                            active_model = session.model = new_model
                            console.print(f"[cyan]Switched model to:[/cyan] {active_model}")
                        else:
                            console.print(f"[cyan]Current model:[/cyan] {active_model}")
                        continue
                    elif cmd == "/agent":
                        if arg:
                            if arg in BUILTIN_AGENTS:
                                try:
                                    with console.status("[dim]Switching agent…[/dim]"):
                                        await session.switch_agent(arg)
                                except Exception as e:
                                    print_error_card("Agent Switch Failed", str(e))
                                    continue
                                active_agent = arg
                                console.print(f"[cyan]Switched active agent persona to:[/cyan] [bold]{active_agent}[/bold]")
                            else:
                                console.print(f"[red]Unknown agent '{arg}'. Available:[/red] {', '.join(BUILTIN_AGENTS.keys())}")
                        else:
                            console.print(f"[cyan]Current agent:[/cyan] [bold]{active_agent}[/bold]")
                        continue
                    elif cmd == "/permission":
                        if arg:
                            norm_mode = normalize_permission_mode(arg)
                            try:
                                await session.set_permission_mode(norm_mode)
                                console.print(f"[cyan]Permission mode updated to:[/cyan] [bold]{norm_mode}[/bold]")
                            except Exception as e:
                                console.print(f"[red]Failed to update permission mode:[/red] {e}")
                        else:
                            console.print(f"[cyan]Current permission mode:[/cyan] {session.permission_mode}")
                        continue
                    elif cmd == "/thinking":
                        if arg:
                            clean_arg = arg.lower().strip()
                            if clean_arg in ("on", "true", "yes", "1", "enable"):
                                session.show_thinking = True
                                console.print("[green]✔ Live reasoning/thinking stream enabled.[/green]")
                            elif clean_arg in ("off", "false", "no", "0", "disable"):
                                session.show_thinking = False
                                console.print("[yellow]✔ Live reasoning/thinking stream hidden.[/yellow]")
                            else:
                                console.print("[red]Invalid argument. Use: /thinking on or /thinking off[/red]")
                        else:
                            state_str = "[green]enabled[/green]" if session.show_thinking else "[yellow]hidden[/yellow]"
                            console.print(f"[cyan]Live thinking stream is currently:[/cyan] {state_str}")
                        continue
                    elif cmd[1:] not in completer.sdk_command_names():
                        console.print(f"[red]Unknown command '{cmd}'. Type /help for assistance.[/red]")
                        continue
                    # Anything else is a Claude Code command or skill (/compact, /init, …); Claude runs it.

                # Execute query
                console.print()
                await run_turn(session, text)
                month_spent = get_month_spent()

            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted current operation. Type /exit to quit.[/yellow]")
                await session.interrupt()
            except EOFError:
                console.print("\n[dim]Session closed.[/dim]")
                break

    finally:
        await session.close()
        resume_id = session.active_session_id or session.resume
        if resume_id:
            console.print(f"[dim]Resume this session with:[/dim] cagent --resume {resume_id}")
