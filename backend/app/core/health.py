"""Deterministic Project Health Score.

One model, chosen and documented here rather than left to drift: a 0-100
**Health Score** where higher is better (100 = clean). Every factor below is
a fixed, published deduction — there is no hidden weighting and nothing here
calls out to an LLM or any external scoring service.

Current inputs (Sonar-only): the latest successful analysis run's Quality
Gate, bug/vulnerability counts, coverage, and duplication, plus the
project's most recent Smart Insights. Trivy/Grype findings are not yet
folded in — that requires the Project-to-Nexus-artifact correlation from a
later phase, which does not exist yet. Adding it later means adding terms to
:data:`SCORING_FACTORS` and :func:`compute_health_score`, not redesigning
the model.
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


async def compute_health_score(session: AsyncSession, project_id: int) -> HealthScore:
    sonar_project = await session.scalar(select(SonarProject).where(SonarProject.project_id == project_id))
    if sonar_project is None:
        return HealthScore(score=0, factors=["No SonarQube project connected yet."], has_data=False)

    latest_run = await session.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.sonar_project_id == sonar_project.id, AnalysisRun.status == "success")
        .order_by(desc(AnalysisRun.started_at))
        .limit(1)
    )
    if latest_run is None:
        return HealthScore(score=0, factors=["No successful analysis yet."], has_data=False)

    score = 100
    factors: list[str] = []

    gate = await session.scalar(
        select(QualityGateResult).where(QualityGateResult.analysis_run_id == latest_run.id)
    )
    if gate is not None and gate.status == "ERROR":
        score += SCORING_FACTORS["quality_gate_error"]
        factors.append(f"Quality gate failed ({SCORING_FACTORS['quality_gate_error']})")
    elif gate is not None and gate.status == "WARN":
        score += SCORING_FACTORS["quality_gate_warn"]
        factors.append(f"Quality gate warning ({SCORING_FACTORS['quality_gate_warn']})")

    if latest_run.vulnerabilities:
        deduction = max(
            SCORING_FACTORS["per_vulnerability"] * latest_run.vulnerabilities,
            SCORING_FACTORS["vulnerability_cap"],
        )
        score += deduction
        factors.append(f"{latest_run.vulnerabilities} vulnerabilit{'y' if latest_run.vulnerabilities == 1 else 'ies'} ({deduction})")

    if latest_run.bugs:
        deduction = max(SCORING_FACTORS["per_bug"] * latest_run.bugs, SCORING_FACTORS["bug_cap"])
        score += deduction
        factors.append(f"{latest_run.bugs} bug(s) ({deduction})")

    if latest_run.coverage is not None and latest_run.coverage < SCORING_FACTORS["low_coverage_threshold_pct"]:
        score += SCORING_FACTORS["low_coverage_penalty"]
        factors.append(f"Coverage below {SCORING_FACTORS['low_coverage_threshold_pct']:.0f}% ({SCORING_FACTORS['low_coverage_penalty']})")

    if latest_run.duplication_pct is not None and latest_run.duplication_pct > SCORING_FACTORS["high_duplication_threshold_pct"]:
        score += SCORING_FACTORS["high_duplication_penalty"]
        factors.append(f"Duplication above {SCORING_FACTORS['high_duplication_threshold_pct']:.0f}% ({SCORING_FACTORS['high_duplication_penalty']})")

    recent_insights = (
        await session.execute(
            select(Insight)
            .where(Insight.project_id == project_id)
            .order_by(desc(Insight.created_at))
            .limit(SCORING_FACTORS["recent_insights_considered"])
        )
    ).scalars().all()
    insight_deduction = 0
    for insight in recent_insights:
        if insight.severity == "CRITICAL":
            insight_deduction += SCORING_FACTORS["insight_critical"]
        elif insight.severity == "HIGH":
            insight_deduction += SCORING_FACTORS["insight_high"]
        elif insight.severity == "MEDIUM":
            insight_deduction += SCORING_FACTORS["insight_medium"]
    insight_deduction = max(insight_deduction, SCORING_FACTORS["insight_cap"])
    if insight_deduction:
        score += insight_deduction
        factors.append(f"Recent insights ({insight_deduction})")

    score = max(0, min(100, score))
    if not factors:
        factors.append("No deductions — clean quality gate and no recent negative insights.")
    return HealthScore(score=score, factors=factors, has_data=True)
