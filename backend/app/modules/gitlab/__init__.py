"""GitLab integration: user-level (PAT) and repository-level connections,
repository discovery, webhook ingress.

Importing this package registers it with :mod:`app.core.integrations` — see
``app/modules/__init__.py``, imported once at startup for this side effect.
"""

from __future__ import annotations

from ...core.integrations import ModuleManifest, register_module

register_module(ModuleManifest(key="gitlab", kind="source", display_name="GitLab"))
