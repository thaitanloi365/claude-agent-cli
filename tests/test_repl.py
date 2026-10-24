"""Tests for REPL turn handling and live-display pausing."""

import asyncio
from contextlib import contextmanager
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console
from rich.text import Text
from claude_agent_sdk import PermissionResultAllow, ToolPermissionContext

from claude_agent_cli import ui
from claude_agent_cli.permissions import create_permission_callback
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

import subprocess

from claude_agent_sdk import RateLimitInfo

from claude_agent_cli.repl import ReplCompleter, git_location, is_slash_command, limit_countdown, run_turn


def _complete(completer, text):
    return [(c.text, c.display_meta_text) for c in completer.get_completions(Document(text), CompleteEvent())]


def test_completer_menu(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("")
    (tmp_path / ".git").mkdir()
    completer = ReplCompleter(tmp_path)
    completer.sdk_commands = [
        {"name": "compact", "description": "Clear history but keep a summary. More detail here."},
        {"name": "code-review", "description": "Review the diff"},
        {"name": "model", "description": "shadowed by cagent's own /model"},
    ]

    assert _complete(completer, "/mo") == [("/model", "Show or switch the model")]
    assert ("/compact", "Clear history but keep a summary") in _complete(completer, "/")
    assert [t for t, _ in _complete(completer, "/rev")] == ["/code-review"]  # substring match
    assert [t for t, _ in _complete(completer, "/c")][:2] == ["/credits", "/cost"]  # prefix matches first
    assert [t for t, _ in _complete(completer, "/permission a")] == ["ask", "acceptEdits"]
    assert _complete(completer, "/help me") == []
    assert _complete(completer, "explain this") == []

    assert [t for t, _ in _complete(completer, "look at @s")] == ["rc"]
    assert [t for t, _ in _complete(completer, "look at @src/")] == ["main.py"]
    assert [t for t, _ in _complete(completer, "@")] == ["src"]  # .git is hidden
    assert _complete(completer, "mail me@s") == []


def test_pasted_paths_are_prompts_not_commands():
    assert is_slash_command("/model opus")
    assert is_slash_command("/help")
    assert not is_slash_command("/var/folders/ng/T/paste-1.png also check this")
    assert not is_slash_command("/Users/me/shot.png")
    assert not is_slash_command("describe /tmp/x.png")


@pytest.mark.asyncio
async def test_second_interrupt_drops_a_stuck_turn(monkeypatch):
    presses = []

    @contextmanager
    def fake_keys(on_interrupt):
        presses.append(on_interrupt)
        yield

    monkeypatch.setattr("claude_agent_cli.repl.interrupt_keys", fake_keys)

    class StuckSession:
        interrupts = 0

        async def query(self, text):
            await asyncio.Event().wait()  # Claude never finishes

        async def interrupt(self):
            self.interrupts += 1  # and ignores the interrupt

    session = StuckSession()
    task = asyncio.ensure_future(run_turn(session, "hi"))
    await asyncio.sleep(0)

    presses[0]()
    await asyncio.sleep(0)
    assert session.interrupts == 1
    assert not task.done()

    presses[0]()
    await asyncio.wait_for(task, 1)
    assert not task.cancelled()  # the turn is dropped, the REPL keeps running
    assert task.cancelling() == 0


def test_pause_and_resume_live(monkeypatch):
    monkeypatch.setattr(ui, "console", Console(file=StringIO(), force_terminal=True))
    ui.start_live(Text("partial reply"))
    ui.pause_live()
    assert ui._live is None

    ui.resume_live()
    assert ui._live is not None
    ui.stop_live()
    assert ui._live is None and ui._paused is None


@pytest.mark.asyncio
async def test_permission_prompt_hides_streaming_reply():
    cb = create_permission_callback(permission_mode="default", is_interactive=True)
    order = MagicMock()

    with patch("sys.stdin.isatty", return_value=True), \
         patch("claude_agent_cli.permissions.pause_live", order.pause), \
         patch("claude_agent_cli.permissions.resume_live", order.resume), \
         patch("claude_agent_cli.permissions._ask", AsyncMock(side_effect=lambda name: (order.ask(), "y")[1])):
        result = await cb("Bash", {"command": "ls"}, ToolPermissionContext(tool_use_id="t1"))

    assert isinstance(result, PermissionResultAllow)
    assert [c[0] for c in order.mock_calls] == ["pause", "ask", "resume"]


@pytest.mark.asyncio
async def test_switch_agent_keeps_conversation_and_sends_persona(monkeypatch):
    from claude_agent_cli.agent import AgentInteractiveSession
    from claude_agent_cli.config import CagentConfig

    session = AgentInteractiveSession(config=CagentConfig(), api_key="sk-test")
    session.active_session_id = "s1"
    sent = []

    class FakeClient:
        async def query(self, prompt):
            sent.append(prompt)

        async def receive_response(self):
            return
            yield

        async def disconnect(self):
            pass

    session.client = FakeClient()

    async def fake_start():
        session.client = FakeClient()

    monkeypatch.setattr(session, "start", fake_start)

    await session.switch_agent("reviewer")
    assert session.resume == "s1"
    assert session.agent_name == "reviewer"

    await session.query("look at main.py")
    await session.query("and utils.py")
    assert sent[0].startswith("[Agent persona switched to REVIEWER.")
    assert sent[0].endswith("look at main.py")
    assert sent[1] == "and utils.py"  # the note rides on one message only


def test_run_command_reports_errors_before_streaming(monkeypatch):
    from typer.testing import CliRunner
    from claude_agent_cli.cli import app

    async def boom(**kwargs):
        raise RuntimeError("options failed")

    monkeypatch.setattr("claude_agent_cli.cli.run_single_turn", boom)
    result = CliRunner().invoke(app, ["run", "hello"])

    assert result.exit_code == 1
    assert "options failed" in result.output


def test_git_location(tmp_path):
    def git(*args, cwd=tmp_path / "repo"):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    assert git_location(tmp_path) == ""
    git("init", "-q", "-b", "main", "repo", cwd=tmp_path)
    assert git_location(tmp_path / "repo") == "main"  # no commits yet
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    git("worktree", "add", "-q", "-b", "feat", str(tmp_path / "wt"))
    assert git_location(tmp_path / "wt") == "feat (worktree wt)"
    git("checkout", "-q", "--detach")
    assert git_location(tmp_path / "repo") == "detached"


def test_limit_countdown():
    info = RateLimitInfo(status="allowed", resets_at=10_000, rate_limit_type="five_hour")
    assert limit_countdown(info, now=10_000 - 2 * 3600 - 5 * 60 - 30) == "five hour resets in 2h 05m"
    assert limit_countdown(info, now=10_001) == ""
    assert limit_countdown(RateLimitInfo(status="allowed"), now=0) == ""
    assert limit_countdown(None) == ""
