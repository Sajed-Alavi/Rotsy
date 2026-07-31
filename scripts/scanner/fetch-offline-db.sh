#!/usr/bin/env bash
# Fetch the Trivy + Grype vulnerability databases for OFFLINE import.
#
# Run this on a machine WITH internet access, copy the resulting files into
# nexus-project/offline-db/ on the restricted host, then trigger the import from
# the dashboard ("Import offline DBs") or with POST /api/scan/db-import.
#
# Requires: oras (https://oras.land) and curl. jq is optional but recommended.
#
# Usage:
#   ./fetch-offline-db.sh [OUTPUT_DIR]
#   OUTPUT_DIR defaults to ../offline-db relative to this script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$SCRIPT_DIR/../offline-db}"
mkdir -p "$OUT"
echo "[fetch] output dir: $OUT"

# --- Trivy databases (OCI artifacts) ---------------------------------------
if command -v oras >/dev/null 2>&1; then
  echo "[fetch] pulling trivy-db:2 ..."
  oras pull registry-1.docker.io/aquasec/trivy-db:2 --output "$OUT"   # → db.tar.gz
  echo "[fetch] pulling trivy-java-db:1 (optional) ..."
  oras pull ghcr.io/aquasecurity/trivy-java-db:1 --output "$OUT" ||
    echo "[fetch] WARN: java-db pull failed — continuing (affects Java scanning only)"
else
  echo "[fetch] ERROR: oras is not installed. Install it from https://oras.land" >&2
  echo "        (needed to fetch the Trivy database from the OCI registry)" >&2
fi

# --- Grype database ---------------------------------------------------------
# Grype's database format is versioned and the *installed grype* decides which
# schema it will accept. Importing the wrong schema fails with a validation
# error, so try the current v6 endpoint first and fall back to the legacy v5
# listing. Match this to the grype in the backend image ("grype version" prints
# the supported DB schema).
echo "[fetch] resolving the latest Grype database ..."
V6_BASE="https://grype.anchore.io/databases/v6"
if LATEST="$(curl -fsSL "$V6_BASE/latest.json" 2>/dev/null)"; then
  if command -v jq >/dev/null 2>&1; then
    DB_PATH="$(printf '%s' "$LATEST" | jq -r '.path // empty')"
  else
    DB_PATH="$(printf '%s' "$LATEST" | grep -oE 'vulnerability-db_v6[^"]+\.tar\.(zst|gz)' | head -1)"
  fi
  if [ -n "${DB_PATH:-}" ]; then
    echo "[fetch] downloading Grype DB (schema v6): $DB_PATH"
    curl -fSL "$V6_BASE/$DB_PATH" -o "$OUT/$(basename "$DB_PATH")"
  else
    echo "[fetch] ERROR: could not read the database path out of latest.json" >&2
  fi
else
  echo "[fetch] v6 endpoint unavailable — falling back to the legacy v5 listing"
  if LISTING="$(curl -fsSL https://grype.anchore.io/databases/listing.json 2>/dev/null)"; then
    if command -v jq >/dev/null 2>&1; then
      URL="$(printf '%s' "$LISTING" | jq -r '.available["5"][0].url // empty')"
    else
      URL="$(printf '%s' "$LISTING" | grep -oE 'https?://[^"]+\.tar\.(gz|zst)' | head -1)"
    fi
    if [ -n "${URL:-}" ]; then
      echo "[fetch] downloading Grype DB (schema v5): $URL"
      curl -fSL "$URL" -o "$OUT/grype-db.tar.gz"
    else
      echo "[fetch] ERROR: could not determine the Grype database URL" >&2
    fi
  else
    echo "[fetch] ERROR: could not reach either Grype database endpoint" >&2
  fi
fi

echo ""
echo "[fetch] done. Contents of $OUT:"
ls -lh "$OUT"
cat <<'EOF'

Next steps
  1. Copy these files to the restricted host's nexus-project/offline-db/.
  2. Import them: dashboard → Vulnerability Scanning → "Import offline DBs",
     or POST /api/scan/db-import.
  3. Confirm both databases report "ready" on the dashboard's database cards
     (GET /api/scan/db-status). Scans fail until they do.
EOF
