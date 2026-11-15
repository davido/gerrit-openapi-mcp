"""FastMCP's existing bridge, plus a guard on the actual outgoing request."""

import json
import re
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, unquote

import httpx2 as httpx  # the httpx fork FastMCP 4.2's OpenAPI client requires
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from jsonschema import Draft202012Validator

from .profiles import REVIEW, operations


class GuardedClient(httpx.AsyncClient):
    def __init__(self, spec: dict, *, base_url: str, **kwargs):
        super().__init__(
            base_url=base_url,
            follow_redirects=False,
            trust_env=False,
            timeout=30,
            **kwargs,
        )
        if (
            self.base_url.scheme not in ("http", "https")
            or self.base_url.userinfo
            or self.base_url.query
            or self.base_url.fragment
        ):
            raise ValueError(
                "Configure a fixed HTTP(S) base URL without userinfo/query"
            )
        self.profile = spec["x-agent-profile"]["name"]
        self.routes = []
        for method, path, op in operations(spec):
            pattern = re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path))
            self.routes.append((method, re.compile(pattern), op))
        self.review_validator = None
        if self.profile == "agent" and any(
            op["operationId"] == REVIEW for _, _, op in self.routes
        ):
            self.review_validator = Draft202012Validator(
                {
                    "$ref": "#/components/schemas/AgentReviewInput",
                    "components": spec["components"],
                }
            )

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        # Guard after FastMCP builds/serializes the request, before any I/O.
        target = request.url
        base = self.base_url
        if (target.scheme, target.host, target.port) != (
            base.scheme,
            base.host,
            base.port,
        ):
            raise PermissionError("Request left the configured Gerrit origin")
        if target.userinfo or target.fragment:
            raise PermissionError("Userinfo and URL fragments are not permitted")
        raw_path = target.raw_path.split(b"?", 1)[0].decode("ascii")
        decoded = unquote(raw_path)
        if (
            any(part in (".", "..") for part in decoded.split("/"))
            or any(char in decoded for char in ("\\", "\r", "\n", "\x00", "%"))
            or "//" in decoded
        ):
            raise PermissionError("Noncanonical request path")
        prefix = base.raw_path.split(b"?", 1)[0].decode("ascii").rstrip("/")
        if not raw_path.startswith(prefix + "/"):
            raise PermissionError("Request left the configured Gerrit base path")
        path = raw_path[len(prefix) :]
        if any(
            k.lower() in ("$m", "$ct", "access_token")
            for k, _ in parse_qsl(target.query.decode())
        ):
            raise PermissionError("Method/authentication query overrides are forbidden")
        operation = next(
            (
                op
                for method, pattern, op in self.routes
                if method == request.method and pattern.fullmatch(path)
            ),
            None,
        )
        if operation is None:
            raise PermissionError("Operation is outside the selected profile")
        body = await request.aread()
        if request.method in ("GET", "HEAD") and body:
            raise PermissionError("Read operations cannot carry a request body")
        if self.profile == "agent" and request.method not in ("GET", "HEAD"):
            if operation["operationId"] != REVIEW or target.query:
                raise PermissionError("Only the bounded review write is permitted")
            if (
                request.headers.get("content-type", "").split(";", 1)[0]
                != "application/json"
            ):
                raise PermissionError("Review requires JSON")
            payload = json.loads(body or b"{}")
            self.review_validator.validate(payload)
            # Preserve the existing broker's strict-int behavior beyond JSON Schema.
            if any(
                type(value) is not int for value in payload.get("labels", {}).values()
            ):
                raise PermissionError("Votes must be integers, without coercion")
        # FastMCP supplies its own Request. HTTPX's cookie jar is not merged by send.
        # Only server-configured cookies may reach this bridge's upstream.
        if "Cookie" in self.headers:
            request.headers["Cookie"] = self.headers["Cookie"]
        kwargs["follow_redirects"] = False
        response = await super().send(request, **kwargs)
        if not kwargs.get("stream", False):
            await response.aread()
            if "json" in response.headers.get(
                "content-type", ""
            ) and response.content.startswith(b")]}'"):
                content = response.content[4:].lstrip(b"\r\n")
                headers = dict(response.headers)
                headers.pop("content-encoding", None)
                headers.pop("content-length", None)
                response = httpx.Response(
                    response.status_code,
                    headers=headers,
                    content=content,
                    request=request,
                )
        return response


@asynccontextmanager
async def bridge(spec: dict, base_url: str, *, transport=None, auth=None, cookie=None):
    headers = {"Cookie": cookie} if cookie else {}
    async with GuardedClient(
        spec, base_url=base_url, transport=transport, auth=auth, headers=headers
    ) as client:
        allowed = {(method, path) for method, path, _ in operations(spec)}
        # FastMCP already implements operation filtering. Catch-all EXCLUDE is
        # explicit, rather than relying on its default conversion of every route.
        maps = [
            RouteMap(
                methods=[method],
                pattern="^" + re.escape(path) + "$",
                mcp_type=MCPType.TOOL,
            )
            for method, path in sorted(allowed)
        ]
        maps.append(RouteMap(mcp_type=MCPType.EXCLUDE))
        server = FastMCP.from_openapi(
            openapi_spec=spec,
            client=client,
            name="Gerrit OpenAPI bridge",
            route_maps=maps,
            strict_input_validation=True,
            # Response compatibility is not established for every endpoint.
            # Keep JSON structured, but avoid promising full output validation.
            validate_output=False,
        )
        yield server
