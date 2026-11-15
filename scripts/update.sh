#!/usr/bin/env bash
#
# Regenerate the MCP bridge catalogs from the gerrit-openapi specs.
#
# This repo is a CONSUMER of gerrit-openapi (a distribution point), mirroring its
# own update.sh. The per-API operation catalog (gerrit_openapi_mcp/<api>_catalog.json)
# is the human-reviewed gate between a changed Gerrit API and what the agent
# profiles expose: a changed upstream spec STOPS generation until the catalog is
# reviewed, so a new endpoint never silently reaches an agent.
#
# First refresh the specs upstream (../gerrit-openapi/update.sh), then run this.
#
# Usage:
#   ./scripts/update.sh                 # regenerate every configured API
#   ./scripts/update.sh checks          # just one API
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -gt 0 ]; then
  APIS=("$@")
else
  # APIs are discovered from the module, so this covers core / checks / code-owners
  # automatically as they are wired into profiles.API_SPECS.
  APIS=($(uv run python -c "from gerrit_openapi_mcp.profiles import API_SPECS; print(' '.join(API_SPECS))"))
fi

status=0
for api in "${APIS[@]}"; do
  printf '== %s ==\n' "$api"
  err=$(mktemp)
  if uv run gerrit-openapi-mcp --api "$api" generate >/dev/null 2>"$err"; then
    out="generated"; [ "$api" = "core" ] || out="generated/$api"
    echo "   regenerated -> $out/"
  elif grep -q "Spec changed" "$err"; then
    echo "   DRIFT: the '$api' spec changed; its catalog must be reviewed before regenerating." >&2
    uv run python - "$api" <<'PY' >&2 || true
import json, sys
from gerrit_openapi_mcp.profiles import API_SPECS, API_CATALOGS, load_spec, digest
api = sys.argv[1]
spec = load_spec(API_SPECS[api])
cat = json.loads(API_CATALOGS[api].read_text())
methods = ("get", "post", "put", "delete", "patch")
spec_ops = {m.get("operationId")
            for p in spec.get("paths", {}).values()
            for mth, m in p.items() if mth in methods}
cat_ops = set(cat.get("operations", {}))
new, gone = sorted(spec_ops - cat_ops), sorted(cat_ops - spec_ops)
print(f"     new digest : {digest(spec)}")
if new:
    print(f"     + {len(new)} new op(s) to classify: " + ", ".join(new[:12]) + (" ..." if len(new) > 12 else ""))
if gone:
    print(f"     - {len(gone)} removed: " + ", ".join(gone[:12]) + (" ..." if len(gone) > 12 else ""))
print(f"     -> edit gerrit_openapi_mcp/{API_CATALOGS[api].name}: classify new ops,")
print(f"        set source_sha256 to the digest above, then re-run.")
PY
    status=1
  else
    cat "$err" >&2
    status=1
  fi
  rm -f "$err"
done

echo "== pytest =="
uv run pytest -q
exit "$status"
