"""Vulnerability-database lifecycle for Trivy and Grype.

This package is the **single** owner of scanner database state. Before it
existed the same job was spread across four places that could disagree with
each other (a shell script run from the entrypoint, an unconditional refresh
enqueued on every boot, an ``oras``-based downloader and an offline importer);
scans then failed for reasons none of them reported. Everything now lives here
and every caller goes through one of the functions re-exported below:

  * :func:`status`         — what is on disk (version, build date, size, path).
  * :func:`readiness`      — is each scanner *able to scan right now*, and if not why.
  * :func:`update`         — refresh from the network (with live progress).
  * :func:`import_offline` — install from pre-downloaded archives (no network).

Internally it is split by lifecycle stage rather than by scanner, so the
Trivy/Grype pair for one concern sits together:

  ``paths``    locations and constants every stage shares
  ``status``   read-only inspection of what is on disk + readiness
  ``process``  subprocess, download-progress and archive primitives
  ``update``   online refresh
  ``offline``  air-gapped import

**Why the scanners must never update their own DB mid-scan.** Both tools will,
by default, try to refresh their database when they are asked to scan. On a
restricted network that download fails and the *scan* fails with it — which is
exactly how a working scanner ends up reporting ``FAILED``. Scans therefore run
with auto-update disabled (see :mod:`app.modules.nexus.trivy` and
:mod:`app.modules.nexus.grype`) and the database is managed only through
this package. :func:`readiness` is what turns "no database" into an actionable
message instead of an opaque scanner exit code.
"""

from __future__ import annotations

from .offline import import_offline, offline_status
from .paths import (
    GRYPE_CACHE_ROOT,
    OFFLINE_DB_DIR,
    STALE_AFTER,
    TRIVY_CACHE_ROOT,
    TRIVY_DB_DIR,
    TRIVY_JAVA_DB_DIR,
    TRIVY_JAVA_DB_IMAGE,
    TRIVY_JAVA_DB_IMAGE_FALLBACK,
    ProgressCallback,
    which,
)
from .process import proxy_env
from .status import Readiness, readiness, status
from .update import update

__all__ = [
    "GRYPE_CACHE_ROOT",
    "OFFLINE_DB_DIR",
    "ProgressCallback",
    "Readiness",
    "STALE_AFTER",
    "TRIVY_CACHE_ROOT",
    "TRIVY_DB_DIR",
    "TRIVY_JAVA_DB_DIR",
    "TRIVY_JAVA_DB_IMAGE",
    "TRIVY_JAVA_DB_IMAGE_FALLBACK",
    "import_offline",
    "offline_status",
    "proxy_env",
    "readiness",
    "status",
    "update",
    "which",
]
