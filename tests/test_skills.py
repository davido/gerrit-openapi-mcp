import asyncio
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from fastmcp.skills import SkillsClientExtension

from gerrit_openapi_mcp import skills as skills_mod
from gerrit_openapi_mcp.bridge import _stamp_operation_id, bridge
from gerrit_openapi_mcp.generated import load_generated_spec
from gerrit_openapi_mcp.skills import add_skills, skills_dir, tool_bindings

CHANGE_SCREEN = "gerrit-change-screen"
CHANGES_LIST = "gerrit-changes-list"

# The dev symlink to the gerrit-agent-skills sibling; real-skill coverage is a separate,
# skipped-when-absent integration test, so the unit suite never needs the sibling.
_DEV_SKILLS = Path(__file__).resolve().parents[1] / ".claude" / "skills"


def _write_skill(root: Path, name: str, *, required=(), extra_files=None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill {name} for the suite.\n---\n# {name}\n",
        encoding="utf-8",
    )
    (folder / "requires.json").write_text(
        json.dumps({"required": [{"operationId": op} for op in required]}),
        encoding="utf-8",
    )
    for filename, content in (extra_files or {}).items():
        (folder / filename).write_text(content, encoding="utf-8")
    return folder


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    """A self-contained skills root selected via config env -- no sibling repo needed."""
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setenv("GERRIT_AGENT_SKILLS_ROOT", str(root))
    return root


@pytest.fixture
def server(skills_root):
    _write_skill(
        skills_root,
        "alpha",
        required=["getChanges"],
        extra_files={"template.html": "<p>a</p>"},
    )
    _write_skill(skills_root, "beta", required=["getChanges"])
    mcp = FastMCP("test")
    assert {"alpha", "beta"} <= set(
        add_skills(mcp)
    )  # no capability filter -> both served
    return mcp


def _provider(spec):
    """A real FastMCP provider over `spec` (every route a tool), stamped exactly as the bridge
    does -- so FastMCP's actual naming, collision suffixes, AND the operationId meta are real."""
    client = httpx.AsyncClient(
        base_url="https://gerrit.invalid",
        transport=httpx.MockTransport(lambda req: httpx.Response(500)),
    )
    return FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        route_maps=[RouteMap(mcp_type=MCPType.TOOL)],
        mcp_component_fn=_stamp_operation_id,
    )


def _bindings_from_openapi(spec):
    """operationId -> tool name from an UNMOUNTED provider over `spec`."""

    async def build():
        server = _provider(spec)
        return tool_bindings(await server.list_tools(run_middleware=False))

    return asyncio.run(build())


def _mounted_bindings(spec):
    """operationId -> tool name after MOUNTING the provider (as serve-all does): the mount wraps
    tools and drops ``_route``, so a correct binding must come from the preserved meta."""

    async def build():
        root = FastMCP("root")
        root.mount(_provider(spec), namespace=None)  # core mounts unprefixed
        return tool_bindings(await root.list_tools(run_middleware=False))

    return asyncio.run(build())


@pytest.fixture
def core_bindings():
    """The real operationId -> tool-name bindings of the core read_only bridge."""

    async def build():
        spec = load_generated_spec("core", "read_only")
        transport = httpx.MockTransport(lambda req: httpx.Response(500))
        async with bridge(spec, "https://gerrit.invalid", transport=transport) as srv:
            return tool_bindings(await srv.list_tools(run_middleware=False))

    return asyncio.run(build())


# A valid-spec control: three operationIds that collide under FastMCP's naming, plus a
# clean one. FastMCP assigns getChanges / getChanges_2 / getChanges_2_2 by discovery order, so
# the operation literally named getChanges_2 is served as getChanges_2_2 -- a skill requiring
# getChanges_2 must be hidden, since the tool named getChanges_2 is a DIFFERENT operation.
_COLLISION_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "t", "version": "1"},
    "paths": {
        "/first": {
            "get": {
                "operationId": "getChanges__first",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/second": {
            "get": {
                "operationId": "getChanges__second",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/wanted": {
            "get": {
                "operationId": "getChanges_2",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/clean": {
            "get": {
                "operationId": "getFoo",
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


async def test_skills_discoverable_via_extension(server):
    async with Client(server, extensions=[SkillsClientExtension()]) as client:
        entries = await client.list_skills()
        names = {entry.frontmatter.name for entry in entries}
        assert {"alpha", "beta"} <= names
        uri = next(entry.uri for entry in entries if entry.frontmatter.name == "alpha")
        skill = await client.read_skill(uri)
    assert skill.name == "alpha"
    assert {"SKILL.md", "template.html", "requires.json"} <= set(skill.files)


async def test_skill_files_readable_without_the_extension(server):
    async with Client(server) as client:
        skill = await client.read_skill("skill://alpha/SKILL.md")
    assert skill.name == "alpha"
    assert "requires.json" in skill.files


def test_configured_empty_root_serves_no_skills(skills_root):
    # Configured but empty: tools-only, never an error.
    assert add_skills(FastMCP("test")) == []


def test_invalid_configured_root_is_an_error(tmp_path, monkeypatch):
    not_a_dir = tmp_path / "skills.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GERRIT_AGENT_SKILLS_ROOT", str(not_a_dir))
    with pytest.raises(FileNotFoundError):
        add_skills(FastMCP("test"))


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_or_blank_env_is_treated_as_unset(tmp_path, monkeypatch, value):
    # An empty/whitespace GERRIT_AGENT_SKILLS_ROOT must not raise and must fall
    # through to dev-symlink resolution -- i.e. behave exactly like unset.
    dev = tmp_path / "dev-skills"
    dev.mkdir()
    monkeypatch.setattr(skills_mod, "_dev_skills_symlink", lambda: dev)
    monkeypatch.setenv("GERRIT_AGENT_SKILLS_ROOT", value)
    assert skills_dir() == dev


def test_missing_dev_root_is_tools_only(tmp_path, monkeypatch):
    monkeypatch.delenv("GERRIT_AGENT_SKILLS_ROOT", raising=False)
    monkeypatch.setattr(skills_mod, "_dev_skills_symlink", lambda: tmp_path / "nope")
    assert skills_dir() is None


def test_dangling_dev_symlink_is_tools_only(tmp_path, monkeypatch):
    link = tmp_path / "skills"
    link.symlink_to(tmp_path / "missing-target")  # dangling, like a clone-only checkout
    monkeypatch.delenv("GERRIT_AGENT_SKILLS_ROOT", raising=False)
    monkeypatch.setattr(skills_mod, "_dev_skills_symlink", lambda: link)
    assert skills_dir() is None


def test_valid_dev_root_is_used(tmp_path, monkeypatch):
    dev = tmp_path / "dev-skills"
    dev.mkdir()
    monkeypatch.delenv("GERRIT_AGENT_SKILLS_ROOT", raising=False)
    monkeypatch.setattr(skills_mod, "_dev_skills_symlink", lambda: dev)
    assert skills_dir() == dev


def test_capability_filter_uses_actual_registered_tools(skills_root, core_bindings):
    # A real, short core read op whose tool is named identically.
    present = "getChanges"
    assert core_bindings.get(present) == present
    # An operationId too long to survive FastMCP's 56-char cap: not served under its own name.
    absent = "getChanges" + "X" * 60
    assert core_bindings.get(absent) != absent

    _write_skill(skills_root, "ok", required=[present])
    _write_skill(skills_root, "nope", required=[absent])
    served = set(add_skills(FastMCP("test"), core_bindings))
    assert "ok" in served
    assert "nope" not in served


def test_capability_filter_hides_collision_victim(skills_root):
    # Built from the real provider, so the collision suffixing is FastMCP's own, not guessed.
    binding = _bindings_from_openapi(_COLLISION_SPEC)
    assert binding["getFoo"] == "getFoo"  # clean op: served under its own name
    assert binding["getChanges_2"] == "getChanges_2_2"  # victim: its tool was renamed
    assert binding["getChanges__first"] == "getChanges"  # took the base name

    _write_skill(skills_root, "clean", required=["getFoo"])
    _write_skill(skills_root, "victim", required=["getChanges_2"])
    served = set(add_skills(FastMCP("test"), binding))
    assert "clean" in served
    # Hidden: the tool named getChanges_2 is getChanges__second, a DIFFERENT operation.
    assert "victim" not in served


def test_capability_filter_survives_mounting(skills_root):
    # serve-all mounts the bridge; the mount wrapper drops _route, so the binding must come
    # from the stamped meta. Without that, tool_bindings would fall back to name->name and
    # wrongly advertise the victim. The unmounted test alone would miss this.
    binding = _mounted_bindings(_COLLISION_SPEC)
    assert binding["getFoo"] == "getFoo"
    assert (
        binding["getChanges_2"] == "getChanges_2_2"
    )  # recovered from meta, post-mount

    _write_skill(skills_root, "clean", required=["getFoo"])
    _write_skill(skills_root, "victim", required=["getChanges_2"])
    served = set(add_skills(FastMCP("test"), binding))
    assert "clean" in served
    assert "victim" not in served


@pytest.mark.skipif(
    not (_DEV_SKILLS / CHANGE_SCREEN / "SKILL.md").is_file(),
    reason="gerrit-agent-skills sibling not checked out at ../gerrit-agent-skills",
)
def test_integration_real_sibling_skills_load(monkeypatch):
    monkeypatch.setenv("GERRIT_AGENT_SKILLS_ROOT", str(_DEV_SKILLS.resolve()))
    served = set(add_skills(FastMCP("test")))
    assert {CHANGE_SCREEN, CHANGES_LIST} <= served
