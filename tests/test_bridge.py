import copy
import json

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from gerrit_openapi_mcp.bridge import GuardedClient, bridge
from gerrit_openapi_mcp.profiles import REVIEW, derive, load_spec, operations


@pytest.fixture(scope="module")
def source():
    return load_spec()


@pytest.mark.parametrize(
    "profile,count", [("full", 339), ("read_only", 161), ("agent", 162)]
)
async def test_actual_spec_registers_complete_profile(source, profile, count):
    def unexpected(request):
        pytest.fail("Listing tools attempted an HTTP request")

    spec = derive(source, profile)
    async with (
        bridge(
            spec, "https://gerrit.invalid", transport=httpx.MockTransport(unexpected)
        ) as server,
        Client(server) as client,
    ):
        tools = await client.list_tools()
    assert len(tools) == count
    assert len({tool.name for tool in tools}) == count
    if profile != "full":
        assert all(
            method == "GET" or op["operationId"] == REVIEW
            for method, _, op in operations(spec)
        )
        names = {tool.name for tool in tools}
        assert "postChangesChangeIdSubmit" not in names
        assert "postChangesChangeIdRevisionsRevisionIdSubmit" not in names
        assert "postChangesChangeIdAbandon" not in names
        assert not any(name.startswith("delete") for name in names)


async def test_allowed_read_query_and_xssi_response(source):
    calls = []

    def record(request):
        calls.append(request)
        return httpx.Response(
            200, content=b")]}'\n[]", headers={"Content-Type": "application/json"}
        )

    async with (
        bridge(
            derive(source, "read_only"),
            "https://gerrit.invalid/a/",
            transport=httpx.MockTransport(record),
        ) as server,
        Client(server) as client,
    ):
        result = await client.call_tool(
            "getChanges", {"query": ["status:open", "owner:self"]}
        )
    assert not result.is_error
    assert calls[0].url.path == "/a/changes"
    assert calls[0].url.params.get_list("query") == ["status:open", "owner:self"]
    assert result.structured_content == {"result": []}


@pytest.mark.parametrize("vote", [-1, 0, 1])
async def test_bounded_review_reaches_transport(source, vote):
    bodies = []

    def record(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"labels": {"Code-Review": vote}})

    async with (
        bridge(
            derive(source, "agent"),
            "https://gerrit.invalid",
            transport=httpx.MockTransport(record),
        ) as server,
        Client(server) as client,
    ):
        result = await client.call_tool(
            REVIEW,
            {
                "change-id": "123",
                "revision-id": "current",
                "labels": {"Code-Review": vote},
                "is_ai": True,
            },
        )
    assert not result.is_error
    assert bodies == [{"labels": {"Code-Review": vote}, "is_ai": True}]


@pytest.mark.parametrize(
    "labels",
    [
        {"Code-Review": -2},
        {"Code-Review": 2},
        {"Code-Review": "1"},
        {"Code-Review": True},
        {"Code-Review": 1.0},
        {"Verified": 1},
    ],
)
async def test_forbidden_vote_never_reaches_transport(source, labels):
    calls = []
    transport = httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, json={})
    )
    async with (
        bridge(
            derive(source, "agent"), "https://gerrit.invalid", transport=transport
        ) as server,
        Client(server) as client,
    ):
        with pytest.raises(ToolError):
            await client.call_tool(
                REVIEW,
                {"change-id": "123", "revision-id": "current", "labels": labels},
            )
    assert calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("on_behalf_of", "bot"),
        ("ready", True),
        ("work_in_progress", True),
        ("reviewers", []),
        ("Authorization__header", "other-identity"),
    ],
)
async def test_forbidden_review_fields_never_reach_transport(source, field, value):
    calls = []
    transport = httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, json={})
    )
    async with (
        bridge(
            derive(source, "agent"), "https://gerrit.invalid", transport=transport
        ) as server,
        Client(server) as client,
    ):
        with pytest.raises(ToolError):
            await client.call_tool(
                REVIEW,
                {
                    "change-id": "123",
                    "revision-id": "current",
                    field: value,
                },
            )
    assert calls == []


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/changes/123/submit", {}),
        ("POST", "/changes/123/abandon", {}),
        ("DELETE", "/changes/123", None),
        ("PUT", "/accounts/self/password.http", {}),
    ],
)
async def test_network_guard_rejects_hidden_operations_directly(
    source, method, path, body
):
    calls = []
    transport = httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, json={})
    )
    async with GuardedClient(
        derive(source, "agent"), base_url="https://gerrit.invalid", transport=transport
    ) as client:
        with pytest.raises(PermissionError):
            await client.request(method, path, json=body)
    assert not calls


async def test_transport_validates_review_even_without_tool_schema(source):
    calls = []
    transport = httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, json={})
    )
    async with GuardedClient(
        derive(source, "agent"), base_url="https://gerrit.invalid", transport=transport
    ) as client:
        with pytest.raises(Exception, match="Additional properties"):
            await client.post(
                "/changes/123/revisions/current/review", json={"on_behalf_of": "bot"}
            )
    assert not calls


async def test_redirect_is_not_followed(source):
    calls = []

    def record(request):
        calls.append(request)
        return httpx.Response(
            302, headers={"Location": "https://elsewhere.invalid/secret"}
        )

    async with GuardedClient(
        derive(source, "read_only"),
        base_url="https://gerrit.invalid",
        transport=httpx.MockTransport(record),
    ) as client:
        response = await client.get("/changes", follow_redirects=True)
    assert response.status_code == 302
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://elsewhere.invalid/changes",
        "https://gerrit.invalid/a/changes?$m=POST",
        "https://gerrit.invalid/a/changes/%252e%252e",
    ],
)
async def test_origin_and_override_rejections(source, url):
    calls = []
    transport = httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, json={})
    )
    async with GuardedClient(
        derive(source, "agent"),
        base_url="https://gerrit.invalid/a/",
        transport=transport,
    ) as client:
        with pytest.raises(PermissionError):
            await client.get(url)
    assert not calls


def test_new_operation_and_schema_change_fail_generation(source):
    for mutate in ("operation", "schema"):
        changed = copy.deepcopy(source)
        if mutate == "operation":
            changed["paths"]["/new"] = {
                "post": {"operationId": "postNew", "responses": {}}
            }
        else:
            changed["components"]["schemas"]["ReviewInput"]["properties"][
                "new_field"
            ] = {"type": "string"}
        with pytest.raises(ValueError, match="Spec changed"):
            derive(changed, "agent")


def test_deriving_agent_does_not_modify_canonical_schema(source):
    original = copy.deepcopy(source)
    derived = derive(source, "agent")
    assert source == original
    labels = derived["components"]["schemas"]["AgentReviewInput"]["properties"][
        "labels"
    ]
    assert labels["properties"]["Code-Review"]["maximum"] == 1
    assert labels["additionalProperties"] is False


@pytest.mark.parametrize("strict", [False, True])
async def test_stock_fastmcp_validation_of_derived_bound(source, strict):
    """Test the pinned framework itself, without the prototype's outgoing guard."""
    bodies = []

    def record(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        base_url="https://gerrit.invalid", transport=httpx.MockTransport(record)
    ) as http:
        server = FastMCP.from_openapi(
            openapi_spec=derive(source, "agent"),
            client=http,
            strict_input_validation=strict,
            validate_output=False,
        )
        async with Client(server) as client:
            args = {"change-id": "123", "revision-id": "current"}
            await client.call_tool(REVIEW, {**args, "labels": {"Code-Review": 1}})
            for labels in ({"Code-Review": 2}, {"Verified": 1}):
                if strict:
                    with pytest.raises(ToolError):
                        await client.call_tool(REVIEW, {**args, "labels": labels})
                else:
                    await client.call_tool(REVIEW, {**args, "labels": labels})
    assert len(bodies) == (1 if strict else 3)
