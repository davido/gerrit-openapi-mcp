"""Fetch change/check data through generated MCP tools; explicit read-only use."""

import argparse
import asyncio
import json
import os

import httpx2 as httpx  # the httpx fork FastMCP 4.2's OpenAPI client requires
from fastmcp import Client

from gerrit_openapi_mcp.bridge import bridge
from gerrit_openapi_mcp.profiles import API_SPECS, derive, load_catalog, load_spec
from gerrit_openapi_mcp.screen import change_screen_data


async def run(args):
    username = os.environ.get("GERRIT_HTTP_USER")
    password = os.environ.get("GERRIT_HTTP_PASSWORD")
    if bool(username) != bool(password):
        raise ValueError("Set both GERRIT_HTTP_USER and GERRIT_HTTP_PASSWORD")
    auth = httpx.BasicAuth(username, password) if username else None
    cookie = os.environ.get("GERRIT_COOKIE")
    core_spec = derive(load_spec(API_SPECS["core"]), "read_only", load_catalog("core"))
    checks_spec = derive(
        load_spec(API_SPECS["checks"]), "read_only", load_catalog("checks")
    )
    async with (
        bridge(core_spec, args.base_url, auth=auth, cookie=cookie) as core,
        bridge(checks_spec, args.base_url, auth=auth, cookie=cookie) as checks,
        Client(core) as core_client,
        Client(checks) as checks_client,
    ):
        data = await change_screen_data(
            core_client, checks_client, args.change_id, args.revision
        )
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--revision")
    asyncio.run(run(parser.parse_args()))
