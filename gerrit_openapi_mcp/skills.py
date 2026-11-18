"""Serve agent skills from the MCP server (FastMCP 4.2), skill-agnostic.

This server is a configurable skills *host*: it serves whatever skills repository is
pointed at, not a bundled set. The skill content lives in the standalone
`gerrit-agent-skills` repo. Each skill is served under ``skill://<name>/`` and advertised
through the Skills extension (``skills/list``); a plain MCP client can still read the files
as resources, and a discovery client materializes one with
``client.read_skill(uri).to_directory(...)``.

Resolution of the skills root:
- ``GERRIT_AGENT_SKILLS_ROOT`` if set to a non-empty value -- an explicit configuration; a
  non-directory path is a hard error, not a silent fallback. An empty or whitespace-only
  value is treated as unset (the shell idiom for "not configured").
- else the ``.claude/skills`` dev symlink to the sibling checkout, if present.
- else none -- the server runs tools-only (no skills), never failing on their absence.

Capability enforcement: a skill declares the operations it needs
in ``requires.json``; a skill whose *required* operations are not all available as tools in
the running server is hidden (with a logged reason). This is discovery hygiene, NOT an
authorization boundary -- the per-origin/profile guards in the bridge still enforce access.
"""

import json
import logging
import os
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.skills import Skill
from fastmcp.skills.provider import SkillCatalogProvider

logger = logging.getLogger(__name__)

# Meta key under which the bridge stamps each tool's canonical operationId. Mounting
# (serve-all) wraps tools and drops their ``_route``, but copies meta onto the wrapper, so
# this is how the true operationId -> tool-name binding survives a mounted server.
OPERATION_ID_META = "gerritOperationId"


def skills_dir() -> Path | None:
    """The skills root to serve, or ``None`` for tools-only operation.

    ``GERRIT_AGENT_SKILLS_ROOT`` wins (and must be a directory); otherwise the
    ``.claude/skills`` dev symlink to the `gerrit-agent-skills` checkout is used when
    present. A symlinked *root* loads fine -- FastMCP only rejects a skill *folder* that is
    itself a symlink.
    """
    # An empty or whitespace-only value is the shell idiom for "not set": treat
    # it as unset rather than as an invalid path. A non-empty value is an
    # explicit configuration and must resolve to a directory or we fail fast.
    configured = (os.environ.get("GERRIT_AGENT_SKILLS_ROOT") or "").strip()
    if configured:
        root = Path(configured)
        if not root.is_dir():
            raise FileNotFoundError(
                f"GERRIT_AGENT_SKILLS_ROOT={configured!r} is not a directory"
            )
        return root
    source = _dev_skills_symlink()
    return source if source.is_dir() else None


def _dev_skills_symlink() -> Path:
    """The local-dev ``.claude/skills`` symlink path (may be absent or dangling).
    A seam so tests can exercise missing/dangling/valid dev roots."""
    return Path(__file__).resolve().parents[1] / ".claude" / "skills"


def _required_ops(skill_dir: Path) -> set[str]:
    """Core operations the skill cannot run without, from its requires.json (empty if none)."""
    manifest = skill_dir / "requires.json"
    if not manifest.is_file():
        return set()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return {entry["operationId"] for entry in data.get("required", [])}


def tool_bindings(tools) -> dict[str, str]:
    """Map operationId -> the name under which this server registered its tool.

    Built from the real provider's tools (``await server.list_tools(run_middleware=False)``),
    so it reflects FastMCP's actual naming -- slugify, the 56-char cap, mount namespacing, AND
    generated collision suffixes. That last one is why a reconstructed/approximated name set is
    unsafe: a distinct operation can take another's base name while the one a skill wants is
    suffixed. The operationId comes from the tool's meta (stamped by the bridge, and preserved
    when mounting wraps the tool and drops its ``_route``), falling back to ``_route`` for an
    unmounted raw provider and then to the name itself for a non-OpenAPI tool.
    """
    binding = {}
    for tool in tools:
        meta = tool.meta or {}
        operation_id = meta.get(OPERATION_ID_META)
        if operation_id is None:
            route = getattr(tool, "_route", None)
            operation_id = (
                getattr(route, "operation_id", None) if route is not None else None
            )
        binding[operation_id or tool.name] = tool.name
    return binding


def add_skills(server: FastMCP, bindings: dict[str, str] | None = None) -> list[str]:
    """Serve the discoverable skills under the skills root; return the served names.

    Discovery is a recursive glob (``**/SKILL.md``), so grouped or flat layouts both work.
    When ``bindings`` is given (operationId -> registered tool name, from ``tool_bindings``),
    a skill is advertised only if EVERY ``requires.json`` ``required`` operation binds to a
    tool of the SAME name -- i.e. the operation the skill calls by its operationId is actually
    served under that exact name. That rejects an operation whose tool FastMCP renamed
    (slug/cap) or suffixed on collision (the tool with the bare name is a different operation),
    which a name-membership test would wrongly accept. Pass ``None`` to skip filtering. Skills
    go to ``SkillCatalogProvider`` as a **list** (it rejects duplicate names). A missing/empty
    root is tolerated.
    """
    root = skills_dir()
    if root is None or not root.is_dir():
        return []
    skills = []
    for md in sorted(root.glob("**/SKILL.md")):
        folder = md.parent
        if bindings is not None:
            unsatisfied = sorted(
                op for op in _required_ops(folder) if bindings.get(op) != op
            )
            if unsatisfied:
                logger.info(
                    "skill %r not advertised: required operations not served under their own "
                    "name in this profile: %s",
                    folder.name,
                    unsatisfied,
                )
                continue
        skills.append(Skill.from_directory(folder))
    if skills:
        server.add_provider(SkillCatalogProvider(skills))
    return [skill.name for skill in skills]
