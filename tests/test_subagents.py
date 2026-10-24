"""Tests for subagents definitions and loader."""

import json
from pathlib import Path
from claude_agent_cli.subagents import (
    BUILTIN_AGENTS,
    get_agent_definitions,
    get_agent_spec,
    load_custom_agents,
)


def test_builtin_agents_exist():
    assert "coder" in BUILTIN_AGENTS
    assert "architect" in BUILTIN_AGENTS
    assert "reviewer" in BUILTIN_AGENTS
    assert "tester" in BUILTIN_AGENTS
    assert "debugger" in BUILTIN_AGENTS


def test_get_agent_spec():
    spec = get_agent_spec("coder")
    assert spec is not None
    assert "Read" in spec["tools"]
    assert "Bash" in spec["tools"]


def test_load_custom_agents(tmp_path, monkeypatch):
    custom_agents_file = tmp_path / "agents.json"
    with open(custom_agents_file, "w") as f:
        json.dump(
            {
                "devops": {
                    "description": "DevOps automation agent",
                    "prompt": "Manage docker and k8s",
                    "tools": ["Bash", "Read"],
                }
            },
            f,
        )

    monkeypatch.setattr("claude_agent_cli.subagents.DEFAULT_CONFIG_DIR", tmp_path)
    custom = load_custom_agents()
    assert "devops" in custom
    assert custom["devops"]["tools"] == ["Bash", "Read"]


def test_get_agent_definitions():
    defs = get_agent_definitions(default_model="claude-3-7-sonnet")
    assert "coder" in defs
    assert defs["coder"].model == "claude-3-7-sonnet"
    assert "Edit" in defs["coder"].tools
