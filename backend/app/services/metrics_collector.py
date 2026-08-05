"""Periodic metric collection from Nexus.

Walks every repository and writes a ``storage`` metric sample (total bytes +
asset/component counts) to Postgres. Designed to be called by the background
collector loop AND by the ``collect_metrics`` job handler (so users can
trigger an immediate snapshot from the UI).

Heavy work is offloaded: the Nexus client paginates assets per repo, and the
caller (the job runner) reports progress via the callback.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..modules.nexus.connector import NexusClient
from ..models import Metric
from . import make_emitter

logger = logging.getLogger(__name__)

METRIC_TYPE_STORAGE = "storage"
# Blobstore usage samples reuse the ``Metric`` table's ``repo`` column to hold
# the blobstore name rather than a repository name — same shape, different
# resource — so no schema change was needed to add this dimension.
METRIC_TYPE_BLOBSTORE = "blobstore"


async def collect_once(
    nexus: NexusClient,
    session: AsyncSession,
    *,
    on_progress=None,
    retention_days: int = 90,
) -> dict:
    """Collect a snapshot for every repo and persist it.

    Returns a summary ``{repos, total_bytes}``. ``on_progress`` is an optional
    async callback ``(percent: int, message: str) -> None`` used by the job
    runner to update job progress.
    """
    emit = make_emitter(on_progress)

    # Guard: if the Nexus client has no URL configured, fail fast with a
    # clear message instead of a cryptic connection error.
    base_url = str(getattr(nexus.client, "_base_url", ""))
    if not base_url or base_url in ("", "/"):
        raise RuntimeError(
            "Nexus connection is not configured. Set it via Settings → Nexus Connection in the dashboard."
        )

    await emit(0, "listing repositories")
    try:
        resp = await nexus.client.get("/service/rest/v1/repositories")
        resp.raise_for_status()
        repos = [r for r in resp.json() if r.get("name")]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to list repositories for metric collection")
        raise RuntimeError(f"Failed to list repositories: {exc}") from exc

    total = len(repos)
    grand_total_bytes = 0
    samples: list[Metric] = []
    now = datetime.now(timezone.utc)

    for i, repo in enumerate(repos):
        name = repo["name"]
        repo_total = 0
        asset_count = 0
        async for asset in nexus.paginate("/service/rest/v1/assets", params={"repository": name}):
            asset_count += 1
            repo_total += asset.get("fileSize") or 0
        grand_total_bytes += repo_total
        samples.append(
            Metric(
                timestamp=now,
                repo=name,
                metric_type=METRIC_TYPE_STORAGE,
                value_json=json.dumps(
                    {
                        "total_bytes": repo_total,
                        "asset_count": asset_count,
                        "format": repo.get("format"),
                        "type": repo.get("type"),
                        "online": repo.get("online", True),
                    }
                ),
            )
        )
        await emit(int((i + 1) / total * 90), f"{name}: {asset_count} assets")

    if samples:
        session.add_all(samples)
        await _trim_old_metrics(session, retention_days)
        await session.commit()

    await emit(100, f"collected {len(samples)} samples")
    logger.info("Metric collection complete: %d repos, %.2f MB total", len(samples), grand_total_bytes / 1e6)
    return {"repos": len(samples), "total_bytes": grand_total_bytes}


async def collect_blobstore_metrics(nexus: NexusClient, session: AsyncSession) -> dict:
    """Snapshot every blobstore's disk usage and persist it.

    Same shape/store as :func:`collect_once`'s storage samples, just a
    different ``metric_type`` — this is what lets an alert rule reference
    ``blobstore.used_pct`` (see :mod:`app.services.alerting`).
    """
    try:
        resp = await nexus.client.get("/service/rest/v1/blobstores")
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("blobstore metric collection failed: %s", exc)
        return {"blobstores": 0}

    now = datetime.now(timezone.utc)
    samples: list[Metric] = []
    for b in resp.json() or []:
        name = b.get("name")
        if not name:
            continue
        total = int(b.get("totalSizeInBytes") or 0)
        free = int(b.get("availableSpaceInBytes") or 0)
        capacity = total + free
        samples.append(
            Metric(
                timestamp=now,
                repo=name,
                metric_type=METRIC_TYPE_BLOBSTORE,
                value_json=json.dumps({
                    "used_bytes": total,
                    "free_bytes": free,
                    "capacity_bytes": capacity,
                    "used_pct": round(total / capacity * 100, 1) if capacity else 0.0,
                }),
            )
        )
    if samples:
        session.add_all(samples)
        await session.commit()
    return {"blobstores": len(samples)}


async def latest_blobstore_snapshot(session: AsyncSession) -> list[dict]:
    """Most recent blobstore-usage sample per blobstore."""
    sub = (
        select(Metric.repo, func.max(Metric.timestamp).label("ts"))
        .where(Metric.metric_type == METRIC_TYPE_BLOBSTORE)
        .group_by(Metric.repo)
        .subquery()
    )
    stmt = (
        select(Metric)
        .join(sub, (Metric.repo == sub.c.repo) & (Metric.timestamp == sub.c.ts))
        .order_by(Metric.repo)
    )
    rows = (await session.execute(stmt)).scalars().all()
    out = []
    for r in rows:
        try:
            value = json.loads(r.value_json)
        except (json.JSONDecodeError, TypeError):
            value = {}
        out.append({"repo": r.repo, "timestamp": r.timestamp.isoformat(), **value})
    return out


async def blobstore_timeseries(session: AsyncSession, name: str, hours: int = 24) -> list[dict]:
    """Blobstore-usage samples for ``name`` within the last ``hours``."""
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(Metric)
        .where(Metric.repo == name, Metric.metric_type == METRIC_TYPE_BLOBSTORE, Metric.timestamp >= since)
        .order_by(Metric.timestamp)
    )
    rows = (await session.execute(stmt)).scalars().all()
    out = []
    for r in rows:
        try:
            value = json.loads(r.value_json)
        except (json.JSONDecodeError, TypeError):
            value = {}
        out.append({"timestamp": r.timestamp.isoformat(), **value})
    return out


async def _trim_old_metrics(session: AsyncSession, retention_days: int) -> None:
    """Delete metric samples older than the retention window."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    await session.execute(delete(Metric).where(Metric.timestamp < cutoff))


async def latest_snapshot(session: AsyncSession) -> list[dict]:
    """Return the most recent storage sample per repo (for the overview cards)."""
    # Subquery: max timestamp per repo.
    sub = (
        select(Metric.repo, func.max(Metric.timestamp).label("ts"))
        .where(Metric.metric_type == METRIC_TYPE_STORAGE)
        .group_by(Metric.repo)
        .subquery()
    )
    stmt = (
        select(Metric)
        .join(sub, (Metric.repo == sub.c.repo) & (Metric.timestamp == sub.c.ts))
        .order_by(Metric.repo)
    )
    rows = (await session.execute(stmt)).scalars().all()
    out = []
    for r in rows:
        try:
            value = json.loads(r.value_json)
        except (json.JSONDecodeError, TypeError):
            value = {}
        out.append({"repo": r.repo, "timestamp": r.timestamp.isoformat(), **value})
    return out


async def timeseries(session: AsyncSession, repo: str, hours: int = 24) -> list[dict]:
    """Return storage samples for ``repo`` within the last ``hours``."""
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(Metric)
        .where(Metric.repo == repo, Metric.metric_type == METRIC_TYPE_STORAGE, Metric.timestamp >= since)
        .order_by(Metric.timestamp)
    )
    rows = (await session.execute(stmt)).scalars().all()
    out = []
    for r in rows:
        try:
            value = json.loads(r.value_json)
        except (json.JSONDecodeError, TypeError):
            value = {}
        out.append({"timestamp": r.timestamp.isoformat(), **value})
    return out
