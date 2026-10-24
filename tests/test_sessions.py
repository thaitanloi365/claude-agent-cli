"""Tests for listing cagent's saved sessions."""

import json
import re
from unittest.mock import MagicMock

from claude_agent_cli import sessions


def test_get_sessions_reads_cagent_claude_home(tmp_path, monkeypatch):
    """Sessions come from cagent's isolated claude_home, not ~/.claude."""
    claude_home = tmp_path / "claude_home"
    workspace = tmp_path / "work"
    workspace.mkdir()
    project = claude_home / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(workspace.resolve()))
    project.mkdir(parents=True)
    session_id = "11111111-2222-3333-4444-555555555555"
    line = {
        "type": "user",
        "sessionId": session_id,
        "cwd": str(workspace),
        "timestamp": "2026-10-25T00:00:00.000Z",
        "message": {"role": "user", "content": "hello"},
    }
    (project / f"{session_id}.jsonl").write_text(json.dumps(line) + "\n")

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(sessions, "load_config", lambda: MagicMock(claude_home=claude_home))

    assert [s.session_id for s in sessions.get_sessions(str(workspace))] == [session_id]
