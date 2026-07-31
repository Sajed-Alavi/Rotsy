"""Vulnerability scanning endpoints.

Scans are **event-driven only** — see :mod:`app.services.scanning.events`. There
is no "scan everything" endpoint: the previous ``POST /scan/scan-all`` fanned a
job out for every image in every enabled repository, which is exactly the
behaviour this system must not have. An image is scanned when it is pushed
(``POST /scan/events/nexus``, or the new-image watcher) or when an operator asks
(``POST /scan/image``).

Registry endpoints are discovered from Nexus, never configured:
``GET /scan/registry`` shows what discovery found and whether each endpoint
answers.

This is a package rather than one module: the endpoints fall into six unrelated
groups and used to sit in one 698-line file behind comment banners. Each group
now has its own module and its own ``APIRouter``, composed below into the single
``router`` that ``app.main`` mounts — routes, paths and the OpenAPI tag are
unchanged. Request/response models live in :mod:`app.schemas.scan`, like every
other feature's.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import events, images, registry, reports, scanner_db, targets

router = APIRouter(prefix="/scan", tags=["scan"])

router.include_router(targets.router)
router.include_router(images.router)
router.include_router(events.router)
router.include_router(registry.router)
router.include_router(scanner_db.router)
router.include_router(reports.router)

__all__ = ["router"]
