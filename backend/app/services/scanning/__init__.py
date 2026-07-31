"""Static vulnerability scanning of Nexus-hosted images with Trivy and Grype.

**Static analysis only.** Neither scanner is allowed to touch a container
runtime: images are read over the Docker Registry v2 API (manifest → config →
layer blobs) and analysed as data. Nothing is ever started, run or spun up. This
is enforced, not merely intended:

  * Trivy runs with ``--image-src remote``, so it never probes the local
    docker/containerd/podman daemons.
  * Grype is given an explicit ``registry:`` reference and
    ``GRYPE_DEFAULT_IMAGE_PULL_SOURCE=registry``; by default it would try the
    container runtimes first.
  * :func:`.base.assert_static_ref` rejects any reference carrying a runtime scheme.
  * The backend container mounts no Docker socket (see docker-compose.yml).

The registry endpoint is discovered from Nexus at scan time (see
:mod:`.registry`) — there is no configured registry URL or port.

Package layout — one concern per module, so a scanner backend can be read (or
added) without touching persistence, and the parsers are testable on their own:

  ``base``         shared types, subprocess exec, JSON/stderr parsing, static-ref guard
  ``trivy``        the Trivy adapter and its report parser
  ``grype``        the Grype adapter and its report parser
  ``persistence``  runner registry, orchestration, ORM writes
  ``registry``     Docker connector discovery from Nexus
  ``events``       push/webhook triggers and the scanned-image ledger
  ``db``           vulnerability-database lifecycle (status/update/offline import)

Root causes of the ``FAILED`` reports this code previously produced, and what
fixes them:

1. *Wrong endpoint.* The reference was built from a hand-configured registry
   URL, falling back to ``{nexus-host}:8081/{repo}/{image}`` — a path Nexus does
   not serve the v2 API on, so every scan 404'd. Now resolved by discovery.
2. *TLS conflated with the REST connection.* Plaintext/TLS handling was driven by
   ``NEXUS_VERIFY_SSL`` (which describes the REST API), so a plaintext connector
   behind an HTTPS Nexus was probed over TLS. Now driven by the connector the
   repository actually declares.
3. *Mid-scan database updates.* Both tools try to refresh their database when
   asked to scan, and Grype outright refuses a database older than five days.
   On a restricted network that download fails and takes the scan down with it.
   Updates are now disabled during scans and owned by :mod:`.db`, with a
   preflight that reports a missing database as such.
4. *Unusable diagnostics.* Failures were truncated to 500 characters and dropped
   into a JSON blob. The command line, exit code and output tail are now
   persisted on the report and surfaced in the UI.
5. *Credentials on the command line.* ``--username``/``--password`` put the Nexus
   password in the process table. Both scanners now take credentials from the
   environment.
"""

from __future__ import annotations

from .base import SEVERITIES, Credentials, ScanOutcome
from .persistence import apply_outcome, reap_stale_reports, scan_image, severity_from_cvss

__all__ = [
    "SEVERITIES",
    "Credentials",
    "ScanOutcome",
    "apply_outcome",
    "reap_stale_reports",
    "scan_image",
    "severity_from_cvss",
]
