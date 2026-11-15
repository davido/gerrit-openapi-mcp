import pytest
from fastmcp import Client, FastMCP
from fastmcp.skills import SkillsClientExtension

from gerrit_openapi_mcp.skills import add_skills

CHANGE_SCREEN = "gerrit-change-screen"


@pytest.fixture
def server():
    mcp = FastMCP("test")
    assert CHANGE_SCREEN in add_skills(mcp)
    return mcp


async def test_skill_is_discoverable_via_extension(server):
    async with Client(server, extensions=[SkillsClientExtension()]) as client:
        entries = await client.list_skills()
        names = {entry.frontmatter.name for entry in entries}
        assert CHANGE_SCREEN in names
        uri = next(
            entry.uri for entry in entries if entry.frontmatter.name == CHANGE_SCREEN
        )
        skill = await client.read_skill(uri)
    assert skill.name == CHANGE_SCREEN
    # Both authored files travel with the skill, verified against the manifest.
    assert set(skill.files) == {"SKILL.md", "template.html"}


async def test_skill_files_readable_without_the_extension(server):
    async with Client(server) as client:
        skill = await client.read_skill(f"skill://{CHANGE_SCREEN}/SKILL.md")
    assert skill.name == CHANGE_SCREEN
    assert "template.html" in skill.files
