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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..modules.nexus.connector import NexusClient
from ..models import RetentionPolicy
from .images import component_timestamps, delete_component, trigger_compact
from . import make_emitter

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


def _split_dated(components: list[dict]) -> tuple[list[tuple[dict, datetime | None]], list[dict]]:
    """Every component paired with its best-known timestamp, plus the subset
    whose age could not be determined at all."""
    dated: list[tuple[dict, datetime | None]] = []
    undated: list[dict] = []
    for component in components:
        timestamp = _component_time(component)
        if timestamp is None:
            undated.append(component)
        dated.append((component, timestamp))
    return dated, undated


def _age_rule_targets(dated: list[tuple[dict, datetime | None]], max_age_days: int) -> dict[str, dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return {
        str(component.get("id")): component
        for component, timestamp in dated
        if timestamp is not None and timestamp < cutoff
    }


def _keep_last_n_targets(dated: list[tuple[dict, datetime | None]], keep_last_n: int) -> dict[str, dict]:
    """``keep_last_n`` counts **per image name**, not across the whole
    repository. Repository-wide counting was the bug behind "I have 4 images
    and it deleted the wrong thing / nothing": with `keep_last_n=3` and four
    separate images of one tag each, it kept the three newest images in the
    entire repository and deleted the fourth image outright, rather than
    keeping the last three tags of each image."""
    by_name: dict[str, list[tuple[dict, datetime | None]]] = {}
    for component, timestamp in dated:
        by_name.setdefault(_group_key(component), []).append((component, timestamp))
    targets: dict[str, dict] = {}
    for entries in by_name.values():
        # Newest first. Undated components sort last, so a component of
        # unknown age is a deletion candidate before a known-recent one.
        entries.sort(
            key=lambda pair: pair[1] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for component, _ in entries[keep_last_n:]:
            targets[str(component.get("id"))] = component
    return targets


def _select_for_deletion(components: list[dict], policy: RetentionPolicy) -> tuple[list[dict], list[dict]]:
    """Apply a policy; return ``(to_delete, undated)``.

    ``undated`` lists components whose age could not be determined. They are
    never deleted by an age rule — the run reports them so the operator can see
    that they were skipped instead of silently losing data or silently keeping it.
    """
    dated, undated = _split_dated(components)

    to_delete: dict[str, dict] = {}
    if policy.delete_older_than_days is not None:
        to_delete.update(_age_rule_targets(dated, policy.delete_older_than_days))
    if policy.keep_last_n is not None and policy.keep_last_n >= 0:
        to_delete.update(_keep_last_n_targets(dated, policy.keep_last_n))

    return list(to_delete.values()), undated


async def _delete_targets(
    nexus: NexusClient, targets: list[dict], emit,
) -> tuple[int, list[dict[str, str]]]:
    """Delete every target component, recording a per-failure reason so a run
    reporting "0 deleted" is never a silent no-op. Returns (deleted_count,
    failures)."""
    deleted = 0
    failures: list[dict[str, str]] = []
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
            failures.append({"name": component.get("name", "?"),
                             "version": component.get("version", "?"),
                             "reason": reason})
        if (index + 1) % 5 == 0 or index + 1 == len(targets):
            await emit(50 + int((index + 1) / total * 40),
                       f"deleted {deleted}/{len(targets)}"
                       + (f" ({len(failures)} failed)" if failures else ""))
    return deleted, failures


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
    emit = make_emitter(on_progress)

    await emit(0, f"listing components in {policy.repo}")
    components = await _collect_components(nexus, policy.repo)
    await emit(30, f"{len(components)} components")

    targets, undated = _select_for_deletion(components, policy)
    await emit(50, f"{len(targets)} to delete")

    deleted = 0
    failures: list[dict[str, str]] = []
    compact: dict[str, Any] = {}

    if not dry_run:
        deleted, failures = await _delete_targets(nexus, targets, emit)

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
    """Run every enabled policy still on the shared schedule.

    Excludes policies with their own ``interval_minutes`` set — those already
    run on their own cadence via ``_retention_interval_loop``/
    ``poll_due_policies``, so including them here (the daily
    ``RETENTION_RUN_AT`` sweep, and the manual "run all" action) would run
    them a second time, more often than their configured interval.
    """
    rows = (
        await session.execute(
            select(RetentionPolicy).where(
                RetentionPolicy.enabled.is_(True),
                RetentionPolicy.interval_minutes.is_(None),
            )
        )
    ).scalars().all()
    results = []
    for p in rows:
        try:
            results.append(await run_policy(nexus, session, p, dry_run=dry_run, on_progress=on_progress))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Retention policy %s failed", p.name)
            results.append({"repo": p.repo, "policy": p.name, "error": str(exc)})
    return results


def compute_next_run(policy: RetentionPolicy, *, after: datetime | None = None) -> datetime:
    """Next UTC run time for an interval-scheduled policy, strictly after ``after``.

    Only meaningful when ``interval_minutes`` is set — a policy left on the
    legacy shared schedule has no ``next_run_at`` of its own; see
    ``app.main._retention_scheduler``.
    """
    if not policy.interval_minutes or policy.interval_minutes <= 0:
        raise ValueError("interval_minutes must be a positive number of minutes")
    after = after or datetime.now(timezone.utc)
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    return after + timedelta(minutes=policy.interval_minutes)


async def poll_due_policies(cache, session: AsyncSession) -> list[str]:
    """Enqueue a ``run_retention`` job for every due, enabled, interval-scheduled policy.

    Policies without ``interval_minutes`` set are untouched here — they stay on
    the shared daily sweep. ``next_run_at`` is recomputed and persisted right
    after enqueuing, before the job itself has run, so a slow poll tick (or a
    job that outlives the poll interval) can't fire the same policy twice —
    the same reasoning as ``app.services.backup_schedule.poll_due_schedules``.
    """
    from ..core.jobs import JobQueue

    now = datetime.now(timezone.utc)
    rows = (await session.execute(
        select(RetentionPolicy).where(
            RetentionPolicy.enabled.is_(True),
            RetentionPolicy.interval_minutes.is_not(None),
            RetentionPolicy.next_run_at.is_not(None),
            RetentionPolicy.next_run_at <= now,
        )
    )).scalars().all()

    enqueued: list[str] = []
    queue = JobQueue(cache)
    for policy in rows:
        try:
            job_id = await queue.enqueue("run_retention", {"policy_id": policy.id})
        except Exception:  # noqa: BLE001 - one policy must not block the others
            logger.exception("Failed to enqueue interval retention run for policy %s", policy.id)
            continue
        enqueued.append(job_id)
        try:
            policy.next_run_at = compute_next_run(policy, after=now)
        except ValueError:
            logger.exception("Could not compute next run for policy %s; disabling its interval", policy.id)
            policy.interval_minutes = None
            policy.next_run_at = None

    if rows:
        await session.commit()
    return enqueued
