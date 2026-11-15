"""Load the committed per-profile OpenAPI docs so serving needs no sibling spec.

``generate`` writes ``generated/<api>/<profile>-openapi.json`` (core is written
flat at ``generated/<profile>-openapi.json``). Serving reads those instead of
re-deriving from the sibling gerrit-openapi checkout, so a
``uvx --from git+...`` install is self-contained: the specs ship inside the
wheel (see the force-include in pyproject.toml).
"""

import importlib.resources
import json
from pathlib import Path


def generated_root() -> Path:
    """The directory holding generated specs, in a source tree or a wheel."""
    # A source/dev tree keeps generated/ at the repo root; an installed wheel
    # ships it inside the package. Prefer the source copy when present.
    source = Path(__file__).resolve().parents[1] / "generated"
    if source.is_dir():
        return source
    return Path(str(importlib.resources.files(__package__))) / "generated"


def generated_spec_path(api: str, profile: str) -> Path:
    root = generated_root()
    base = root if api == "core" else root / api
    return base / f"{profile}-openapi.json"


def load_generated_spec(api: str, profile: str) -> dict:
    path = generated_spec_path(api, profile)
    if not path.is_file():
        raise FileNotFoundError(
            f"No generated spec for api={api!r} profile={profile!r} at {path}. "
            f"Run `gerrit-openapi-mcp --api {api} generate` first."
        )
    return json.loads(path.read_text())
