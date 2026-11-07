#!/usr/bin/env bash
# Download the cognee-mcp source (from github.com/topoteretes/cognee) into ./cognee-mcp,
# pinned to a known commit so builds are reproducible.
#
#   scripts/fetch-cognee-mcp.sh            # pinned commit below
#   scripts/fetch-cognee-mcp.sh <ref>      # another commit, tag or branch
#
# Update the pin after testing a newer version. Keep the local (~/.venv) and image
# cognee versions in step: they share one storage format.
set -euo pipefail

# cognee-mcp 0.5.8, locks cognee 1.6.7 (main, 2026-11-06).
PINNED_REF="dcb8d7c6bf80a097005fb3aded6cdc3599225de8"

REF="${1:-$PINNED_REF}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/cognee-mcp"

if [ -e "$DEST" ]; then
    echo "$DEST already exists; remove it first to fetch again." >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Fetching cognee-mcp at $REF ..."
curl -fsSL "https://github.com/topoteretes/cognee/archive/$REF.tar.gz" | tar -xz -C "$TMP"
mv "$TMP"/cognee-*/cognee-mcp "$DEST"
echo "$REF" > "$DEST/.fetched-ref"
echo "Done: $DEST"
