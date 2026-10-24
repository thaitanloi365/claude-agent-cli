"""Tests for skills and MCP discovery and filtering."""

from pathlib import Path
from claude_agent_cli.mcp import get_active_mcp_servers
from claude_agent_cli.skills import discover_all_skills, parse_skill_metadata


def test_parse_skill_metadata(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\nname: my-skill\ndescription: Custom test skill\n---\n# My Skill\nContent here",
        encoding="utf-8",
    )

    meta = parse_skill_metadata(skill_dir)
    assert meta["name"] == "my-skill"
    assert meta["description"] == "Custom test skill"


def test_mcp_filtering():
    servers = get_active_mcp_servers(filter_names=["github", "filesystem"])
    # If github or filesystem exist in discovered configs, they should be in the filtered list
    for name in servers:
        assert name in ("github", "filesystem")
