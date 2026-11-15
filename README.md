# Gerrit OpenAPI → FastMCP

Directly consumes the local Gerrit OpenAPI document. **No Gerrit Python SDK and
no handwritten endpoint wrappers.** Uses the existing
[FastMCP OpenAPI bridge](https://gofastmcp.com/docs/v3/integrations/openapi).

```text
Gerrit extraction → core/openapi.json → pinned profile → FastMCP → MCP tools
```

The existing Gerrit MCP server and SDK checkouts are unchanged. This bridge
is a separate local project, running on FastMCP **4.2** and Python 3.12+.

## Generated artifacts

| Profile | Tools | Selection |
| --- | ---: | --- |
| `full` | 339 | Every operation in the pinned core document |
| `read_only` | 161 | Cataloged GET operations; default when serving |
| `agent` | 162 | Same reads plus constrained revision review |

The checks plugin is also supported with `--api checks`, using
`../gerrit-openapi/plugins/checks/openapi.json`. Its generated catalogs are in
`generated/checks/`: **9 full tools, 4 read-only tools, and 4 agent tools**.
Checks writes, including reruns and status/checker updates, require the explicit
`full` profile. Each API has its own source hash and operation catalog.

See [Checks and change-screen data](docs/checks-change-screen.md) for commands,
MCP host configuration, tool names, and the client-side read-only example.
All served tools remain generated operations; the example is not a registered
composite server tool.

The agent profile removes **every POST/PUT/DELETE except revision review**.
That excludes submit, abandon, merge, rebase, delete, password changes, and other
writes. The review schema permits `message`, `comments`, `labels`, `drafts`, and
the boolean attribution field `is_ai`. Its labels object permits only
`Code-Review` values −1, 0, and +1. It does not permit `on_behalf_of`, `ready`,
`work_in_progress`, reviewers, or other review controls.

`gerrit_openapi_mcp/catalog.json` records explicit method/path/profile decisions
for all 339 source operations and a canonical source hash. A changed spec,
including a new operation or request field, stops generation. Review and update
the catalog deliberately; normal generation does not learn new operations.
The initial read selection includes the current GET inventory; it is **not a
completed sensitivity audit of all read endpoints**.

## Run locally

From this directory, with the sibling `../gerrit-openapi` checkout present:

```bash
uv sync --python 3.12
uv run gerrit-openapi-mcp generate
uv run gerrit-openapi-mcp --api checks generate
uv run pytest -q
uv run ruff check .
```

Generation uses a mock HTTP transport and makes no Gerrit requests. It writes:

- `generated/full-openapi.json` and `full-tools.json`
- `generated/read_only-openapi.json` and `read_only-tools.json`
- `generated/agent-openapi.json` and `agent-tools.json`
- `generated/manifest.json`, recording counts, source hash, and FastMCP version

Another source path may be selected with the global `--spec` argument. It must
match the selected API's catalog; changing the input is not an automatic policy
update. The default API is `core`.

To serve a stdio MCP endpoint against a site you choose:

```bash
uv run gerrit-openapi-mcp serve \
  --base-url https://gerrit.example.com/a/ --profile agent
```

The bridge was tested against mock transports, not that example URL or any
real Gerrit. Serving does not make a request until an MCP tool is invoked.
Use `/a/` for authenticated Gerrit REST paths; include any site context path.
`--profile full` deliberately enables the broader operation catalog.

Optional credentials are read from `GERRIT_HTTP_USER` plus
`GERRIT_HTTP_PASSWORD`, or `GERRIT_COOKIE`. They are server configuration, not
tool arguments. No existing Gerrit config, gitcookies file, or MCP configuration
is read. Gitcookies parsing, external authentication helpers, and per-caller
authentication are not implemented.

Example MCP host configuration:

```json
{
  "mcpServers": {
    "gerrit-openapi-bridge": {
      "command": "/usr/local/bin/uv",
      "args": [
        "--directory", "/Users/Davido/projects/gerrit-openapi-mcp",
        "run", "--frozen", "gerrit-openapi-mcp", "serve",
        "--base-url", "https://gerrit.example.com/a/",
        "--profile", "agent"
      ]
    }
  }
}
```

## What is verified

Verification result: **44 tests passed**; Ruff checks and formatting passed.

- All 339 full tools register through the actual FastMCP bridge; a subprocess
  stdio MCP client initializes and lists all 339.
- Read-only and agent sets contain 161 and 162 unique tools, respectively.
- Reads serialize query arrays and return parsed JSON after stripping Gerrit's
  XSSI prefix.
- −1/0/+1 reviews, including `is_ai`, reach a mock transport.
- ±2, other labels, string/bool/float votes, forbidden review controls, and
  destructive operations are rejected before the mock transport sees them.
- Direct attempts to use omitted operations through the guarded HTTP client
  are rejected independently of tool discovery.
- Foreign origins, selected URL overrides, and noncanonical paths are rejected;
  redirects are not followed, including an explicit per-call follow request.
- A changed upstream operation or request schema stops profile generation.
- Checks reads preserve site context and `/a/` paths, parse XSSI, and return
  checker/submit-impact fields. Read-only and agent profiles block all five
  checks writes; an explicit full-profile rerun forwards to a mock.
- The client-side change-screen example composes generated core and checks
  tools, including an empty checks list. Checks CLI generation is subprocess-tested.

The tests also exercise **stock FastMCP 4.2 without the custom HTTP guard**.
With `strict_input_validation=True`, 4.2 rejects only *undeclared top-level*
tool arguments; it no longer clamps nested JSON Schema value bounds, so it
forwards +2 and an unlisted `Verified` vote to the mock transport
([#6457](https://github.com/PrefectHQ/fastmcp/issues/6457)). The bridge does
not rely on the framework for this: its outgoing guard validates the final
agent-review body and enforces the −1/0/+1 bound independently, before any
request is sent.

## Existing upstream work and limits

The OpenAPI bridge, route exclusions, and tool naming already exist upstream.
This project supplies Gerrit profiles and a small outgoing-request guard, rather
than another OpenAPI parser or MCP implementation.

[#6457](https://github.com/PrefectHQ/fastmcp/issues/6457) reports that FastMCP
4.x strict validation does not enforce nested JSON Schema value bounds. The
bridge's tests confirm this on 4.2: stock `from_openapi` forwards +2. The
−1/0/+1 bound is therefore enforced only by the bridge's outgoing guard, which
validates the final agent-review body and preserves strict integer handling.
`test_stock_fastmcp_does_not_enforce_nested_bound` pins the framework behavior
so a future bump that restores the pre-dispatch clamp is noticed.

FastMCP 4.2 is above both the
[path-traversal](https://github.com/PrefectHQ/fastmcp/security/advisories/GHSA-99xm-8q53-5c8p)
(fixed 3.4.9 / 4.1.0) and
[undeclared-parameter-forwarding](https://github.com/PrefectHQ/fastmcp/security/advisories/GHSA-qvjc-pw7p-hvg5)
(fixed 3.4.8 / 4.0.11) advisory lines. This is not an audit of all upstream
advisories or a production certification.

Output validation is intentionally disabled with `validate_output=False`;
structured JSON remains available. Mixed success responses, binary downloads,
all request media types, and every existing workflow are not validated here.
The 339 count proves coverage of the current **documented core inventory**, not
that all server/plugin endpoints or every field are accurately extracted.
For example, the current source `CommentInput` schema is sparse; this bridge
does not reconstruct missing fields. Plugins require their own specs; checks is
included here, while other plugin APIs are not currently configured.

Profiles do not change Gerrit ACLs. The runtime checks constrain this bridge;
they require trusted configuration and isolated upstream credentials. Separate
raw HTTP, SSH, or Git access remains subject to the credential's Gerrit rights.
This is an explicit bridge, not a replacement for the existing MCP service.
