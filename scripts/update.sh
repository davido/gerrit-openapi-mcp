#!/usr/bin/env bash
#
# Regenerate the MCP bridge catalogs from the gerrit-openapi specs.
#
# Publish -> consume flow:
#   1. A Gerrit endpoint changes. The gerrit-openapi repo's build-time extractor
#      regenerates core/openapi.json and plugins/*/openapi.json and PUBLISHES them
#      (committed/tagged in ../gerrit-openapi). This repo is a CONSUMER.
#   2. Refresh the sibling checkout (git pull) -- or pass --refresh to do it here.
#   3. Regenerate. A changed upstream spec STOPS generation (the drift gate) until
#      its per-API catalog (gerrit_openapi_mcp/<api>_catalog.json) is reviewed, so a
#      new endpoint never silently reaches an agent profile. Classify the new ops,
#      set source_sha256 to the printed digest, and re-run.
#   4. Commit the refreshed generated/ (and bump the package version to mirror the
#      Gerrit source_version shown in the manifest when cutting a release).
#
# Usage:
#   ./scripts/update.sh                   # regenerate every configured API
#   ./scripts/update.sh --refresh         # git pull ../gerrit-openapi first, then all
#   ./scripts/update.sh checks            # just one API
#   ./scripts/update.sh --refresh checks  # refresh, then one API
set -euo pipefail
cd "$(dirname "$0")/.."

refresh=0
apis=()
for arg in "$@"; do
  case "$arg" in
    --refresh | -r) refresh=1 ;;
    -*)
      echo "unknown flag: $arg" >&2
      exit 2
      ;;
    *) apis+=("$arg") ;;
  esac
done

# Step 2: optionally pull the published specs from the sibling gerrit-openapi
# checkout. --ff-only keeps it safe (no merge commits, never discards local work).
if [ "$refresh" -eq 1 ]; then
  upstream="../gerrit-openapi"
  printf '== refresh %s ==\n' "$upstream"
  if [ -d "$upstream/.git" ]; then
    git -C "$upstream" pull --ff-only
  else
    echo "   $upstream is not a git checkout; refresh it yourself, then re-run." >&2
    exit 1
  fi
fi

if [ "${#apis[@]}" -eq 0 ]; then
  # APIs are discovered from the module, so this covers core / checks / code-owners
  # automatically as they are wired into profiles.API_SPECS.
  apis=($(uv run python -c "from gerrit_openapi_mcp.profiles import API_SPECS; print(' '.join(API_SPECS))"))
fi

status=0
for api in "${apis[@]}"; do
  printf '== %s ==\n' "$api"
  err=$(mktemp)
  if uv run gerrit-openapi-mcp --api "$api" generate >/dev/null 2>"$err"; then
    out="generated"
    [ "$api" = "core" ] || out="generated/$api"
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
