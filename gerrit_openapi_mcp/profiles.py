"""Pinned operation decisions and derived OpenAPI artifacts."""

import copy
import hashlib
import json
from pathlib import Path

METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
PROFILES = ("full", "read_only", "agent")
REVIEW = "postChangesChangeIdRevisionsRevisionIdReview"
DEFAULT_SPEC = Path(__file__).resolve().parents[2] / "gerrit-openapi/core/openapi.json"
DEFAULT_CATALOG = Path(__file__).with_name("catalog.json")
API_SPECS = {
    "core": DEFAULT_SPEC,
    "checks": DEFAULT_SPEC.parent.parent / "plugins/checks/openapi.json",
    "code-owners": DEFAULT_SPEC.parent.parent / "plugins/code-owners/openapi.json",
}
API_CATALOGS = {
    "core": DEFAULT_CATALOG,
    "checks": Path(__file__).with_name("checks_catalog.json"),
    "code-owners": Path(__file__).with_name("code_owners_catalog.json"),
}


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def operations(spec: dict):
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in METHODS:
                yield method.upper(), path, operation


def load_spec(path: Path = DEFAULT_SPEC) -> dict:
    return json.loads(path.read_text())


def load_catalog(api: str = "core") -> dict:
    return json.loads(API_CATALOGS[api].read_text())


def derive(spec: dict, profile: str, catalog: dict | None = None) -> dict:
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile: {profile}")
    catalog = (
        catalog if catalog is not None else json.loads(DEFAULT_CATALOG.read_text())
    )
    if digest(spec) != catalog["source_sha256"]:
        raise ValueError("Spec changed: review the operation catalog before generation")
    current = {}
    for method, path, operation in operations(spec):
        key = operation["operationId"]
        if key in current:
            raise ValueError(f"Duplicate operationId: {key}")
        current[key] = (method, path)
    decisions = catalog["operations"]
    if set(current) != set(decisions):
        raise ValueError("Unclassified or stale operation IDs")
    for key, (method, path) in current.items():
        if (decisions[key]["method"], decisions[key]["path"]) != (method, path):
            raise ValueError(f"Changed operation mapping: {key}")

    result = copy.deepcopy(spec)
    result["paths"] = {}
    for path, item in spec["paths"].items():
        kept = {
            key: copy.deepcopy(value)
            for key, value in item.items()
            if key not in METHODS
        }
        for method, op in item.items():
            if (
                method in METHODS
                and profile in decisions[op["operationId"]]["profiles"]
            ):
                kept[method] = copy.deepcopy(op)
        if any(method in kept for method in METHODS):
            result["paths"][path] = kept

    if profile == "agent" and REVIEW in current:
        schemas = result["components"]["schemas"]
        original = schemas["ReviewInput"]["properties"]
        properties = {
            name: copy.deepcopy(original[name])
            for name in ("message", "comments", "drafts", "is_ai")
        }
        properties["labels"] = {
            "type": "object",
            "properties": {
                "Code-Review": {"type": "integer", "minimum": -1, "maximum": 1}
            },
            "additionalProperties": False,
        }
        schemas["AgentReviewInput"] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        for method, path, op in operations(result):
            if op["operationId"] == REVIEW:
                op["requestBody"]["content"] = {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/AgentReviewInput"}
                    }
                }

    result["info"]["title"] += f" ({profile} MCP prototype)"
    result["x-agent-profile"] = {
        "name": profile,
        "version": 1,
        "source_sha256": catalog["source_sha256"],
    }
    return result
