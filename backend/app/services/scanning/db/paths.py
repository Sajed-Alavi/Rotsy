"""Where the scanner databases live on disk, and the constants describing them.

Split out so ``status``, ``update`` and ``offline`` all agree on one set of
locations instead of each carrying its own copy.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Awaitable, Callable

# Progress reporting contract.
#
# ``detail`` carries the structured numbers behind the message so the UI can
# render a real bar, speed and ETA. Everything used to be flattened into the
# percent and a formatted string, which meant bytes/speed/ETA were computed and
# then thrown away — the browser could only show a spinner. The string is kept
# as the human-readable fallback (logs, the jobs list), it is just no longer the
# only thing that survives.
#
# Recognised ``detail`` keys, all optional:
#   scanner      "trivy" | "grype"
#   stage        machine-readable step, see STAGES below
#   done_bytes   bytes fetched so far
#   total_bytes  expected total, when knowable
#   estimated    True when total_bytes is a guess rather than reported by the tool
#   speed_bps    bytes/second over the last sample window
#   eta_seconds  seconds remaining at the current rate
ProgressCallback = Callable[..., Awaitable[None]]

# Stages a database update moves through. The UI labels them; it does not parse
# the human message.
STAGES = (
    "connecting",   # resolving the registry / opening the connection
    "downloading",  # bytes are moving
    "extracting",   # unpacking an archive into the cache
    "importing",    # offline archive being imported
    "verifying",    # scanner validating what it just wrote
    "done",
    "failed",
    "skipped",      # already current, nothing to do
)

# Cache locations. Trivy lays its database out as <cache>/db/metadata.json;
# Grype uses <cache>/db/<schema>/. The Dockerfile sets TRIVY_CACHE_DIR and
# GRYPE_CACHE_DIR and pre-creates them writable for the non-root app user.
TRIVY_CACHE_ROOT = Path(os.environ.get("TRIVY_CACHE_DIR") or (Path.home() / ".cache" / "trivy"))
TRIVY_DB_DIR = TRIVY_CACHE_ROOT / "db"
TRIVY_JAVA_DB_DIR = TRIVY_CACHE_ROOT / "java-db"
GRYPE_CACHE_ROOT = Path(os.environ.get("GRYPE_CACHE_DIR") or (Path.home() / ".cache" / "grype"))

# Offline / air-gapped import directory. Where Docker Hub, ghcr.io and github.com
# are blocked the databases cannot be pulled at runtime: an operator downloads
# them on a connected machine, drops the archives into this host folder (mounted
# by docker-compose) and triggers an import, which extracts them straight into
# the scanner caches. See :mod:`app.services.scanning.db.offline`.
OFFLINE_DB_DIR = Path(os.environ.get("SCANNER_OFFLINE_DIR") or "/app/offline-db")

# A Grype database older than this is reported as stale by ``readiness``.
# Grype's own default is to *refuse* a database older than 5 days; we scan with
# that check disabled (a slightly stale database beats no scan at all) but the
# operator still needs to see that it is aging.
STALE_AFTER = timedelta(days=5)

# Approximate archive sizes, used only to render download progress.
TRIVY_DB_MB = 50
TRIVY_JAVA_DB_MB = 125

TRIVY_DB_IMAGE = "registry-1.docker.io/aquasec/trivy-db:2"
TRIVY_JAVA_DB_IMAGE = "ghcr.io/aquasecurity/trivy-java-db:1"

# Grype's progress line: "Vulnerability DB [30 MB / 208 MB]".
GRYPE_PROGRESS = re.compile(r"\[\s*([\d.]+)\s*([KMGTP]?B)\s*/\s*([\d.]+)\s*([KMGTP]?B)\s*\]")
SI_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


def which(binary: str) -> str | None:
    """Absolute path of a scanner binary, or None when it is not installed."""
    return shutil.which(binary)
