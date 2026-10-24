import pytest
from unittest.mock import AsyncMock, patch

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ToolPermissionContext,
)
from claude_agent_cli.permissions import (
    create_permission_callback,
    normalize_permission_mode,
)


def test_normalize_permission_mode():
    assert normalize_permission_mode("bypass") == "bypassPermissions"
    assert normalize_permission_mode("bypassPermissions") == "bypassPermissions"
    assert normalize_permission_mode("force") == "bypassPermissions"
    assert normalize_permission_mode("ask") == "default"
    assert normalize_permission_mode("interactive") == "default"
    assert normalize_permission_mode("default") == "default"
    assert normalize_permission_mode("acceptEdits") == "acceptEdits"
    assert normalize_permission_mode("accept_edits") == "acceptEdits"
    assert normalize_permission_mode("dontAsk") == "dontAsk"
    assert normalize_permission_mode("dont_ask") == "dontAsk"
    assert normalize_permission_mode("plan") == "plan"
    assert normalize_permission_mode(None) == "acceptEdits"


@pytest.mark.asyncio
async def test_bypass_mode_still_asks_when_cli_asks():
    # The CLI auto-allows in bypass mode and only calls back when a hook or ask rule demands a prompt.
    cb = create_permission_callback(permission_mode="bypassPermissions", is_interactive=True)
    ctx = ToolPermissionContext(tool_use_id="t1", decision_reason="Other people will see this")

    with patch("sys.stdin.isatty", return_value=True), \
         patch("claude_agent_cli.permissions._ask", AsyncMock(return_value="d")) as ask:
        result = await cb("mcp__claude_ai_Slack__slack_send_message", {"text": "hi"}, ctx)
        assert isinstance(result, PermissionResultDeny)
        ask.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_edits_mode_auto_allows_edit_and_reads():
    cb = create_permission_callback(permission_mode="acceptEdits", is_interactive=True)
    ctx = ToolPermissionContext(tool_use_id="t1")

    for tool in ("Edit", "Write", "Read", "Grep", "Glob", "Skill"):
        result = await cb(tool, {"file_path": "foo.py"}, ctx)
        assert isinstance(result, PermissionResultAllow)


@pytest.mark.asyncio
async def test_plan_mode():
    cb = create_permission_callback(permission_mode="plan", is_interactive=True)
    ctx = ToolPermissionContext(tool_use_id="t1")

    # Reads allowed
    assert isinstance(await cb("Read", {"file_path": "foo.py"}, ctx), PermissionResultAllow)
    assert isinstance(await cb("Grep", {"pattern": "abc"}, ctx), PermissionResultAllow)

    # Modifications denied
    denied = await cb("Bash", {"command": "npm test"}, ctx)
    assert isinstance(denied, PermissionResultDeny)
    assert "Plan mode" in denied.message


@pytest.mark.asyncio
async def test_dont_ask_mode():
    cb = create_permission_callback(permission_mode="dontAsk", is_interactive=True)
    ctx = ToolPermissionContext(tool_use_id="t1")

    denied = await cb("Bash", {"command": "npm test"}, ctx)
    assert isinstance(denied, PermissionResultDeny)
    assert "dontAsk" in denied.message


@pytest.mark.asyncio
async def test_non_interactive_denial():
    cb = create_permission_callback(permission_mode="default", is_interactive=False)
    ctx = ToolPermissionContext(tool_use_id="t1")

    result = await cb("Bash", {"command": "echo 1"}, ctx)
    assert isinstance(result, PermissionResultDeny)
    assert "Non-interactive" in result.message


@pytest.mark.asyncio
async def test_interactive_prompt_allow_once():
    cb = create_permission_callback(permission_mode="default", is_interactive=True)
    ctx = ToolPermissionContext(
        tool_use_id="t1",
        title="Execute Bash command",
        decision_reason="Running tests",
    )

    with patch("sys.stdin.isatty", return_value=True), \
         patch("claude_agent_cli.permissions._ask", AsyncMock(return_value="y")):
        # Brackets in the command must not be read as rich markup
        result = await cb("Bash", {"command": "sed 's/[/]/_/' f"}, ctx)
        assert isinstance(result, PermissionResultAllow)


@pytest.mark.asyncio
async def test_interactive_prompt_always_allow():
    cb = create_permission_callback(permission_mode="default", is_interactive=True)
    ctx = ToolPermissionContext(
        tool_use_id="t1",
        suggestions=[PermissionUpdate.from_dict({"type": "addRules", "rules": []})],
    )

    # First time: select 'always'
    with patch("sys.stdin.isatty", return_value=True), \
         patch("claude_agent_cli.permissions._ask", AsyncMock(return_value="a")):
        result = await cb("Bash", {"command": "pytest"}, ctx)
        assert isinstance(result, PermissionResultAllow)
        # A tool-wide session rule stops the CLI asking again, so PermissionRequest hooks stay quiet.
        # The CLI drops a rule sent with "ruleContent": null, so the key must be absent.
        assert [u.to_dict() for u in result.updated_permissions] == [
            {"type": "addRules", "destination": "session", "rules": [{"toolName": "Bash"}], "behavior": "allow"}
        ]

    # Second time: should be auto-allowed without prompting input
    with patch("sys.stdin.isatty", return_value=True), \
         patch("claude_agent_cli.permissions._ask", AsyncMock(side_effect=AssertionError("Should not prompt"))):
        result2 = await cb("Bash", {"command": "pytest"}, ctx)
        assert isinstance(result2, PermissionResultAllow)


@pytest.mark.asyncio
async def test_interactive_prompt_deny():
    cb = create_permission_callback(permission_mode="default", is_interactive=True)
    ctx = ToolPermissionContext(tool_use_id="t1")

    with patch("sys.stdin.isatty", return_value=True), \
         patch("claude_agent_cli.permissions._ask", AsyncMock(return_value="d")):
        result = await cb("Bash", {"command": "curl http://bad.com"}, ctx)
        assert isinstance(result, PermissionResultDeny)
        assert result.interrupt is False


@pytest.mark.asyncio
async def test_interactive_prompt_cancel():
    cb = create_permission_callback(permission_mode="default", is_interactive=True)
    ctx = ToolPermissionContext(tool_use_id="t1")

    with patch("sys.stdin.isatty", return_value=True), \
         patch("claude_agent_cli.permissions._ask", AsyncMock(return_value="c")):
        result = await cb("Bash", {"command": "curl http://bad.com"}, ctx)
        assert isinstance(result, PermissionResultDeny)
        assert result.interrupt is True


@pytest.mark.asyncio
async def test_dynamic_permission_mode_getter():
    current_mode = "default"
    cb = create_permission_callback(permission_mode=lambda: current_mode, is_interactive=True)
    ctx = ToolPermissionContext(tool_use_id="t1")

    # In default mode with non-interactive mock: denies
    with patch("sys.stdin.isatty", return_value=False):
        res1 = await cb("Edit", {"file_path": "foo.py"}, ctx)
        assert isinstance(res1, PermissionResultDeny)

    # Change mode dynamically to acceptEdits
    current_mode = "edits"
    with patch("sys.stdin.isatty", return_value=False):
        res2 = await cb("Edit", {"file_path": "foo.py"}, ctx)
        assert isinstance(res2, PermissionResultAllow)
