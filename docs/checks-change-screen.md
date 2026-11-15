# Checks plugin → MCP, and data for a change screen

This bridge reads the generated document at
`~/projects/gerrit-openapi/plugins/checks/openapi.json`. It uses the existing
`FastMCP.from_openapi()` integration, without a checks SDK. The corresponding
plugin implementation was inspected in `~/projects/checks`.

## Generate and serve

From `~/projects/gerrit-openapi-mcp-prototype`:

```bash
uv run gerrit-openapi-mcp --api checks generate
uv run gerrit-openapi-mcp --api checks serve \
  --base-url https://gerrit.example.com/a/ --profile read_only
```

Generation uses a mock transport and makes no Gerrit requests. Serving makes a
request only when a tool is invoked. Replace the example URL with your site.
Use the **Gerrit base URL**, including any context path and `/a/` for
authentication. Do not append `/plugins/checks`: the generated routes already
contain that prefix where needed.

Credentials use `GERRIT_HTTP_USER` plus `GERRIT_HTTP_PASSWORD`, or `GERRIT_COOKIE`,
as with the core bridge. Gerrit enforces the account's change visibility and
plugin permissions. The checks plugin must be installed; this bridge does not
install or probe it.

| Profile | Tools | Operations |
| --- | ---: | --- |
| `full` | 9 | All documented checks reads/writes |
| `read_only` | 4 | Reads only; default |
| `agent` | 4 | Reads only; no additional checks writes |

The read profiles omit check creation/update, reruns, and checker creation/update.
The outgoing guard also rejects those writes if attempted directly through the
guarded client. `full` deliberately exposes those operations, subject to Gerrit
permissions.

Artifacts are in `generated/checks/`, including OpenAPI documents, MCP tool
catalogs, and `manifest.json`. `checks_catalog.json` pins this API separately from
the core catalog. A changed source document stops generation until reviewed.

## Connect your MCP client

Use this as a second MCP server alongside the existing core server. A ready
template is in `examples/checks.mcp.json`:

```json
{
  "mcpServers": {
    "gerrit-checks-read-only": {
      "command": "/usr/local/bin/uv",
      "args": [
        "--directory", "/Users/Davido/projects/gerrit-openapi-mcp-prototype",
        "run", "--frozen", "gerrit-openapi-mcp",
        "--api", "checks", "serve",
        "--base-url", "https://gerrit.example.com/a/",
        "--profile", "read_only"
      ]
    }
  }
}
```

Supply authentication through your host's environment configuration. No existing
MCP configuration or credentials have been modified by this implementation.

## Read tools

| Tool | Arguments and use |
| --- | --- |
| `getChangesChangeIdRevisionsRevisionIdChecks` | `change-id`, `revision-id`: list runs for a patch set |
| `getChangesChangeIdRevisionsRevisionIdChecksCheckId` | Same IDs plus `check-id`; `o: ["CHECKER"]` adds checker metadata |
| `getPluginsChecksCheckers` | List checker definitions; requires Administrate Checkers in the inspected plugin |
| `getPluginsChecksCheckersCheckerId` | `checker-id`: get a checker definition, subject to server visibility |

For the screen, use the first two tools. You do not need the global,
administrator-only checker list to obtain metadata associated with a run.

List tool arguments:

```json
{"change-id": "637823", "revision-id": "17"}
```

Individual-check tool arguments, using a UUID returned by the list:

```json
{
  "change-id": "637823",
  "revision-id": "17",
  "check-id": "ci:unit-tests",
  "o": ["CHECKER"]
}
```

The UUID above is illustrative; use the actual `checker_uuid` from your response.
No request was made to this change during development. A revision number or SHA
pins the screen to a patch set, avoiding a moving `current` revision during fetch.

## Connect the already-built screen

The plugin's local `web/fetcher.ts` reads
`/changes/{change}/revisions/{patchset}/checks?o=CHECKER` and converts each
`CheckInfo` into a Checks UI run. The bridge provides that plugin's data; it does
not create or register a frontend by itself. A screenshot alone cannot identify
which provider a deployment uses, since other plugins can also supply the tab.

For each row, use these response fields:

- `checker_name`, falling back to `checker_uuid`: display name.
- `state`: `NOT_STARTED`, `SCHEDULED`, `RUNNING`, `SUCCESSFUL`, `FAILED`, or
  `NOT_RELEVANT`, matching the actual plugin enum.
- `message`, `url`: result summary and CI details link.
- `started`, `finished`, `created`, `updated`: timing information when present.
- `checker_status`, `checker_description`: metadata when requested.
- `submit_impact.required`: show a required marker when true.

The full profile also provides the rerun tool:
`postChangesChangeIdRevisionsRevisionIdChecksCheckIdRerun`. Do not display an
enabled rerun button for a read-only or agent connection; those profiles omit
the operation. Gerrit may independently deny it even on a full connection.

Gerrit's overall submit eligibility also depends on core labels and submit
requirements; do not derive it solely from these rows.

## Optional client-side data composition

The included example calls generated MCP tools for core change detail and checks:

```bash
uv run python examples/change_screen_data.py \
  --base-url https://gerrit.example.com/a/ --change-id 123
```

It resolves the current revision, lists runs, fetches each run's checker metadata,
and prints `{"change": ..., "revision": ..., "checks": [...]}`. Use
`--revision 17` to choose an explicit revision. Both clients are read-only.

`screen.py` and this example are **client-side composition**, not served MCP
tools. The running server exposes only `from_openapi`-generated operations. No
`getChangeScreen` facade was added, and the existing screen was not modified.

## Current source limitations

The plugin's `ListChecks` class declares `-o` and `-O`, but the generated list
operation omits those query parameters and marks its response schema unresolved.
The bridge preserves the supplied document and parses the JSON list; it does
not silently patch the generated schema.

The individual-check operation declares `o` correctly. Until extraction is fixed,
use one list call plus one detail call per run with `o=["CHECKER"]`. That supplies
the names, status, description, and submit impact the screen needs, without
requiring administrator-only checker enumeration.

All request/response checks used mocks, not a live checks-enabled Gerrit. Output
schema validation remains disabled as in the core prototype. Missing plugins or
insufficient permissions surface as errors and must not be shown as an empty,
successful checks list.

The local checks repository labels the plugin deprecated. This bridge targets
its REST API. The Gerrit Checks UI provider API is a separate frontend contract,
and other providers may obtain data from other systems.
