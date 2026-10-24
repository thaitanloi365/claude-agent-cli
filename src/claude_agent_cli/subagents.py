"""Specialized subagent profiles for claude-agent-cli."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional
from claude_agent_sdk import AgentDefinition
from claude_agent_cli.config import DEFAULT_CONFIG_DIR

BUILTIN_AGENTS: dict[str, dict[str, Any]] = {
    "coder": {
        "description": "Expert full-stack coding assistant. Writes clean code, refactors, creates files, and runs terminal commands.",
        "prompt": (
            "You are an elite software engineer paired with the user. "
            "You write idiomatic, high-performance, maintainable code following best practices. "
            "When implementing changes, read relevant files first, make precise and surgical edits, "
            "and verify your work by running tests or build commands when applicable."
        ),
        "tools": ["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
    },
    "architect": {
        "description": "System architect specialist. Analyzes system architecture, plans complex refactors, and evaluates technical trade-offs.",
        "prompt": (
            "You are a principal software architect. You specialize in system design, component boundaries, "
            "API design, scalability, and technical strategy. Provide clear, structured recommendations with "
            "concrete trade-offs. You have read access to inspect the codebase thoroughly."
        ),
        "tools": ["Read", "Grep", "Glob"],
    },
    "reviewer": {
        "description": "Security and code quality auditor. Scrutinizes code for bugs, edge cases, security vulnerabilities, and antipatterns.",
        "prompt": (
            "You are a rigorous code reviewer and security auditor. "
            "Inspect code for logic flaws, security vulnerabilities (OWASP top 10), performance regressions, "
            "concurrency bugs, and maintainability concerns. Point directly to specific file locations and offer actionable fixes."
        ),
        "tools": ["Read", "Grep", "Glob"],
    },
    "tester": {
        "description": "QA and test automation engineer. Writes unit, integration, and e2e tests, and runs test suites to verify behavior.",
        "prompt": (
            "You are a senior test engineer. Your mission is to ensure robust test coverage. "
            "Analyze existing test suites, identify missing edge cases, write comprehensive tests, "
            "and run tests via bash commands to ensure they pass."
        ),
        "tools": ["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
    },
    "debugger": {
        "description": "Troubleshooting specialist. Investigates stack traces, formulates hypotheses, isolates root causes, and validates fixes.",
        "prompt": (
            "You are a debugging expert. When diagnosing an issue, analyze error logs and stack traces, "
            "formulate clear hypotheses, locate the minimal reproduction, and propose surgical fixes without unintended side effects."
        ),
        "tools": ["Read", "Edit", "Bash", "Grep", "Glob"],
    },
}


def load_custom_agents(project_root: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Load user-defined custom agents from global and local config files."""
    custom_agents: dict[str, dict[str, Any]] = {}

    # Global custom agents: ~/.claude-agent-cli/agents.json
    global_file = DEFAULT_CONFIG_DIR / "agents.json"
    if global_file.exists():
        try:
            with open(global_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    custom_agents.update(data)
        except Exception:
            pass

    # Project-local agents: .claude-agent/agents.json or .claude/agents.json
    if project_root:
        for local_path in [
            project_root / ".claude-agent" / "agents.json",
            project_root / ".claude" / "agents.json",
        ]:
            if local_path.exists():
                try:
                    with open(local_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            custom_agents.update(data)
                except Exception:
                    pass

    return custom_agents


def get_agent_definitions(
    default_model: str = "claude-3-7-sonnet",
    project_root: Optional[Path] = None,
) -> dict[str, AgentDefinition]:
    """Build the dictionary of AgentDefinition objects for ClaudeAgentOptions."""
    all_specs: dict[str, dict[str, Any]] = {}
    all_specs.update(BUILTIN_AGENTS)
    all_specs.update(load_custom_agents(project_root))

    definitions: dict[str, AgentDefinition] = {}
    for name, spec in all_specs.items():
        tools = spec.get("tools", ["Read", "Write", "Edit", "Bash", "Grep", "Glob"])
        prompt = spec.get("prompt", "You are a helpful software engineering assistant.")
        description = spec.get("description", f"Specialized {name} agent")
        model = spec.get("model", default_model)

        definitions[name] = AgentDefinition(
            description=description,
            prompt=prompt,
            tools=tools,
            model=model,
        )

    return definitions


def get_agent_spec(name: str, project_root: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Get metadata for a specific agent by name."""
    if name in BUILTIN_AGENTS:
        return BUILTIN_AGENTS[name]
    custom = load_custom_agents(project_root)
    return custom.get(name)
