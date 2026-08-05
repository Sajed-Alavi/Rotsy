"""Module registration registry.

Core knows *that* a module exists and what kind of capability it provides —
never its vendor-specific implementation. A module (``app/modules/github``,
``app/modules/sonar``, ...) registers one :class:`ModuleManifest` at import
time via :func:`register_module`; core code (the integrations router, the
Smart Insights engine) looks modules up by ``module_key`` through this
registry instead of importing the module directly.

Adding a new integration (Harbor, Snyk, Bitbucket, ...) means writing a new
``app/modules/<name>/`` package that calls :func:`register_module` — no
change to this file or to any other core module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

IntegrationKind = Literal["source", "analysis_engine", "artifact_registry"]


@dataclass(frozen=True)
class ModuleManifest:
    key: str          # "github" | "gitlab" | "sonar" | "artifact_registry" | ...
    kind: IntegrationKind
    display_name: str


_REGISTRY: dict[str, ModuleManifest] = {}


def register_module(manifest: ModuleManifest) -> None:
    """Register a module's manifest. Idempotent re-registration is an error —
    it almost always means two modules picked the same ``key`` by accident."""
    existing = _REGISTRY.get(manifest.key)
    if existing is not None and existing is not manifest:
        raise ValueError(f"module key {manifest.key!r} is already registered")
    _REGISTRY[manifest.key] = manifest


def get_module(key: str) -> ModuleManifest | None:
    return _REGISTRY.get(key)


def list_modules() -> list[ModuleManifest]:
    return sorted(_REGISTRY.values(), key=lambda m: m.key)


def is_registered(key: str) -> bool:
    return key in _REGISTRY
