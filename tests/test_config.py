"""Tests for configuration management."""

import os
from pathlib import Path
from claude_agent_cli.config import (
    CagentConfig,
    get_api_key,
    load_config,
    mask_api_key,
    resolve_model_name,
    save_config,
)


def test_resolve_model_name():
    assert resolve_model_name("sonnet") == "claude-sonnet-5-5"
    assert resolve_model_name("sonnet 5.5") == "claude-sonnet-5-5"
    assert resolve_model_name("haiku") == "claude-haiku-5-5"
    assert resolve_model_name("opus") == "claude-opus-5-5"
    assert resolve_model_name("opus 5.5") == "claude-opus-5-5"
    assert resolve_model_name("opus-5.5") == "claude-opus-5-5"
    assert resolve_model_name("custom-model-x") == "custom-model-x"


def test_mask_api_key():
    assert "Not configured" in mask_api_key(None)
    assert mask_api_key("sk-ant-api03-abcdefghijklmn-1234") == "sk-ant-a...1234"
    assert mask_api_key("short") == "****"


def test_config_save_and_load(tmp_path, monkeypatch):
    test_config_file = tmp_path / "config.json"
    monkeypatch.setattr("claude_agent_cli.config.DEFAULT_CONFIG_FILE", test_config_file)
    monkeypatch.setattr("claude_agent_cli.config.DEFAULT_CONFIG_DIR", tmp_path)

    cfg = CagentConfig(
        anthropic_api_key="sk-ant-test-12345",
        default_model="claude-3-7-sonnet",
        default_agent="coder",
    )
    save_config(cfg)
    loaded = load_config()

    assert loaded.anthropic_api_key == "sk-ant-test-12345"
    assert loaded.default_model == "claude-3-7-sonnet"


def test_get_api_key_precedence(tmp_path, monkeypatch):
    test_config_file = tmp_path / "config.json"
    monkeypatch.setattr("claude_agent_cli.config.DEFAULT_CONFIG_FILE", test_config_file)
    monkeypatch.setattr("claude_agent_cli.config.DEFAULT_CONFIG_DIR", tmp_path)

    # 1. Explicit key has highest precedence
    assert get_api_key("explicit-key") == "explicit-key"

    # 2. Environment variable
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert get_api_key() == "env-key"

    # 3. Config file
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    save_config(CagentConfig(anthropic_api_key="cfg-key"))
    assert get_api_key() == "cfg-key"
