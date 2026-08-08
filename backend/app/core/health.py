"""Deterministic Project Health Score.

One model, chosen and documented here rather than left to drift: a 0-100
**Health Score** where higher is better (100 = clean). Every factor below is
a fixed, published deduction — there is no hidden weighting and nothing here
calls out to an LLM or any external scoring service.

Current inputs (Sonar-only): across *every* repository connected to the
Project (a Project can hold many), each repository's latest successful
analysis run contributes its Quality Gate, bug/vulnerability counts,
coverage, and duplication — vulnerabilities and bugs are summed across
repositories, coverage and duplication are averaged, and the gate status
used is the worst one seen (a single failing repository's gate failure
isn't hidden by nine passing ones). Plus the project's most recent Smart
Insights. Trivy/Grype findings are not yet folded in — that requires the
Project-to-Nexus-artifact correlation from a later phase, which does not
exist yet. Adding it later means adding terms to :data:`SCORING_FACTORS`
and :func:`compute_health_score`, not redesigning the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AnalysisRun, Insight, QualityGateResult, SonarProject

# Published, fixed deductions — see the module docstring. Each is a cap, not
# a multiplier without bound, so one very bad analysis can't drive the score
# permanently negative or require clamping to hide an unbounded formula.
SCORING_FACTORS = {
    "quality_gate_error": -40,
    "quality_gate_warn": -15,
    "per_vulnerability": -5,
    "vulnerability_cap": -30,
    "per_bug": -2,
    "bug_cap": -20,
    "low_coverage_threshold_pct": 50.0,
    "low_coverage_penalty": -10,
    "high_duplication_threshold_pct": 20.0,
    "high_duplication_penalty": -10,
    "insight_critical": -10,
    "insight_high": -5,
    "insight_medium": -2,
    "insight_cap": -20,
    "recent_insights_considered": 5,
}


@dataclass
class HealthScore:
    score: int  # 0-100, higher is better
    factors: list[str] = field(default_factory=list)  # human-readable deductions actually applied
    has_data: bool = True  # False when there's no analysis yet to score


async def _sonar_projects_for(session: AsyncSession, project_id: int) -> list[SonarProject]:
    return (
        await session.execute(
            select(SonarProject).where(
                SonarProject.project_id == project_id,
                # A SonarProject with no linked repository has nothing valid
                # to score — shouldn't exist after the 20260811 migration,
                # but skip it defensively rather than crash on it.
                (SonarProject.github_repository_id.isnot(None)) | (SonarProject.gitlab_repository_id.isnot(None)),
            )
        )
    ).scalars().all()


async def _latest_successful_runs(session: AsyncSession, sonar_projects: list[SonarProject]) -> list[AnalysisRun]:
    """One latest-successful-run per repository — a Project can hold many."""
    runs: list[AnalysisRun] = []
    for sonar_project in sonar_projects:
        run = await session.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.sonar_project_id == sonar_project.id, AnalysisRun.status == "success")
            .order_by(desc(AnalysisRun.started_at))
            .limit(1)
        )
        if run is not None:
            runs.append(run)
    return runs


async def _gate_deduction(session: AsyncSession, latest_runs: list[AnalysisRun]) -> tuple[int, str | None]:
    """Worst gate status across repositories — one failing repo isn't hidden
    by others passing."""
    gate_statuses = []
    for run in latest_runs:
        gate = await session.scalar(select(QualityGateResult).where(QualityGateResult.analysis_run_id == run.id))
        if gate is not None:
            gate_statuses.append(gate.status)
    if "ERROR" in gate_statuses:
        deduction = SCORING_FACTORS["quality_gate_error"]
        failing = gate_statuses.count("ERROR")
        return deduction, f"Quality gate failed on {failing} repositor{'y' if failing == 1 else 'ies'} ({deduction})"
    if "WARN" in gate_statuses:
        deduction = SCORING_FACTORS["quality_gate_warn"]
        return deduction, f"Quality gate warning ({deduction})"
    return 0, None


def _vulnerability_deduction(latest_runs: list[AnalysisRun]) -> tuple[int, str | None]:
    total = sum(r.vulnerabilities or 0 for r in latest_runs)
    if not total:
        return 0, None
    deduction = max(SCORING_FACTORS["per_vulnerability"] * total, SCORING_FACTORS["vulnerability_cap"])
    return deduction, f"{total} vulnerabilit{'y' if total == 1 else 'ies'} across repositories ({deduction})"


def _bug_deduction(latest_runs: list[AnalysisRun]) -> tuple[int, str | None]:
    total = sum(r.bugs or 0 for r in latest_runs)
    if not total:
        return 0, None
    deduction = max(SCORING_FACTORS["per_bug"] * total, SCORING_FACTORS["bug_cap"])
    return deduction, f"{total} bug(s) across repositories ({deduction})"


def _coverage_deduction(latest_runs: list[AnalysisRun]) -> tuple[int, str | None]:
    coverages = [r.coverage for r in latest_runs if r.coverage is not None]
    avg = sum(coverages) / len(coverages) if coverages else None
    if avg is None or avg >= SCORING_FACTORS["low_coverage_threshold_pct"]:
        return 0, None
    deduction = SCORING_FACTORS["low_coverage_penalty"]
    return deduction, f"Average coverage below {SCORING_FACTORS['low_coverage_threshold_pct']:.0f}% ({deduction})"


def _duplication_deduction(latest_runs: list[AnalysisRun]) -> tuple[int, str | None]:
    duplications = [r.duplication_pct for r in latest_runs if r.duplication_pct is not None]
    avg = sum(duplications) / len(duplications) if duplications else None
    if avg is None or avg <= SCORING_FACTORS["high_duplication_threshold_pct"]:
        return 0, None
    deduction = SCORING_FACTORS["high_duplication_penalty"]
    return deduction, f"Average duplication above {SCORING_FACTORS['high_duplication_threshold_pct']:.0f}% ({deduction})"


async def _insight_deduction(session: AsyncSession, project_id: int) -> tuple[int, str | None]:
    recent_insights = (
        await session.execute(
            select(Insight)
            .where(Insight.project_id == project_id)
            .order_by(desc(Insight.created_at))
            .limit(SCORING_FACTORS["recent_insights_considered"])
        )
    ).scalars().all()
    per_severity = {
        "CRITICAL": SCORING_FACTORS["insight_critical"],
        "HIGH": SCORING_FACTORS["insight_high"],
        "MEDIUM": SCORING_FACTORS["insight_medium"],
    }
    deduction = sum(per_severity.get(insight.severity, 0) for insight in recent_insights)
    deduction = max(deduction, SCORING_FACTORS["insight_cap"])
    if not deduction:
        return 0, None
    return deduction, f"Recent insights ({deduction})"


async def compute_health_score(session: AsyncSession, project_id: int) -> HealthScore:
    sonar_projects = await _sonar_projects_for(session, project_id)
    if not sonar_projects:
        return HealthScore(score=0, factors=["No SonarQube project connected yet."], has_data=False)

    latest_runs = await _latest_successful_runs(session, sonar_projects)
    if not latest_runs:
        return HealthScore(score=0, factors=["No successful analysis yet."], has_data=False)

    score = 100
    factors: list[str] = []

    deductions = [
        await _gate_deduction(session, latest_runs),
        _vulnerability_deduction(latest_runs),
        _bug_deduction(latest_runs),
        _coverage_deduction(latest_runs),
        _duplication_deduction(latest_runs),
        await _insight_deduction(session, project_id),
    ]
    for deduction, message in deductions:
        if message:
            score += deduction
            factors.append(message)

    score = max(0, min(100, score))
    if not factors:
        factors.append("No deductions — clean quality gate and no recent negative insights.")
    return HealthScore(score=score, factors=factors, has_data=True)
