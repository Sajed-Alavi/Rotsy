"""Retention policy execution.

A :class:`RetentionPolicy` combines two optional conditions:
  * ``keep_last_n`` — keep the most recent N versions **of each image**.
  * ``delete_older_than_days`` — delete components older than X days.

Both are applied when set. Deletion goes through
:func:`app.services.images.delete_component` so each failure carries a reason,
then the ``blobstore.compact`` task is triggered — without it Nexus removes the
tag but leaves the blobs on disk, so nothing appears to have been freed.

This module is called both by the daily scheduler and by the ``run_retention``
background job handler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.nexus_client import NexusClient
from ..models import RetentionPolicy
from .images import component_timestamps, delete_component, trigger_compact

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
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _component_time(component: dict) -> datetime | None:
    """Best available timestamp for a component, or None when unknown.

    Nexus reports timestamps on **assets**, never on the component itself, so
    reading ``component["blobCreated"]`` always yielded ``None``. The previous
    fallback then indexed ``component["assets"][0]`` — which raises IndexError
    on a component with an empty asset list and aborted the entire policy run,
    deleting nothing at all.

    Returning None rather than ``datetime.min`` matters just as much: an unknown
    age used to read as "infinitely old", so an age-based policy would happily
    delete every component whose timestamp could not be parsed.
    """
    created, _modified = component_timestamps(component)
    return _parse_iso(created)


def _group_key(component: dict) -> str:
    """The unit ``keep_last_n`` counts within: one image (or artifact) name."""
    group = component.get("group") or ""
    name = component.get("name") or ""
    return f"{group}/{name}".lstrip("/")


def _select_for_deletion(components: list[dict], policy: RetentionPolicy) -> tuple[list[dict], list[dict]]:
    """Apply a policy; return ``(to_delete, undated)``.

    ``undated`` lists components whose age could not be determined. They are
    never deleted by an age rule — the run reports them so the operator can see
    that they were skipped instead of silently losing data or silently keeping it.

    ``keep_last_n`` counts **per image name**, not across the whole repository.
    Repository-wide counting was the bug behind "I have 4 images and it deleted
    the wrong thing / nothing": with `keep_last_n=3` and four separate images of
    one tag each, it kept the three newest images in the entire repository and
    deleted the fourth image outright, rather than keeping the last three tags of
    each image.
    """
    to_delete: dict[str, dict] = {}
    undated: list[dict] = []

    dated: list[tuple[dict, datetime | None]] = []
    for component in components:
        timestamp = _component_time(component)
        if timestamp is None:
            undated.append(component)
        dated.append((component, timestamp))

    if policy.delete_older_than_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=policy.delete_older_than_days)
        for component, timestamp in dated:
            if timestamp is not None and timestamp < cutoff:
                to_delete[str(component.get("id"))] = component

    if policy.keep_last_n is not None and policy.keep_last_n >= 0:
        by_name: dict[str, list[tuple[dict, datetime | None]]] = {}
        for component, timestamp in dated:
            by_name.setdefault(_group_key(component), []).append((component, timestamp))
        for entries in by_name.values():
            # Newest first. Undated components sort last, so a component of
            # unknown age is a deletion candidate before a known-recent one.
            entries.sort(
                key=lambda pair: pair[1] or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            for component, _ in entries[policy.keep_last_n:]:
                to_delete[str(component.get("id"))] = component

    return list(to_delete.values()), undated


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

    targets, undated = _select_for_deletion(components, policy)
    await emit(50, f"{len(targets)} to delete")

    deleted = 0
    failures: list[dict[str, str]] = []
    compact: dict[str, Any] = {}

    if not dry_run:
        total = max(1, len(targets))
        for index, component in enumerate(targets):
            component_id = component.get("id")
            if not component_id:
                failures.append({"name": component.get("name", "?"),
                                 "version": component.get("version", "?"),
                                 "reason": "Nexus did not return a component id"})
                continue
            ok, reason = await delete_component(nexus, component_id)
            if ok:
                deleted += 1
            else:
                # Record why, rather than counting it as a silent no-op. A run
                # reporting "0 deleted" with no reason is impossible to debug.
                failures.append({"name": component.get("name", "?"),
                                 "version": component.get("version", "?"),
                                 "reason": reason})
            if (index + 1) % 5 == 0 or index + 1 == len(targets):
                await emit(50 + int((index + 1) / total * 40),
                           f"deleted {deleted}/{len(targets)}"
                           + (f" ({len(failures)} failed)" if failures else ""))

        if deleted:
            await emit(92, "triggering blob compaction")
            compact = await trigger_compact(nexus)

        policy.last_run_at = datetime.now(timezone.utc)
        await session.commit()
    else:
        deleted = len(targets)

    summary = f"{deleted} deleted"
    if failures:
        summary += f", {len(failures)} failed: {failures[0]['reason']}"
    if undated:
        summary += f", {len(undated)} skipped (no timestamp)"
    await emit(100, summary)

    return {
        "repo": policy.repo,
        "policy": policy.name,
        "candidate_count": len(targets),
        "deleted": deleted,
        "failed_count": len(failures),
        "failures": failures[:20],
        "skipped_undated": len(undated),
        "compact": compact,
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
