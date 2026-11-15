# Gerrit OpenAPI → FastMCP

Directly consumes the local Gerrit OpenAPI document. **No Gerrit Python SDK and
no handwritten endpoint wrappers.** Uses the existing
[FastMCP OpenAPI bridge](https://gofastmcp.com/docs/integrations/openapi).

```text
Gerrit extraction → core/openapi.json → pinned profile → FastMCP → MCP tools
```

Runs on FastMCP **4.2** and Python 3.12+.

## Generated artifacts

| Profile | Tools | Selection |
| --- | ---: | --- |
| `full` | 339 | Every operation in the pinned core document |
| `read_only` | 161 | Cataloged GET operations; default when serving |
| `agent` | 162 | Same reads plus one constrained revision review |

Two plugins are also supported, each with its own source hash and operation
catalog: **checks** (`--api checks`: 9 / 4 / 4 tools) and **code-owners**
(`--api code-owners`: 13 / 9 / 9). For both, `read_only` and `agent` expose only
reads; plugin writes require the explicit `full` profile.

The `agent` profile removes every POST/PUT/DELETE except one revision review. Its
review schema permits `message`, `comments`, `drafts`, `is_ai`, and a `labels`
object bounded to `Code-Review` ∈ {−1, 0, +1} — no `on_behalf_of`, `ready`,
reviewers, or other controls.

`gerrit_openapi_mcp/catalog.json` pins explicit method/path/profile decisions and
a source hash. A changed spec (new operation or request field) stops generation
until the catalog is reviewed.

## Run locally

With the sibling `../gerrit-openapi` checkout present:

```bash
uv sync --python 3.12   # one-time
./update.sh             # regenerate core + plugins, then run the tests
```

`update.sh` regenerates every API over a mock transport (no Gerrit requests) and
runs the suite. `--refresh` git-pulls the sibling spec first; a changed spec stops
generation until its catalog is reviewed.

## Serve

```bash
# Core plus every plugin in one server (recommended):
uv run gerrit-openapi-mcp serve-all --base-url https://gerrit.example.com/a/

# A single API:
uv run gerrit-openapi-mcp serve --base-url https://gerrit.example.com/a/ --profile agent
```

`serve-all` mounts core unprefixed and each plugin under its name (`checks_`,
`code_owners_`); drop one the site lacks with `--checks-profile off`. It serves
from the committed `generated/` specs — so `uvx --from git+...` is self-contained,
no sibling checkout needed — and serves the change-screen and changes-list skills
alongside the tools. Credentials come from `GERRIT_HTTP_USER` /
`GERRIT_HTTP_PASSWORD` or `GERRIT_COOKIE` (server config, not tool arguments).

Example host config — the committed `.mcp.json` wires `gerrit` (read-only),
`gerrit-agent` (the bounded review write), and `gerrit-full`:

```json
{
  "mcpServers": {
    "gerrit": {
      "command": "uv",
      "args": [
        "run", "--project", "/path/to/gerrit-openapi-mcp",
        "gerrit-openapi-mcp", "serve-all",
        "--base-url", "https://gerrit.example.com/a/"
      ],
      "env": { "GERRIT_COOKIE": "${GERRIT_COOKIE:-}" }
    }
  }
}
```

## Verified

50 tests pass (Ruff + formatting clean): per-profile tool registration, the
outgoing guard (origin/path/profile checks, the bounded −1/0/+1 review, ±2 and
other writes rejected before any request reaches the wire, no redirect-following,
XSSI handling), the drift gate, the mounted `serve-all` topology, and both served
skills. The suite also pins that stock FastMCP 4.2 no longer clamps the nested
bound ([#6457](https://github.com/PrefectHQ/fastmcp/issues/6457)) — so the guard,
not the framework, enforces it.
