"""Vendor-agnostic contract every container-registry backend implements.

The direct counterpart of :mod:`app.core.source_provider`, which already lets
GitHub and GitLab be interchangeable behind one Protocol. Registries never got
the same treatment: Nexus was the only one, so ``modules/nexus/registry.py``
*is* the registry layer, and code that wants "where do images come from"
imports it by name.

That is the gap between what Rotsy claims to be — a console for container and
code security — and what it supports. Adding Harbor, GHCR or ECR today would
mean finding every ``from ..modules.nexus import registry`` and teaching it
about a second shape.

Defining the contract does not by itself add a backend, and this deliberately
does not invent one. What it does is fix the *shape* a second backend has to
fit, in the layer that is allowed to be depended upon, so the work is additive
rather than a refactor of everything that touches images.

**Static analysis only.** Nothing on this Protocol runs, starts or executes an
image; every method reads metadata or returns a reference for a scanner to
read over the registry API. That invariant is enforced elsewhere
(``modules/nexus/base.py::assert_static_ref``) and no implementation here may
weaken it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class RegistryEndpoint:
    """A resolved registry endpoint an image can be addressed through.

    Mirrors what ``modules/nexus/registry.DockerRegistry`` already exposes, so
    the existing Nexus discovery satisfies this without changing its own
    dataclass — an adapter can wrap or subclass it.
    """

    #: Repository/namespace this endpoint serves, in the backend's own terms.
    repo: str
    host: str
    port: int
    scheme: str  # http | https
    #: Which discovery step or configuration produced this endpoint — kept
    #: because "why do we think the registry is here" is the first question
    #: when a pull fails, and it is not reconstructable after the fact.
    source: str

    @property
    def authority(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.authority}"

    @property
    def is_plaintext(self) -> bool:
        return self.scheme == "http"


@dataclass(frozen=True)
class ImageRef:
    """One image, as the registry names it."""

    #: Pullable reference a scanner reads, e.g. ``host:port/name:tag``.
    reference: str
    name: str
    tag: str
    digest: str | None = None


class ImageRegistry(Protocol):
    """Implemented per backend under ``modules/<backend>/``.

    Satisfied today by the Nexus module. A second backend implements these
    methods and registers itself; nothing in ``core/`` or ``services/``
    changes.
    """

    #: Stable identifier — ``"nexus"``, ``"harbor"``, ``"ghcr"``.
    name: str

    async def list_repositories(self) -> list[str]:
        """Every repository this backend exposes, by name."""
        ...

    async def resolve_endpoint(self, repo: str) -> RegistryEndpoint | None:
        """Where ``repo``'s registry API lives, or ``None`` if it cannot be
        resolved. Discovered from the backend rather than configured by hand —
        see the zero-registry-configuration rule in AGENTS.md."""
        ...

    async def list_images(self, repo: str) -> list[ImageRef]:
        """Every image/tag in ``repo``. Metadata only; nothing is pulled."""
        ...

    async def probe(self, endpoint: RegistryEndpoint) -> dict[str, Any]:
        """Reachability of ``endpoint`` — whether a scan could read from it,
        and if not, why. Shown as ``reachable``/*Not scannable* in Settings."""
        ...
