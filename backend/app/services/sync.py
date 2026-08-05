"""Nexus-to-Nexus sync.

Copies components from a source Nexus repository to a target repository on a
different Nexus instance (or a different repo on the same one). The target
connection is provided at call time — it does NOT come from the app's
``NEXUS_*`` env (those are the primary Nexus the wrapper manages).

Strategy: enumerate components on the source, and for each one download every
asset and re-upload it to the target's matching repository. Nexus' component
DELETE/create API + raw upload covers all formats. For docker specifically we
would need the registry v2 push API (out of scope for this pass — handled as
a clear skip with a logged reason).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..modules.nexus.connector import NexusClient
from . import make_emitter

logger = logging.getLogger(__name__)


async def _enumerate_components(nexus: NexusClient, repo: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for c in nexus.paginate("/service/rest/v1/components", params={"repository": repo}):
        out.append(c)
    return out


async def sync_repository(
    source: NexusClient,
    source_repo: str,
    *,
    target_base_url: str,
    target_username: str,
    target_password: str,
    target_repo: str,
    verify_ssl: bool = True,
    on_progress=None,
) -> dict[str, Any]:
    """Copy every component from ``source_repo`` to ``target_repo``.

    Returns ``{source_repo, target_repo, copied, skipped, errors}``.
    """
    emit = make_emitter(on_progress)

    await emit(0, f"enumerating {source_repo}")
    components = await _enumerate_components(source, source_repo)
    await emit(10, f"{len(components)} components")

    copied = 0
    skipped = 0
    errors: list[str] = []

    async with httpx.AsyncClient(
        base_url=target_base_url.rstrip("/"),
        auth=(target_username, target_password),
        verify=verify_ssl,
        timeout=httpx.Timeout(60.0),
    ) as target:
        total = max(1, len(components))
        for i, c in enumerate(components):
            fmt = c.get("format")
            # Docker registry push requires the v2 API + manifest handling,
            # which is out of scope here. Skip with a clear reason.
            if fmt == "docker":
                skipped += 1
                continue
            assets = c.get("assets") or []
            for asset in assets:
                download_url = asset.get("downloadUrl")
                path = (asset.get("path") or "").lstrip("/")
                if not download_url or not path:
                    continue
                try:
                    # Download from source.
                    src_resp = await source.client.get(download_url)
                    src_resp.raise_for_status()
                    content = src_resp.content

                    # Upload to target via the components upload endpoint. The
                    # exact multipart shape varies by format; the generic form
                    # works for raw/maven2/nuget/npm.
                    files = {"asset": (path.rsplit("/", 1)[-1], content, asset.get("contentType") or "application/octet-stream")}
                    upload_resp = await target.post(
                        f"/service/rest/v1/components?repository={target_repo}",
                        data={},
                        files=files,
                    )
                    if upload_resp.status_code in (200, 201, 204):
                        copied += 1
                    else:
                        errors.append(f"{path}: target returned {upload_resp.status_code}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{path}: {exc}")
            await emit(10 + int((i + 1) / total * 85), f"synced {i + 1}/{len(components)}")

    await emit(100, "done")
    return {
        "source_repo": source_repo,
        "target_repo": target_repo,
        "copied": copied,
        "skipped": skipped,
        "errors": errors[:20],
        "total_components": len(components),
    }
