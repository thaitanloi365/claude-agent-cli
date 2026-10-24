"""Skills discovery and synchronization for claude-agent-cli."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich.table import Table

from claude_agent_cli.config import DEFAULT_CLAUDE_HOME, ensure_config_dirs


def get_skill_sources() -> list[Path]:
    """Get list of directories where skills may be installed."""
    home = Path.home()
    return [
        home / ".claude" / "skills",
        home / ".gemini" / "config" / "skills",
        home / ".agents" / "skills",
    ]


def parse_skill_metadata(skill_path: Path) -> dict[str, str]:
    """Extract name and description from SKILL.md frontmatter or body."""
    skill_md = skill_path / "SKILL.md"
    name = skill_path.name
    description = "No description provided"

    if skill_md.exists():
        try:
            content = skill_md.read_text(encoding="utf-8")
            # Check for YAML frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    for line in frontmatter.splitlines():
                        if line.startswith("description:"):
                            description = line.replace("description:", "").strip().strip("\"'")
                        elif line.startswith("name:"):
                            parsed_name = line.replace("name:", "").strip().strip("\"'")
                            if parsed_name:
                                name = parsed_name
            if description == "No description provided":
                # Find first non-header, non-empty line
                for line in content.splitlines():
                    cleaned = line.strip()
                    if cleaned and not cleaned.startswith("#") and not cleaned.startswith("---"):
                        description = cleaned[:100]
                        break
        except Exception:
            pass

    return {"name": name, "description": description, "path": str(skill_path)}


def sync_skills_to_claude_home(claude_home: Optional[Path] = None) -> list[str]:
    """Link all discovered global skills into cagent's isolated Claude home directory."""
    target_skills_dir = (claude_home or DEFAULT_CLAUDE_HOME) / "skills"
    target_skills_dir.mkdir(parents=True, exist_ok=True)

    linked: list[str] = []
    sources = get_skill_sources()

    for sdir in sources:
        if not sdir.exists():
            continue
        try:
            for item in sdir.iterdir():
                if item.name.startswith("."):
                    continue
                dest = target_skills_dir / item.name
                if not dest.exists():
                    try:
                        real_target = item.resolve()
                        if (real_target / "SKILL.md").exists() or real_target.is_dir():
                            dest.symlink_to(real_target)
                            linked.append(item.name)
                    except Exception:
                        pass
        except Exception:
            pass

    return linked


def discover_all_skills(project_root: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Discover all available skills from user directories and project workspace."""
    skills: dict[str, dict[str, Any]] = {}

    # 1. User global skills
    for sdir in get_skill_sources():
        if not sdir.exists():
            continue
        try:
            for item in sdir.iterdir():
                if item.name.startswith("."):
                    continue
                real_dir = item.resolve()
                if (real_dir / "SKILL.md").exists():
                    meta = parse_skill_metadata(real_dir)
                    meta["source"] = str(sdir)
                    skills[item.name] = meta
        except Exception:
            pass

    # 2. Project local skills
    if project_root:
        for pdir in [
            project_root / ".claude" / "skills",
            project_root / ".agents" / "skills",
        ]:
            if pdir.exists():
                try:
                    for item in pdir.iterdir():
                        if item.name.startswith("."):
                            continue
                        real_dir = item.resolve()
                        if (real_dir / "SKILL.md").exists():
                            meta = parse_skill_metadata(real_dir)
                            meta["source"] = f"project ({pdir.name})"
                            skills[item.name] = meta
                except Exception:
                    pass

    return skills


def render_skills_table(skills: dict[str, dict[str, Any]]) -> Table:
    """Generate a formatted table of all discovered skills."""
    table = Table(
        title="Available Skills",
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Skill Name", style="bold green", width=22)
    table.add_column("Description", style="white", min_width=35)
    table.add_column("Source", style="dim", width=24)

    if not skills:
        table.add_row("-", "No skills discovered in system or workspace", "-")
        return table

    for name in sorted(skills.keys()):
        info = skills[name]
        desc = info.get("description", "")
        if len(desc) > 65:
            desc = desc[:62] + "..."
        src = info.get("source", "")
        # Shorten path display
        src_display = src.replace(str(Path.home()), "~")
        table.add_row(name, desc, src_display)

    return table
