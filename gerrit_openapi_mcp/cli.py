"""Generate catalogs without networking, or launch an opt-in stdio bridge."""

import argparse
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path

import httpx
from fastmcp import Client

from .bridge import bridge
from .profiles import API_SPECS, PROFILES, derive, load_catalog, load_spec, operations


async def generate(spec: dict, output: Path, *, api: str = "core"):
    output.mkdir(parents=True, exist_ok=True)
    summary = {}
    for profile in PROFILES:
        derived = derive(spec, profile, load_catalog(api))
        # Mock transport is deliberate: generating a catalog must never contact Gerrit.
        transport = httpx.MockTransport(lambda req: httpx.Response(500))
        async with (
            bridge(derived, "https://gerrit.invalid", transport=transport) as server,
            Client(server) as client,
        ):
            tools = await client.list_tools()
        count = sum(1 for _ in operations(derived))
        if len(tools) != count or len({tool.name for tool in tools}) != count:
            raise ValueError("Incomplete tool registration or duplicate tool names")
        (output / f"{profile}-openapi.json").write_text(
            json.dumps(derived, indent=2) + "\n"
        )
        (output / f"{profile}-tools.json").write_text(
            json.dumps(
                [
                    tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for tool in tools
                ],
                indent=2,
            )
            + "\n"
        )
        summary[profile] = {"operations": count, "tools": len(tools)}
    manifest = {
        "api": api,
        "fastmcp": importlib.metadata.version("fastmcp"),
        "source_version": spec["info"]["version"],
        "source_sha256": derive(spec, "full", load_catalog(api))["x-agent-profile"][
            "source_sha256"
        ],
        "profiles": summary,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


async def serve(spec: dict, profile: str, base_url: str, *, api: str = "core"):
    username = os.environ.get("GERRIT_HTTP_USER")
    password = os.environ.get("GERRIT_HTTP_PASSWORD")
    if bool(username) != bool(password):
        raise ValueError("Set both GERRIT_HTTP_USER and GERRIT_HTTP_PASSWORD")
    auth = httpx.BasicAuth(username, password) if username else None
    async with bridge(
        derive(spec, profile, load_catalog(api)),
        base_url,
        auth=auth,
        cookie=os.environ.get("GERRIT_COOKIE"),
    ) as server:
        await server.run_async(transport="stdio", show_banner=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", choices=API_SPECS, default="core")
    parser.add_argument("--spec", type=Path, help="Override the selected API spec path")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser(
        "generate", help="Write all tool catalogs; no HTTP requests"
    )
    build.add_argument("--output", type=Path)
    launch = commands.add_parser("serve", help="Start a stdio MCP server")
    launch.add_argument("--base-url", required=True)
    launch.add_argument("--profile", choices=PROFILES, default="read_only")
    args = parser.parse_args()
    spec = load_spec(args.spec or API_SPECS[args.api])
    if args.command == "generate":
        output = args.output or (
            Path("generated") if args.api == "core" else Path("generated") / args.api
        )
        asyncio.run(generate(spec, output, api=args.api))
    else:
        asyncio.run(serve(spec, args.profile, args.base_url, api=args.api))


if __name__ == "__main__":
    main()
