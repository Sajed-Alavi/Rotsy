#!/usr/bin/env bash
# Fetch Trivy + Grype vulnerability DB archives for OFFLINE import.
#
# Run this on a machine WITH internet access, then copy the resulting files
# into nexus-project/offline-db/ on the restricted host and trigger the import
# from the dashboard ("Import offline DBs") or POST /api/scan/db-import.
#
# Requires: oras (https://oras.land) and curl. jq is optional (nicer output).
#
# Usage:
#   ./fetch-offline-db.sh [OUTPUT_DIR]
#   OUTPUT_DIR defaults to ../offline-db relative to this script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$SCRIPT_DIR/../offline-db}"
mkdir -p "$OUT"
echo "[fetch] output dir: $OUT"

# --- Trivy main DB (schema v2) ---------------------------------------------
if command -v oras >/dev/null 2>&1; then
  echo "[fetch] pulling trivy-db:2 ..."
  oras pull registry-1.docker.io/aquasec/trivy-db:2 --output "$OUT"
  # oras extracts db.tar.gz into OUT
  echo "[fetch] pulling trivy-java-db:1 (optional) ..."
  oras pull ghcr.io/aquasecurity/trivy-java-db:1 --output "$OUT" || \
    echo "[fetch] WARN: java-db pull failed — continuing (Java scanning only)"
else
  echo "[fetch] ERROR: oras not installed. Install from https://oras.land" >&2
  echo "        (needed to fetch the Trivy DB from the OCI registry)" >&2
fi

# --- Grype DB (schema v5) ---------------------------------------------------
echo "[fetch] resolving latest Grype DB (schema v5) ..."
LISTING="$(curl -fsSL https://grype.anchore.io/databases/listing.json)" || {
  echo "[fetch] ERROR: could not fetch grype listing.json" >&2
  LISTING=""
}
if [ -n "$LISTING" ]; then
  if command -v jq >/dev/null 2>&1; then
    URL="$(printf '%s' "$LISTING" | jq -r '.available["5"][0].url')"
  else
    # crude fallback: first http(s) url after the "5": key
    URL="$(printf '%s' "$LISTING" | grep -oE 'https?://[^"]+\.tar\.(gz|zst)' | head -1)"
  fi
  if [ -n "${URL:-}" ] && [ "$URL" != "null" ]; then
    echo "[fetch] downloading grype DB: $URL"
    curl -fSL "$URL" -o "$OUT/grype-db.tar.gz"
  else
    echo "[fetch] ERROR: could not determine grype DB url from listing" >&2
  fi
fi

echo ""
echo "[fetch] done. Contents of $OUT:"
ls -lh "$OUT"
echo ""
echo "Next: copy these files to the restricted host's nexus-project/offline-db/,"
echo "then run the import (dashboard button or POST /api/scan/db-import)."
