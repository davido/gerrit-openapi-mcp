import httpx2 as httpx
import pytest
from fastmcp import Client

from gerrit_openapi_mcp.cli import mounted_server
from gerrit_openapi_mcp.profiles import CREATE_DRAFT
from gerrit_openapi_mcp.skills import add_skills, tool_bindings

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


async def test_mounted_review_draft_creates_draft_through_the_mount(tmp_path):
    # The positive create path exercised through the real serve-all mount (not just
    # GuardedClient): the create reaches the transport, and publish stays absent.
    requests = []

    def record(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=b')]}\'\n{"id":"d1"}',
            headers={"Content-Type": "application/json"},
        )

    async with (
        mounted_server(
            "https://gerrit.invalid",
            {"core": "review_draft", "checks": "off", "code-owners": "off"},
            transport=httpx.MockTransport(record),
        ) as server,
        Client(server) as client,
    ):
        names = {tool.name for tool in await client.list_tools()}
        assert CREATE_DRAFT in names
        assert "postChangesChangeIdRevisionsRevisionIdReview" not in names
        result = await client.call_tool(
            CREATE_DRAFT,
            {
                "change-id": "123",
                "revision-id": "current",
                "path": "a.txt",
                "message": "m",
            },
        )
    assert not result.is_error
    assert len(requests) == 1
    assert requests[0].method == "PUT"
    assert requests[0].url.path.endswith("/changes/123/revisions/current/drafts")


async def test_plugin_off_is_omitted():
    names = await _tool_names(
        {"core": "read_only", "checks": "off", "code-owners": "read_only"}
    )
    assert not any(n.startswith("checks_") for n in names)
    assert any(n.startswith("code_owners_") for n in names)


async def test_skill_served_on_mounted_server(tmp_path, monkeypatch):
    # Sibling-independent: a synthetic skill requiring a real core read op, gated against the
    # mounted server's ACTUAL tool names (ground truth), served from a configured root.
    root = tmp_path / "skills"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill needing a core read.\n---\n# demo\n",
        encoding="utf-8",
    )
    (root / "demo" / "requires.json").write_text(
        '{"required": [{"operationId": "getChanges"}]}', encoding="utf-8"
    )
    monkeypatch.setenv("GERRIT_AGENT_SKILLS_ROOT", str(root))
    profiles = {"core": "read_only", "checks": "read_only", "code-owners": "read_only"}
    async with mounted_server(
        "https://gerrit.invalid", profiles, transport=httpx.MockTransport(_reject)
    ) as server:
        bindings = tool_bindings(await server.list_tools(run_middleware=False))
        assert "demo" in add_skills(server, bindings)
        async with Client(server) as client:
            skill = await client.read_skill("skill://demo/SKILL.md")
    assert skill.name == "demo"
