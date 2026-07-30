"""Scan triggering — strictly event-driven.

A scan happens for exactly two reasons:

**a) An image was pushed.** Either Nexus told us so through a repository
webhook (:func:`ingest_push_event`, instant and the preferred path), or the
new-image watcher noticed an image in a repository that is not in the ledger
(:func:`observe_target`, the fallback for deployments without webhooks).

**b) An operator asked.** :func:`request_manual_scan`, behind the per-image
Scan button.

Nothing else scans. In particular:

  * Starting the service scans nothing. The first time a repository is observed
    its existing contents are recorded as *baseline* — history, deliberately
    unscanned — and :attr:`ScanTarget.baseline_at` is stamped. Going from 7
    projects to 12 adds five baselines, not five repositories' worth of scans.
  * Nothing is ever re-scanned implicitly. The ledger lives in Postgres, so a
    restart, a cache flush or a redeploy cannot resurrect work. The previous
    implementation deduplicated in Redis with a 24-hour TTL, which silently
    re-scanned every image in every enabled repository once a day.
  * A tag re-pushed with new content *is* a new push: the manifest digest
    changes, and that is what the ledger compares.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.cache import Cache
from ..core.jobs import JobQueue
from ..core.nexus_client import NexusClient
from ..models import ScannedImage, ScanTarget

logger = logging.getLogger(__name__)

# Nexus webhook event ids we act on, and the component actions worth scanning.
WEBHOOK_EVENT_ID = "rm:repository:component"
_SCANNABLE_ACTIONS = {"CREATED", "UPDATED"}


@dataclass(frozen=True)
class ImageRef:
    """A Docker image in a Nexus repository."""

    image: str  # name:tag
    digest: str = ""  # manifest digest when Nexus reports one


# ---------------------------------------------------------------------------
# Reading what is in a repository (metadata only — no image content)
# ---------------------------------------------------------------------------
def _digest_of(component: dict[str, Any]) -> str:
    """Manifest digest of a Docker component, or "" when unavailable.

    Nexus stores a Docker tag's manifest as an asset under
    ``v2/{name}/manifests/{tag}``; its sha256 checksum is the manifest digest,
    which is what changes when a tag is re-pushed with new content.
    """
    for asset in component.get("assets") or []:
        path = asset.get("path") or ""
        if "/manifests/" in path:
            checksum = (asset.get("checksum") or {}).get("sha256")
            if checksum:
                return f"sha256:{checksum}"
    return ""


async def list_repo_images(nexus: NexusClient, repo: str) -> list[ImageRef]:
    """List every Docker image currently in ``repo``.

    This reads component *metadata* only — names, tags and checksums. No layer
    is downloaded and no image is analysed; that only happens when a scan is
    actually triggered.

    Paginates properly: the previous implementation read a single unpaginated
    page, so anything past the first page of components was invisible to it.
    """
    images: list[ImageRef] = []
    seen: set[str] = set()
    async for component in nexus.paginate("/service/rest/v1/components", params={"repository": repo}):
        if (component.get("format") or "").lower() != "docker":
            continue
        name, tag = component.get("name"), component.get("version")
        if not name or not tag:
            continue
        ref = f"{name}:{tag}"
        if ref in seen:
            continue
        seen.add(ref)
        images.append(ImageRef(ref, _digest_of(component)))
    return images


# ---------------------------------------------------------------------------
# Ledger operations
# ---------------------------------------------------------------------------
async def _ledger_entry(session: AsyncSession, repo: str, image: str) -> ScannedImage | None:
    return await session.scalar(
        select(ScannedImage).where(ScannedImage.repo == repo, ScannedImage.image == image)
    )


async def adopt_baseline(session: AsyncSession, target: ScanTarget, images: Iterable[ImageRef]) -> int:
    """Record a repository's existing images as history and stamp the baseline.

    Returns the number of images adopted. Adopted images are never auto-scanned:
    enabling scanning on a repository that already holds a thousand images must
    not kick off a thousand scans. The operator can still scan any of them
    individually with the Scan button.
    """
    # One query for what is already recorded, rather than one per image: a
    # long-established repository can hold thousands.
    known = set((await session.scalars(
        select(ScannedImage.image).where(ScannedImage.repo == target.repo)
    )).all())

    adopted = 0
    for ref in images:
        if ref.image not in known:
            session.add(ScannedImage(
                repo=target.repo, image=ref.image, digest=ref.digest,
                state="baseline", source="baseline",
            ))
            adopted += 1
    target.baseline_at = datetime.now(timezone.utc)
    await session.commit()
    logger.info(
        "Baselined repository '%s': %d existing image(s) recorded as history (not scanned)",
        target.repo, adopted,
    )
    return adopted


def _scanners_for(target: ScanTarget, default: list[str]) -> list[str]:
    configured = [s.strip().lower() for s in (target.scanners or "").split(",") if s.strip()]
    return configured or default


async def _enqueue_scan(
    cache: Cache,
    session: AsyncSession,
    entry: ScannedImage,
    scanners: list[str],
    source: str,
) -> str:
    """Queue a scan for a ledger entry and mark it in flight."""
    job_id = await JobQueue(cache).enqueue("scan_image", {
        "repo": entry.repo,
        "image": entry.image,
        "scanners": scanners,
        "ledger_id": entry.id,
        "trigger": source,
    })
    entry.state = "queued"
    entry.last_job_id = job_id
    await session.commit()
    logger.info("Scan queued for %s/%s (%s trigger): job %s", entry.repo, entry.image, source, job_id)
    return job_id


# ---------------------------------------------------------------------------
# Trigger (a): a push happened
# ---------------------------------------------------------------------------
async def ingest_push_event(
    session: AsyncSession,
    cache: Cache,
    repo: str,
    ref: ImageRef,
    *,
    source: str,
    default_scanners: list[str],
) -> dict[str, Any]:
    """Handle one pushed image. Idempotent, and safe to call repeatedly.

    Scans when the image is new, or when a known tag's digest changed (a genuine
    re-push). Declines when the repository is not an enabled target, when
    ``auto_scan`` is off, before the repository has a baseline, or when we have
    already seen this exact content.
    """
    target = await session.scalar(select(ScanTarget).where(ScanTarget.repo == repo))
    if target is None or not target.enabled:
        return {"scanned": False, "reason": f"repository '{repo}' is not an enabled scan target"}
    if not target.auto_scan:
        return {"scanned": False, "reason": f"auto-scan is disabled for '{repo}'"}
    if target.baseline_at is None:
        # No baseline yet means we have never observed this repository, so we
        # cannot tell a new push from pre-existing history. Let the watcher
        # baseline it first; the next push is scanned.
        return {"scanned": False, "reason": f"'{repo}' has no baseline yet — it will be adopted shortly"}

    entry = await _ledger_entry(session, repo, ref.image)
    if entry is None:
        entry = ScannedImage(repo=repo, image=ref.image, digest=ref.digest,
                             state="queued", source=source)
        session.add(entry)
        await session.flush()
    elif entry.state == "queued":
        return {"scanned": False, "reason": "a scan for this image is already queued"}
    elif ref.digest and entry.digest and ref.digest != entry.digest:
        # Same tag, new content — treat as a fresh push.
        entry.digest = ref.digest
        entry.source = source
    elif entry.state in ("scanned", "failed", "baseline"):
        return {"scanned": False, "reason": f"already known ({entry.state}); not re-scanning implicitly"}

    job_id = await _enqueue_scan(cache, session, entry, _scanners_for(target, default_scanners), source)
    return {"scanned": True, "job_id": job_id, "image": ref.image, "repo": repo}


def verify_webhook_signature(secret: str, body: bytes, signature: str) -> bool:
    """Verify a Nexus webhook's ``X-Nexus-Webhook-Signature`` header.

    Nexus signs the raw request body with HMAC-SHA1 (hex). SHA-256 is accepted
    as well so a future Nexus release that upgrades the digest keeps working.
    """
    if not secret or not signature:
        return False
    candidate = signature.strip().lower()
    for algorithm in (hashlib.sha1, hashlib.sha256):
        expected = hmac.new(secret.encode(), body, algorithm).hexdigest()
        if hmac.compare_digest(expected, candidate):
            return True
    return False


def parse_webhook_payload(payload: dict[str, Any]) -> tuple[str, ImageRef] | None:
    """Extract ``(repo, image)`` from a Nexus component webhook payload.

    Returns None for anything not worth scanning: non-Docker formats, deletions,
    or payloads without a name and version.
    """
    action = (payload.get("action") or "").upper()
    if action not in _SCANNABLE_ACTIONS:
        return None
    repo = payload.get("repositoryName")
    component = payload.get("component") or {}
    if (component.get("format") or "").lower() != "docker":
        return None
    name, tag = component.get("name"), component.get("version")
    if not repo or not name or not tag:
        return None
    return repo, ImageRef(f"{name}:{tag}")


# ---------------------------------------------------------------------------
# Trigger (a), fallback: notice new images without a webhook
# ---------------------------------------------------------------------------
async def observe_target(
    nexus: NexusClient,
    session: AsyncSession,
    cache: Cache,
    target: ScanTarget,
    default_scanners: list[str],
) -> dict[str, Any]:
    """Look for images in ``target`` the ledger has never seen, or whose content changed.

    This is a *metadata* comparison, not a scan sweep: it lists what is in the
    repository and scans only what is genuinely new — an unseen ``name:tag``, or
    a known tag whose manifest digest changed (someone re-pushed it). On a
    repository without a baseline it adopts one and scans nothing at all.

    Prefer webhooks where they are available — this path costs one component
    listing per repository per poll and only notices a push on the next poll.
    """
    images = await list_repo_images(nexus, target.repo)
    if target.baseline_at is None:
        adopted = await adopt_baseline(session, target, images)
        return {"repo": target.repo, "baselined": adopted, "queued": 0}

    known = {
        entry.image: entry for entry in (await session.scalars(
            select(ScannedImage).where(ScannedImage.repo == target.repo)
        )).all()
    }

    queued: list[str] = []
    scanners = _scanners_for(target, default_scanners)
    for ref in images:
        entry = known.get(ref.image)
        if entry is None:
            entry = ScannedImage(repo=target.repo, image=ref.image, digest=ref.digest,
                                 state="queued", source="push")
            session.add(entry)
            await session.flush()
        elif entry.state == "queued":
            continue  # a scan is already in flight for this image
        elif ref.digest and entry.digest and ref.digest != entry.digest:
            # Same tag, different content: someone re-pushed. That is a new push,
            # and it is the only thing that makes a known image scannable again
            # without an explicit request.
            entry.digest = ref.digest
            entry.source = "push"
        elif not entry.digest and ref.digest:
            # Backfill a digest we did not have (e.g. a row created before the
            # manifest was readable) without treating it as a push.
            entry.digest = ref.digest
            continue
        else:
            continue
        queued.append(await _enqueue_scan(cache, session, entry, scanners, "push"))
    if not queued:
        await session.commit()  # persist any digest backfills
    return {"repo": target.repo, "baselined": 0, "queued": len(queued), "job_ids": queued}


# ---------------------------------------------------------------------------
# Trigger (b): an operator asked
# ---------------------------------------------------------------------------
async def request_manual_scan(
    session: AsyncSession,
    cache: Cache,
    repo: str,
    image: str,
    *,
    scanners: list[str] | None,
    default_scanners: list[str],
) -> dict[str, Any]:
    """Queue a scan because an operator clicked Scan.

    Always runs, whatever the ledger says — an explicit request is the one thing
    that may re-scan an already-scanned or baselined image. Creates the ledger
    entry if the image has never been seen (e.g. a repository with no baseline).
    """
    target = await session.scalar(select(ScanTarget).where(ScanTarget.repo == repo))
    chosen = [s.strip().lower() for s in (scanners or []) if s.strip()]
    if not chosen:
        chosen = _scanners_for(target, default_scanners) if target else default_scanners

    entry = await _ledger_entry(session, repo, image)
    if entry is None:
        entry = ScannedImage(repo=repo, image=image, state="queued", source="manual")
        session.add(entry)
        await session.flush()
    else:
        entry.source = "manual"

    job_id = await _enqueue_scan(cache, session, entry, chosen, "manual")
    return {"job_id": job_id, "repo": repo, "image": image, "scanners": chosen}


async def record_scan_outcome(
    session: AsyncSession, ledger_id: int | None, repo: str, image: str, ok: bool,
) -> None:
    """Close the loop on the ledger after a scan job finishes."""
    entry = await session.get(ScannedImage, ledger_id) if ledger_id else None
    if entry is None:
        entry = await _ledger_entry(session, repo, image)
    if entry is None:
        return
    entry.state = "scanned" if ok else "failed"
    entry.last_scan_at = datetime.now(timezone.utc)
    entry.scan_count += 1
    await session.commit()
