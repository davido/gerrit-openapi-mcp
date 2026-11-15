"""Serve the bundled skills from the MCP server (FastMCP 4.2).

A skill teaches a client how to drive this bridge's read tools (rendering a
change screen, a changes list, ...). Serving them here means one authoritative
copy travels with the server instead of a per-machine copy under
~/.claude/skills. Each is served under ``skill://<name>/`` and advertised through
the Skills extension (``skills/list``); a plain MCP client can still read the
files as resources, and a discovery client materializes one locally with
``client.read_skill(uri).to_directory(...)``.
"""

import importlib.resources
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.skills import Skill
from fastmcp.skills.provider import SkillCatalogProvider

# Folder name must equal each skill's SKILL.md `name`. A name listed here whose
# folder is absent is simply skipped, so this list may lead the folders.
SKILL_NAMES = ("gerrit-change-screen", "gerrit-changes-list")


def skills_dir() -> Path:
    """The directory holding skill folders, in a source tree or a wheel."""
    source = Path(__file__).resolve().parents[1] / ".claude" / "skills"
    if source.is_dir():
        return source
    return Path(str(importlib.resources.files(__package__))) / "skills_data"


def add_skills(server: FastMCP) -> list[str]:
    """Serve every bundled skill whose folder is present; return the served names.

    A missing folder (for example a wheel that did not ship it) is tolerated:
    the server still serves its tools, so serving never fails on a skill.
    """
    root = skills_dir()
    skills, served = [], []
    for name in SKILL_NAMES:
        if (root / name / "SKILL.md").is_file():
            skills.append(Skill.from_directory(root / name))
            served.append(name)
    if skills:
        server.add_provider(SkillCatalogProvider(skills))
    return served
