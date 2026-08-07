"""Importing this package imports every module package for its registration
side effect (see each module's ``__init__.py``, which calls
:func:`app.core.integrations.register_module`).

Adding a new module (Harbor, Snyk, GitLab, ...) means adding one import line
here — nothing else in ``core`` changes.
"""

from __future__ import annotations

from . import github, gitlab, nexus, sonar  # noqa: F401
