"""Generate catalogs without networking, or launch an opt-in stdio bridge."""

import argparse
import asyncio
import importlib.metadata
import json
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx2 as httpx  # the httpx fork FastMCP 4.2's OpenAPI client requires
from fastmcp import Client, FastMCP

from .bridge import bridge
from .generated import load_generated_spec
from .profiles import API_SPECS, PROFILES, derive, load_catalog, load_spec, operations
from .skills import add_skills, tool_bindings


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


def _upstream_auth():
    username = os.environ.get("GERRIT_HTTP_USER")
    password = os.environ.get("GERRIT_HTTP_PASSWORD")
    if bool(username) != bool(password):
        raise ValueError("Set both GERRIT_HTTP_USER and GERRIT_HTTP_PASSWORD")
    return httpx.BasicAuth(username, password) if username else None


async def serve(profile: str, base_url: str, *, api: str = "core"):
    async with bridge(
        load_generated_spec(api, profile),
        base_url,
        auth=_upstream_auth(),
        cookie=os.environ.get("GERRIT_COOKIE"),
    ) as server:
        # Host configured skills in every serve mode (so the skills-root config is honored
        # and validated here too), advertising only those whose required operations are served
        # under their own name. Bindings come from the real provider's tools, so FastMCP's
        # renames AND collision suffixes are reflected -- a reconstructed name set is not safe.
        bindings = tool_bindings(await server.list_tools(run_middleware=False))
        add_skills(server, bindings)
        await server.run_async(transport="stdio", show_banner=False)


@asynccontextmanager
async def mounted_server(
    base_url: str, profiles: dict[str, str], *, transport=None, auth=None, cookie=None
):
    """One server mounting core plus any enabled plugins.

    Core mounts unprefixed -- it is the primary surface, and the change-screen
    skill references core operationIds by their bare name. Each plugin mounts
    under its own name (checks_, code_owners_), mirroring Gerrit's own
    /plugins/<name>/ REST namespacing and keeping the flat merge collision-safe
    as plugins evolve. Each API keeps its own GuardedClient and profile, so the
    per-origin guard and the per-API profile are preserved. A plugin whose
    profile is "off" is omitted (the site may not have it). The sub-bridges
    stay open for the server's lifetime.
    """
    root = FastMCP("Gerrit")
    async with AsyncExitStack() as stack:
        for api in API_SPECS:
            if profiles[api] == "off":
                continue
            sub = await stack.enter_async_context(
                bridge(
                    load_generated_spec(api, profiles[api]),
                    base_url,
                    transport=transport,
                    auth=auth,
                    cookie=cookie,
                )
            )
            namespace = None if api == "core" else api.replace("-", "_")
            root.mount(sub, namespace=namespace)
        yield root


async def serve_all(base_url: str, profiles: dict[str, str]):
    async with mounted_server(
        base_url,
        profiles,
        auth=_upstream_auth(),
        cookie=os.environ.get("GERRIT_COOKIE"),
    ) as server:
        # Gate skills against the mounted server's real operationId -> tool-name bindings
        # (ground truth): the mount namespaces plugin tools and FastMCP caps/suffixes names,
        # so only a binding built from the actual tools reflects what a skill can call.
        bindings = tool_bindings(await server.list_tools(run_middleware=False))
        add_skills(server, bindings)
        await server.run_async(transport="stdio", show_banner=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", choices=API_SPECS, default="core")
    parser.add_argument("--spec", type=Path, help="Override the spec path (generate)")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser(
        "generate", help="Write all tool catalogs; no HTTP requests"
    )
    build.add_argument("--output", type=Path)
    launch = commands.add_parser("serve", help="Serve one API over stdio")
    launch.add_argument("--base-url", required=True)
    launch.add_argument("--profile", choices=PROFILES, default="read_only")
    every = commands.add_parser(
        "serve-all", help="Serve core plus plugins mounted in one stdio server"
    )
    every.add_argument("--base-url", required=True)
    every.add_argument("--core-profile", choices=PROFILES, default="read_only")
    for api in API_SPECS:
        if api == "core":
            continue
        # Plugins are optional: "off" omits one the target site does not have.
        every.add_argument(
            f"--{api}-profile", choices=[*PROFILES, "off"], default="read_only"
        )
    args = parser.parse_args()
    if args.command == "generate":
        spec = load_spec(args.spec or API_SPECS[args.api])
        output = args.output or (
            Path("generated") if args.api == "core" else Path("generated") / args.api
        )
        asyncio.run(generate(spec, output, api=args.api))
    elif args.command == "serve":
        asyncio.run(serve(args.profile, args.base_url, api=args.api))
    else:
        profiles = {
            api: getattr(args, f"{api.replace('-', '_')}_profile") for api in API_SPECS
        }
        asyncio.run(serve_all(args.base_url, profiles))


if __name__ == "__main__":
    main()
