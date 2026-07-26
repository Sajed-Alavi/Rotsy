"""Retention policy execution.

A :class:`RetentionPolicy` combines two optional conditions:
  * ``keep_last_n`` — keep only the most recent N components, delete the rest.
  * ``delete_older_than_days`` — delete components older than X days.

Both are applied when set. Deletion happens via Nexus' component DELETE
endpoint, then the relevant ``blobstore.compact`` task is triggered so the
physical blobs are actually reclaimed (otherwise the disk space stays
allocated and only the metadata is removed).

This module is called both by the periodic scheduler and by the
``run_retention`` background job handler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.nexus_client import NexusClient
from ..models import RetentionPolicy

logger = logging.getLogger(__name__)


async def _collect_components(nexus: NexusClient, repo: str) -> list[dict]:
    """Return all components in ``repo`` (paginated)."""
    out = []
    async for c in nexus.paginate("/service/rest/v1/components", params={"repository": repo}):
        out.append(c)
    return out


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _select_for_deletion(components: list[dict], policy: RetentionPolicy) -> list[dict]:
    """Apply the policy to a list of components; return those to delete."""
    to_delete: list[dict] = []

    # Attach a sortable timestamp to each component.
    enriched = []
    for c in components:
        ts = _parse_iso(c.get("blobCreated") or c.get("lastModified") or c.get("assets", [{}])[0].get("blobCreated"))
        enriched.append((c, ts or datetime.min.replace(tzinfo=timezone.utc)))

    # delete_older_than_days
    if policy.delete_older_than_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=policy.delete_older_than_days)
        for c, ts in enriched:
            if ts < cutoff:
                to_delete.append(c)

    # keep_last_n: sort newest-first, keep first N, delete the rest (that
    # weren't already selected above).
    if policy.keep_last_n is not None and policy.keep_last_n >= 0:
        sorted_newest = [c for c, _ in sorted(enriched, key=lambda x: x[1], reverse=True)]
        keep_ids = {sorted_newest[i].get("id") for i in range(min(policy.keep_last_n, len(sorted_newest)))}
        for c in components:
            if c.get("id") not in keep_ids and c not in to_delete:
                to_delete.append(c)

    # Dedupe by id while preserving order.
    seen = set()
    deduped = []
    for c in to_delete:
        cid = c.get("id")
        if cid not in seen:
            seen.add(cid)
            deduped.append(c)
    return deduped


async def _delete_component(nexus: NexusClient, component_id: str) -> bool:
    """DELETE a component by id. Returns True on success."""
    try:
        resp = await nexus.client.delete(f"/service/rest/v1/components/{component_id}")
        return resp.status_code in (200, 204)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to delete component %s: %s", component_id, exc)
        return False


async def _trigger_compact(nexus: NexusClient) -> None:
    """Trigger the 'Compact blob store' administrative task so deleted blobs
    are physically reclaimed. Best-effort: Nexus may need the task to exist.
    """
    try:
        # List tasks, find one whose typeId is 'blobstore.compact'.
        resp = await nexus.client.get("/service/rest/v1/scheduler/tasks")
        if resp.status_code != 200:
            return
        for t in resp.json() or []:
            if t.get("typeId") == "blobstore.compact" or "compact" in (t.get("type") or "").lower():
                task_id = t.get("id")
                if task_id:
                    await nexus.client.post(f"/service/rest/v1/scheduler/run/{task_id}")
                    logger.info("Triggered compact task %s", task_id)
                    return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not trigger compact task: %s", exc)


async def run_policy(
    nexus: NexusClient,
    session: AsyncSession,
    policy: RetentionPolicy,
    *,
    dry_run: bool = False,
    on_progress=None,
) -> dict:
    """Execute one retention policy.

    Returns ``{repo, deleted, skipped, dry_run}``. When ``dry_run`` is true
    no deletion happens — the result lists what *would* be deleted.
    """
    async def emit(p, m):
        if on_progress is not None:
            await on_progress(p, m)

    await emit(0, f"listing components in {policy.repo}")
    components = await _collect_components(nexus, policy.repo)
    await emit(30, f"{len(components)} components")

    targets = _select_for_deletion(components, policy)
    await emit(50, f"{len(targets)} to delete")

    deleted = 0
    if not dry_run:
        total = max(1, len(targets))
        for i, c in enumerate(targets):
            cid = c.get("id")
            if cid and await _delete_component(nexus, cid):
                deleted += 1
            if (i + 1) % 5 == 0 or i + 1 == len(targets):
                await emit(50 + int((i + 1) / total * 40), f"deleted {i + 1}/{len(targets)}")

        # Reclaim physical space for the deleted blobs.
        await emit(92, "triggering blob compaction")
        await _trigger_compact(nexus)

        policy.last_run_at = datetime.now(timezone.utc)
        await session.commit()
    else:
        deleted = len(targets)

    await emit(100, "done")
    return {
        "repo": policy.repo,
        "policy": policy.name,
        "candidate_count": len(targets),
        "deleted": deleted,
        "dry_run": dry_run,
        "candidates": [
            {"id": c.get("id"), "name": c.get("name"), "version": c.get("version")}
            for c in targets[:50]  # cap preview
        ],
    }


async def run_all_enabled(nexus: NexusClient, session: AsyncSession, *, dry_run: bool = False, on_progress=None) -> list[dict]:
    """Run every enabled retention policy. Returns per-policy results."""
    from sqlalchemy import select
    rows = (await session.execute(select(RetentionPolicy).where(RetentionPolicy.enabled.is_(True)))).scalars().all()
    results = []
    for p in rows:
        try:
            results.append(await run_policy(nexus, session, p, dry_run=dry_run, on_progress=on_progress))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Retention policy %s failed", p.name)
            results.append({"repo": p.repo, "policy": p.name, "error": str(exc)})
    return results
