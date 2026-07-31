"""Install vulnerability databases from pre-downloaded archives — no network.

For air-gapped or restricted networks where Docker Hub, ghcr.io and github.com
are blocked: an operator fetches the archives on a connected machine (see
``scripts/scanner/fetch-offline-db.sh``), drops them into the mounted host
folder, and triggers an import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .paths import OFFLINE_DB_DIR, TRIVY_DB_DIR, TRIVY_JAVA_DB_DIR, ProgressCallback, which
from .process import extract, prune_trivy, run_streaming


def offline_status() -> dict[str, Any]:
    """Report which archives are present in the offline import directory.

    Recognised filenames (case-insensitive):
      * Trivy database:      ``db.tar.gz`` or ``trivy-db.tar.gz``
      * Trivy Java database: ``javadb.tar.gz`` or ``trivy-java-db.tar.gz`` (optional)
      * Grype database:      ``grype-db.tar.*`` or ``vulnerability-*.tar.*``
    """
    out: dict[str, Any] = {"dir": str(OFFLINE_DB_DIR), "exists": OFFLINE_DB_DIR.exists(), "files": []}
    if not OFFLINE_DB_DIR.exists():
        return out
    files = [
        {"name": p.name, "size_bytes": p.stat().st_size}
        for p in sorted(OFFLINE_DB_DIR.iterdir()) if p.is_file()
    ]
    names = {f["name"].lower() for f in files}
    out["files"] = files
    out["trivy_db"] = any(n in names for n in ("db.tar.gz", "trivy-db.tar.gz"))
    out["grype_db"] = any(
        n.startswith(("grype-db", "vulnerability")) and n.endswith((".tar.gz", ".tar.zst", ".tar"))
        for n in names
    )
    return out


def _find_offline(*exact: str, prefixes: tuple[str, ...] = (), suffixes: tuple[str, ...] = ()) -> Path | None:
    """First offline archive matching an exact name, else a prefix/suffix pair."""
    if not OFFLINE_DB_DIR.exists():
        return None
    candidates = [p for p in sorted(OFFLINE_DB_DIR.iterdir()) if p.is_file()]
    for path in candidates:
        if path.name.lower() in exact:
            return path
    if prefixes and suffixes:
        for path in candidates:
            name = path.name.lower()
            if name.startswith(prefixes) and name.endswith(suffixes):
                return path
    return None


async def import_offline(
    scanners: list[str],
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Install databases from pre-downloaded archives — no network access.

    Trivy archives are extracted straight into its cache layout (identical to
    what the online path does after the pull). Grype goes through
    ``grype db import``, its supported offline path, which validates the
    checksum and schema before installing.
    """
    async def emit(pct: int, msg: str, detail: dict[str, Any] | None = None) -> None:
        if on_progress is not None:
            await on_progress(pct, msg, detail)

    enabled = [s.strip().lower() for s in scanners if s.strip()]
    if not OFFLINE_DB_DIR.exists():
        message = (f"the offline database directory {OFFLINE_DB_DIR} does not exist — "
                   "create ./offline-db on the host and drop the archives in")
        await emit(100, message, {"stage": "failed", "error": message})
        return {s: {"ok": False, "error": message} for s in enabled}

    results: dict[str, Any] = {}
    if "trivy" in enabled:
        results["trivy"] = await _import_trivy(emit)
    if "grype" in enabled:
        results["grype"] = await _import_grype(emit)
    if not results:
        await emit(100, "no scanners enabled", {"stage": "done"})
    return results


async def _import_trivy(emit: ProgressCallback) -> dict[str, Any]:
    await emit(5, "trivy: looking for an offline database archive",
               {"scanner": "trivy", "stage": "importing"})
    db_tar = _find_offline("db.tar.gz", "trivy-db.tar.gz")
    if db_tar is None:
        await emit(50, "trivy: no offline archive found",
                   {"scanner": "trivy", "stage": "failed", "error": "no archive found"})
        return {"ok": False, "error": f"no db.tar.gz / trivy-db.tar.gz in {OFFLINE_DB_DIR}"}
    java_tar = _find_offline("javadb.tar.gz", "trivy-java-db.tar.gz")
    try:
        await emit(15, f"trivy: extracting {db_tar.name}",
                   {"scanner": "trivy", "stage": "extracting", "artifact": db_tar.name,
                    "total_bytes": db_tar.stat().st_size, "estimated": False})
        extract(db_tar, TRIVY_DB_DIR)
        if java_tar is not None:
            await emit(30, f"trivy: extracting {java_tar.name}",
                       {"scanner": "trivy", "stage": "extracting", "artifact": java_tar.name,
                        "total_bytes": java_tar.stat().st_size, "estimated": False})
            extract(java_tar, TRIVY_JAVA_DB_DIR)
        prune_trivy()
        await emit(50, "trivy: offline database imported", {"scanner": "trivy", "stage": "done"})
        return {"ok": True, "source": db_tar.name, "java_db": java_tar is not None}
    except Exception as exc:  # noqa: BLE001
        await emit(50, f"trivy: import failed — {exc}",
                   {"scanner": "trivy", "stage": "failed", "error": str(exc)})
        return {"ok": False, "error": str(exc)}


async def _import_grype(emit: ProgressCallback) -> dict[str, Any]:
    grype = which("grype")
    if grype is None:
        return {"ok": False, "error": "grype binary not installed"}
    archive = _find_offline(
        "grype-db.tar.gz", "grype-db.tar.zst",
        prefixes=("grype-db", "vulnerability"), suffixes=(".tar.gz", ".tar.zst", ".tar"),
    )
    if archive is None:
        await emit(100, "grype: no offline archive found",
                   {"scanner": "grype", "stage": "failed", "error": "no archive found"})
        return {"ok": False, "error": f"no grype-db.tar.* / vulnerability-*.tar.* in {OFFLINE_DB_DIR}"}
    await emit(60, f"grype: importing {archive.name}",
               {"scanner": "grype", "stage": "importing", "artifact": archive.name,
                "total_bytes": archive.stat().st_size, "estimated": False,
                "indeterminate": True,
                "note": "grype db import reports no incremental progress"})
    try:
        rc, lines = await run_streaming([grype, "db", "import", str(archive)], timeout=900)
    except Exception as exc:  # noqa: BLE001
        await emit(100, f"grype: import failed — {exc}",
                   {"scanner": "grype", "stage": "failed", "error": str(exc)})
        return {"ok": False, "error": str(exc)}
    if rc == 0:
        await emit(100, "grype: offline database imported", {"scanner": "grype", "stage": "done"})
        return {"ok": True, "source": archive.name}
    tail = " | ".join(line for line in lines[-3:] if line.strip())[:300]
    await emit(100, f"grype: import failed (exit {rc})",
               {"scanner": "grype", "stage": "failed", "error": f"exit {rc}: {tail}"})
    return {"ok": False, "error": f"grype db import exited {rc}: {tail}"}
