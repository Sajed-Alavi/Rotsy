"""Smart Insights — deterministic rule engine over analysis results.

Lives in ``core`` per the architecture decision: insights correlate data
that originates in modules (Sonar today, Nexus/GitLab later) but the engine
itself must not import any module, so a new analysis engine only has to
produce an ``AnalysisRun`` row shaped like the existing one to get insights
for free.
"""

from __future__ import annotations

from .engine import evaluate_and_store
from .rules import DEFAULT_RULES, RuleContext

__all__ = ["evaluate_and_store", "RuleContext", "DEFAULT_RULES"]
