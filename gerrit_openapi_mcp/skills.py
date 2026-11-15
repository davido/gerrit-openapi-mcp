"""Serve the bundled change-screen skill from the MCP server (FastMCP 4.2).

The skill teaches a client how to render the Gerrit change screen from this
bridge's read tools. Serving it here means one authoritative copy travels with
the server instead of a per-machine copy under ~/.claude/skills. It is served
under ``skill://gerrit-change-screen/`` and advertised through the Skills
extension (``skills/list``); a plain MCP client can still read the files as
resources. A discovery client materializes it locally with
``client.read_skill(uri).to_directory(...)``.
"""

from pathlib import Path

from fastmcp import FastMCP
from fastmcp.skills import Skill
from fastmcp.skills.provider import SkillCatalogProvider

# The version-controlled skill folder, resolved relative to the repo root.
SKILLS_DIR = Path(__file__).resolve().parents[1] / ".claude" / "skills"
CHANGE_SCREEN = "gerrit-change-screen"


def add_change_screen_skill(server: FastMCP) -> bool:
    """Serve the change-screen skill if its folder is present.

    Returns True when the skill was served. A missing folder (for example an
    installed wheel that did not ship it) is tolerated: the server still serves
    its tools, so serving never fails on the skill's account.
    """
    folder = SKILLS_DIR / CHANGE_SCREEN
    if not (folder / "SKILL.md").is_file():
        return False
    server.add_provider(SkillCatalogProvider([Skill.from_directory(folder)]))
    return True
