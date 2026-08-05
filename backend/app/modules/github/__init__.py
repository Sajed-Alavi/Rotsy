"""GitHub App integration: auth, repository discovery, webhook ingress.

Importing this package registers it with :mod:`app.core.integrations` — see
``main.py``, which imports every module package at startup for this side
effect before the routers that depend on the registry are wired up.
"""

from __future__ import annotations

from ...core.integrations import ModuleManifest, register_module

register_module(ModuleManifest(key="github", kind="source", display_name="GitHub"))
