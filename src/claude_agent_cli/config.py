"""Configuration management for claude-agent-cli."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load any .env from current directory or parents
load_dotenv()

DEFAULT_CONFIG_DIR = Path.home() / ".claude-agent-cli"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_CLAUDE_HOME = DEFAULT_CONFIG_DIR / "claude_home"
DEFAULT_HISTORY_FILE = DEFAULT_CONFIG_DIR / "history"
DEFAULT_CLAUDE_CLI_SETTINGS = Path.home() / ".claude" / "settings.json"

# Model aliases for convenience
MODEL_ALIASES: dict[str, str] = {
    # Opus models
    "opus": "claude-opus-5-5",
    "opus 5.5": "claude-opus-5-5",
    "opus-5.5": "claude-opus-5-5",
    "opus5.5": "claude-opus-5-5",
    "opus-5": "claude-opus-5-5",
    "opus 5": "claude-opus-5-5",
    "claude-opus-5-5": "claude-opus-5-5",
    "claude-opus-5": "claude-opus-5-5",
    "opus-3": "claude-3-opus-20240229",
    "claude-3-opus": "claude-3-opus-20240229",

    # Sonnet models
    "sonnet": "claude-sonnet-5-5",
    "sonnet 5.5": "claude-sonnet-5-5",
    "sonnet-5.5": "claude-sonnet-5-5",
    "claude-sonnet-5-5": "claude-sonnet-5-5",
    "sonnet 5": "claude-sonnet-5",
    "claude-sonnet-5": "claude-sonnet-5",
    "sonnet 4.6": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",

    # Haiku models
    "haiku": "claude-haiku-5-5",
    "haiku 5.5": "claude-haiku-5-5",
    "haiku-5.5": "claude-haiku-5-5",
    "claude-haiku-5-5": "claude-haiku-5-5",
    "haiku 4.5": "claude-haiku-4-5-20251001",
}


def resolve_model_name(model: str) -> str:
    """Resolve user-friendly model aliases to official model identifiers."""
    cleaned = model.lower().strip()
    return MODEL_ALIASES.get(cleaned, model.strip())


def detect_claude_cli_defaults() -> tuple[str, str]:
    """Detect default model and effort level from existing ~/.claude/settings.json."""
    detected_model = "claude-opus-5-5"
    detected_effort = "xhigh"

    if DEFAULT_CLAUDE_CLI_SETTINGS.exists():
        try:
            with open(DEFAULT_CLAUDE_CLI_SETTINGS, "r", encoding="utf-8") as f:
                data = json.load(f)
            model_settings = data.get("modelSettings", {})
            if isinstance(model_settings, dict) and model_settings:
                first_model = list(model_settings.keys())[0]
                detected_model = first_model
                first_effort = model_settings[first_model].get("effortLevel")
                if first_effort:
                    detected_effort = first_effort
            if data.get("effortLevel"):
                detected_effort = data.get("effortLevel")
            if data.get("model"):
                detected_model = data.get("model")
        except Exception:
            pass

    return detected_model, detected_effort


_detected_model, _detected_effort = detect_claude_cli_defaults()


class CagentConfig(BaseModel):
    """User configuration for claude-agent-cli."""

    anthropic_api_key: Optional[str] = Field(
        default=None,
        description="Anthropic API key from the Claude Console.",
    )
    default_model: str = Field(
        default=_detected_model,
        description="Default Claude model to use for agents (defaults to claude-opus-5-5).",
    )
    fallback_model: Optional[str] = Field(
        default="claude-sonnet-5-5",
        description="Fallback model if primary encounters rate limits.",
    )
    default_agent: str = Field(
        default="coder",
        description="Default subagent profile (coder, architect, reviewer, tester, debugger).",
    )
    permission_mode: str = Field(
        default="acceptEdits",
        description="Default permission mode: 'acceptEdits', 'ask', or 'bypass'.",
    )
    extended_thinking: bool = Field(
        default=True,
        description="Whether to enable adaptive extended thinking on supported models.",
    )
    thinking_budget_tokens: Optional[int] = Field(
        default=None,
        description="Fixed token budget for extended thinking (None for adaptive).",
    )
    effort: Optional[str] = Field(
        default=_detected_effort,
        description="Effort level for reasoning: 'low', 'medium', 'high', 'xhigh', or 'max'.",
    )
    show_thinking: bool = Field(
        default=True,
        description="Whether to display thinking/reasoning blocks in the terminal.",
    )
    stream: bool = Field(
        default=True,
        description="Whether to stream tokens and thinking in real time.",
    )
    reuse_claude_cli_config: bool = Field(
        default=True,
        description="Automatically inherit settings, permissions, hooks, and CLAUDE.md from Claude CLI (~/.claude/settings.json).",
    )
    claude_settings_path: Optional[Path] = Field(
        default=DEFAULT_CLAUDE_CLI_SETTINGS,
        description="Path to existing Claude CLI settings.json to reuse.",
    )
    claude_home: Path = Field(
        default=DEFAULT_CLAUDE_HOME,
        description="Isolated Claude Code config directory so cagent never touches your global Claude Code login.",
    )
    max_budget_per_run_usd: Optional[float] = Field(
        default=None,
        description="Optional safety budget limit in USD per individual run.",
    )


def ensure_config_dirs() -> None:
    """Ensure configuration and isolated Claude home directories exist."""
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_CLAUDE_HOME.mkdir(parents=True, exist_ok=True)


def load_config() -> CagentConfig:
    """Load configuration from disk, falling back to defaults."""
    ensure_config_dirs()
    if DEFAULT_CONFIG_FILE.exists():
        try:
            with open(DEFAULT_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return CagentConfig(**data)
        except Exception:
            return CagentConfig()
    return CagentConfig()


def save_config(config: CagentConfig) -> None:
    """Save configuration to disk."""
    ensure_config_dirs()
    with open(DEFAULT_CONFIG_FILE, "w", encoding="utf-8") as f:
        dumped = config.model_dump(mode="json")
        json.dump(dumped, f, indent=2)


def get_api_key(explicit_key: Optional[str] = None) -> Optional[str]:
    """Resolve Anthropic API key with precedence:

    1. Explicit CLI flag (--api-key)
    2. Environment variable ANTHROPIC_API_KEY
    3. Project .env file
    4. Saved config in ~/.claude-agent-cli/config.json
    """
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()

    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    config = load_config()
    if config.anthropic_api_key and config.anthropic_api_key.strip():
        return config.anthropic_api_key.strip()

    return None


def mask_api_key(key: Optional[str]) -> str:
    """Mask API key for display (e.g. sk-ant-...3f8a)."""
    if not key:
        return "[red]Not configured[/red]"
    if len(key) <= 12:
        return "****"
    return f"{key[:8]}...{key[-4:]}"
