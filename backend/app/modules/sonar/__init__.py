"""SonarQube as an embedded analysis engine: connector, scanner execution,
quality gate polling. The user never operates the Sonar UI directly.
"""

from __future__ import annotations

from ...core.integrations import ModuleManifest, register_module

register_module(ModuleManifest(key="sonar", kind="analysis_engine", display_name="SonarQube"))
