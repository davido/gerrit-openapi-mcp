import json
import subprocess
import sys

import httpx2 as httpx
import pytest
from fastmcp import Client

from gerrit_openapi_mcp.bridge import GuardedClient, bridge
from gerrit_openapi_mcp.profiles import (
    API_SPECS,
    derive,
    load_catalog,
    load_spec,
    operations,
)
from gerrit_openapi_mcp.screen import change_screen_data

LIST = "getChangesChangeIdRevisionsRevisionIdChecks"
GET = "getChangesChangeIdRevisionsRevisionIdChecksCheckId"
RERUN = "postChangesChangeIdRevisionsRevisionIdChecksCheckIdRerun"
CHECK = {
    "repository": "test/repo",
    "change_number": 123,
    "patch_set_id": 7,
    "checker_uuid": "ci:unit",
    "state": "FAILED",
    "message": "One test failed",
    "url": "https://ci.invalid/jobs/1",
    "checker_name": "Unit tests",
    "checker_description": "Unit test suite",
    "checker_status": "ENABLED",
    "submit_impact": {"required": True},
}


@pytest.fixture(scope="module")
def source():
    return load_spec(API_SPECS["checks"])


def checks_spec(source, profile="read_only"):
    return derive(source, profile, load_catalog("checks"))


@pytest.mark.parametrize("profile,count", [("full", 9), ("read_only", 4), ("agent", 4)])
async def test_checks_catalog_and_profile_omission(source, profile, count):
    def unexpected(request):
        pytest.fail("Listing checks tools attempted HTTP")

    spec = checks_spec(source, profile)
    async with (
        bridge(
            spec, "https://gerrit.invalid", transport=httpx.MockTransport(unexpected)
        ) as server,
        Client(server) as client,
    ):
        tools = await client.list_tools()
    assert len(tools) == count
    names = {t.name for t in tools}
    assert LIST in names and GET in names
    if profile != "full":
        assert RERUN not in names
        assert all(method == "GET" for method, _, _ in operations(spec))


@pytest.mark.parametrize(
    "name,args,payload,suffix",
    [
        (
            LIST,
            {"change-id": "123", "revision-id": "7"},
            [CHECK],
            "/changes/123/revisions/7/checks",
        ),
        (
            GET,
            {
                "change-id": "123",
                "revision-id": "7",
                "check-id": "ci:unit",
                "o": ["CHECKER"],
            },
            CHECK,
            "/changes/123/revisions/7/checks/ci:unit",
        ),
        (
            "getPluginsChecksCheckers",
            {},
            [{"uuid": "ci:unit", "name": "Unit tests"}],
            "/plugins/checks/checkers",
        ),
        (
            "getPluginsChecksCheckersCheckerId",
            {"checker-id": "ci:unit"},
            {"uuid": "ci:unit", "name": "Unit tests"},
            "/plugins/checks/checkers/ci:unit",
        ),
    ],
)
async def test_checks_reads_preserve_paths_xssi_and_screen_fields(
    source, name, args, payload, suffix
):
    requests = []

    def record(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=b")]}'\n" + json.dumps(payload).encode(),
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )

    async with (
        bridge(
            checks_spec(source),
            "https://gerrit.invalid/site/a/",
            transport=httpx.MockTransport(record),
        ) as server,
        Client(server) as client,
    ):
        result = await client.call_tool(name, args)
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/site/a" + suffix
    assert result.structured_content == (
        {"result": payload} if isinstance(payload, list) else payload
    )
    if name == GET:
        assert requests[0].url.params.get_list("o") == ["CHECKER"]
        assert result.structured_content["submit_impact"]["required"] is True


@pytest.mark.parametrize("profile", ["read_only", "agent"])
async def test_every_checks_write_blocked_by_transport(source, profile):
    requests = []
    transport = httpx.MockTransport(
        lambda req: requests.append(req) or httpx.Response(200, json={})
    )
    async with GuardedClient(
        checks_spec(source, profile),
        base_url="https://gerrit.invalid",
        transport=transport,
    ) as client:
        for method, path, _ in operations(source):
            if method == "GET":
                continue
            path = (
                path.replace("{change-id}", "123")
                .replace("{revision-id}", "7")
                .replace("{check-id}", "ci:unit")
                .replace("{checker-id}", "ci:unit")
            )
            with pytest.raises(PermissionError):
                await client.request(method, path, json={"state": "SUCCESSFUL"})
    assert requests == []


async def test_explicit_full_profile_rerun_uses_generated_endpoint(source):
    requests = []

    def record(request):
        requests.append(request)
        return httpx.Response(200, json={**CHECK, "state": "NOT_STARTED"})

    async with (
        bridge(
            checks_spec(source, "full"),
            "https://gerrit.invalid/a/",
            transport=httpx.MockTransport(record),
        ) as server,
        Client(server) as client,
    ):
        result = await client.call_tool(
            RERUN,
            {
                "change-id": "123",
                "revision-id": "7",
                "check-id": "ci:unit",
                "notify": "NONE",
            },
        )
    assert requests[0].method == "POST"
    assert requests[0].url.path.endswith("/checks/ci:unit/rerun")
    assert json.loads(requests[0].content) == {"notify": "NONE"}
    assert result.structured_content["state"] == "NOT_STARTED"


def test_checks_cli_offline_generation(source, tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gerrit_openapi_mcp.cli",
            "--api",
            "checks",
            "generate",
            "--output",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    manifest = json.loads(result.stdout)
    assert manifest["api"] == "checks"
    assert manifest["profiles"]["full"]["tools"] == 9
    assert manifest["profiles"]["read_only"]["tools"] == 4
    assert json.loads((tmp_path / "agent-tools.json").read_text())


@pytest.mark.parametrize("runs", [[], [CHECK]])
async def test_change_screen_composes_generated_core_and_checks_tools(source, runs):
    requests = []

    def record(request):
        requests.append(request)
        if request.url.path.endswith("/detail"):
            payload = {
                "_number": 123,
                "subject": "Example change",
                "current_revision": "abc123",
            }
        elif request.url.path.endswith("/checks"):
            payload = runs
        else:
            assert request.url.params.get_list("o") == ["CHECKER"]
            payload = CHECK
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(record)
    async with (
        bridge(
            derive(load_spec(), "read_only"),
            "https://gerrit.invalid/a/",
            transport=transport,
        ) as core,
        bridge(
            checks_spec(source), "https://gerrit.invalid/a/", transport=transport
        ) as checks,
        Client(core) as core_client,
        Client(checks) as checks_client,
    ):
        data = await change_screen_data(core_client, checks_client, "123")
    assert data["change"]["subject"] == "Example change"
    assert data["revision"] == "abc123"
    assert data["checks"] == runs
    assert all(request.method == "GET" for request in requests)
    assert len(requests) == 2 + len(runs)
