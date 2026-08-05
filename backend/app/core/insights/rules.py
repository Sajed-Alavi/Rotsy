"""Deterministic Smart Insights rules — v1, no LLM.

Each rule is a pure function: ``RuleContext`` in, an insight dict or ``None``
out. No side effects, no I/O — independently unit-testable and safe to run
in any order. ``core/insights/engine.py`` is the only thing that persists
what these return.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class RuleContext:
    project_id: int
    commit_sha: str
    issues_count: int | None
    coverage: float | None
    duplication_pct: float | None
    quality_gate_status: str  # "OK" | "ERROR" | "WARN"
    previous_issues_count: int | None
    previous_coverage: float | None
    previous_duplication_pct: float | None
    previous_quality_gate_status: str | None


class InsightRule(Protocol):
    kind: str

    def evaluate(self, ctx: RuleContext) -> dict[str, Any] | None: ...


def _insight(kind: str, severity: str, title: str, evidence: dict, ctx: RuleContext) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "related_commit_sha": ctx.commit_sha,
        "related_source": "sonar",
    }


class QualityGateFailed:
    kind = "quality_gate_failed"

    def evaluate(self, ctx: RuleContext) -> dict[str, Any] | None:
        if ctx.quality_gate_status == "OK":
            return None
        return _insight(
            self.kind, "HIGH",
            f"Quality gate {ctx.quality_gate_status.lower()} on commit {ctx.commit_sha[:8]}",
            {"quality_gate_status": ctx.quality_gate_status}, ctx,
        )


class ScanResultRegressed:
    """Was passing, now isn't — distinct from QualityGateFailed, which fires
    on every failing run regardless of history; this fires only on the
    transition, so it reads as "this got worse", not "this is still broken"."""

    kind = "scan_result_changed_from_pass"

    def evaluate(self, ctx: RuleContext) -> dict[str, Any] | None:
        if ctx.previous_quality_gate_status != "OK":
            return None
        if ctx.quality_gate_status == "OK":
            return None
        return _insight(
            self.kind, "HIGH",
            f"Quality gate regressed to {ctx.quality_gate_status.lower()} on commit {ctx.commit_sha[:8]}",
            {"previous_status": ctx.previous_quality_gate_status, "current_status": ctx.quality_gate_status},
            ctx,
        )


class NewIssuesIntroduced:
    kind = "new_issues_introduced"

    def evaluate(self, ctx: RuleContext) -> dict[str, Any] | None:
        if ctx.issues_count is None or ctx.previous_issues_count is None:
            return None
        delta = ctx.issues_count - ctx.previous_issues_count
        if delta <= 0:
            return None
        severity = "HIGH" if delta >= 5 else "MEDIUM"
        return _insight(
            self.kind, severity,
            f"{delta} new issue(s) introduced by commit {ctx.commit_sha[:8]}",
            {"issues_count": ctx.issues_count, "previous_issues_count": ctx.previous_issues_count, "delta": delta},
            ctx,
        )


class CoverageDropped:
    kind = "coverage_dropped"
    _THRESHOLD_PCT_POINTS = 5.0

    def evaluate(self, ctx: RuleContext) -> dict[str, Any] | None:
        if ctx.coverage is None or ctx.previous_coverage is None:
            return None
        drop = ctx.previous_coverage - ctx.coverage
        if drop <= self._THRESHOLD_PCT_POINTS:
            return None
        return _insight(
            self.kind, "MEDIUM",
            f"Coverage dropped {drop:.1f} points on commit {ctx.commit_sha[:8]}",
            {"coverage": ctx.coverage, "previous_coverage": ctx.previous_coverage, "drop": drop},
            ctx,
        )


class DuplicationIncreased:
    kind = "duplication_increased"
    _THRESHOLD_PCT_POINTS = 3.0

    def evaluate(self, ctx: RuleContext) -> dict[str, Any] | None:
        if ctx.duplication_pct is None or ctx.previous_duplication_pct is None:
            return None
        increase = ctx.duplication_pct - ctx.previous_duplication_pct
        if increase <= self._THRESHOLD_PCT_POINTS:
            return None
        return _insight(
            self.kind, "LOW",
            f"Duplication increased {increase:.1f} points on commit {ctx.commit_sha[:8]}",
            {"duplication_pct": ctx.duplication_pct, "previous_duplication_pct": ctx.previous_duplication_pct,
             "increase": increase},
            ctx,
        )


# Order matters only for display/log readability — each rule is independent.
DEFAULT_RULES: list[InsightRule] = [
    QualityGateFailed(),
    ScanResultRegressed(),
    NewIssuesIntroduced(),
    CoverageDropped(),
    DuplicationIncreased(),
]
