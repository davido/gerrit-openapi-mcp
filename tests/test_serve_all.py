import httpx2 as httpx
import pytest
from fastmcp import Client

from gerrit_openapi_mcp.cli import mounted_server
from gerrit_openapi_mcp.skills import add_skills

# The tools the change-screen skill calls: core reads (bare) + the prefixed
# checks read. If the mount changed names, this cross-check catches it.
CHANGE_SCREEN_TOOLS = {
    "getChangesChangeIdDetail",
    "getChangesChangeIdComments",
    "getChangesChangeIdRevisionsRevisionIdFiles",
    "getChangesChangeIdRevisionsRevisionIdRelated",
    "getChangesChangeIdSubmittedTogether",
    "checks_getChangesChangeIdRevisionsRevisionIdChecks",
}


def _reject(request):
    pytest.fail("Listing tools attempted an HTTP request")


async def _tool_names(profiles):
    async with (
        mounted_server(
            "https://gerrit.invalid", profiles, transport=httpx.MockTransport(_reject)
        ) as server,
        Client(server) as client,
    ):
        return {tool.name for tool in await client.list_tools()}


async def test_mounted_server_merges_core_bare_and_plugins_prefixed():
    names = await _tool_names(
        {"core": "read_only", "checks": "read_only", "code-owners": "read_only"}
    )
    # Core is unprefixed; plugins carry their plugin-name prefix.
    assert {"getChanges", "getChangesChangeIdDetail"} <= names
    assert any(n.startswith("checks_") for n in names)
    assert any(n.startswith("code_owners_") for n in names)
    assert not any(n.startswith("core_") for n in names)
    # Every tool the change-screen skill calls resolves on the mounted server.
    assert CHANGE_SCREEN_TOOLS <= names


async def test_plugin_off_is_omitted():
    names = await _tool_names(
        {"core": "read_only", "checks": "off", "code-owners": "read_only"}
    )
    assert not any(n.startswith("checks_") for n in names)
    assert any(n.startswith("code_owners_") for n in names)


async def test_skill_served_on_mounted_server():
    profiles = {"core": "read_only", "checks": "read_only", "code-owners": "read_only"}
    async with mounted_server(
        "https://gerrit.invalid", profiles, transport=httpx.MockTransport(_reject)
    ) as server:
        assert "gerrit-change-screen" in add_skills(server)
        async with Client(server) as client:
            skill = await client.read_skill("skill://gerrit-change-screen/SKILL.md")
    assert skill.name == "gerrit-change-screen"
