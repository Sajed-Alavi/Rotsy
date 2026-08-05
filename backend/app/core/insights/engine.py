"""Smart Insights engine — runs every rule against one analysis run and
persists whatever fires.

Called synchronously at the end of ``workers/analysis_worker.py`` for v1.
``core`` here depends only on plain values (``RuleContext``) and the
``Insight``/``AnalysisRun``/``QualityGateResult`` models, never on
``modules.sonar`` types directly — the same engine runs unchanged once a
second analysis engine or GitLab exists, because both would still produce an
``AnalysisRun`` row shaped the same way.
"""

from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import AnalysisRun, Insight, QualityGateResult
from .rules import DEFAULT_RULES, RuleContext


async def _previous_run(session: AsyncSession, sonar_project_id: int, before_run_id: int) -> AnalysisRun | None:
    return await session.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.sonar_project_id == sonar_project_id,
            AnalysisRun.id != before_run_id,
            AnalysisRun.status == "success",
        )
        .order_by(desc(AnalysisRun.started_at))
        .limit(1)
    )


async def evaluate_and_store(session: AsyncSession, project_id: int, run: AnalysisRun, gate_status: str) -> list[Insight]:
    """Run every registered rule against ``run`` and persist the insights that fire.

    ``run`` must already be committed (has an id) and its sonar_project_id
    set — this reads the previous successful run for the same Sonar project
    to build the before/after comparison every rule needs.
    """
    previous = await _previous_run(session, run.sonar_project_id, run.id)
    previous_gate = None
    if previous is not None:
        previous_gate_row = await session.scalar(
            select(QualityGateResult).where(QualityGateResult.analysis_run_id == previous.id)
        )
        previous_gate = previous_gate_row.status if previous_gate_row else None

    ctx = RuleContext(
        project_id=project_id,
        commit_sha=run.commit_sha,
        issues_count=run.issues_count,
        coverage=run.coverage,
        duplication_pct=run.duplication_pct,
        quality_gate_status=gate_status,
        previous_issues_count=previous.issues_count if previous else None,
        previous_coverage=previous.coverage if previous else None,
        previous_duplication_pct=previous.duplication_pct if previous else None,
        previous_quality_gate_status=previous_gate,
    )

    created: list[Insight] = []
    for rule in DEFAULT_RULES:
        result = rule.evaluate(ctx)
        if result is None:
            continue
        insight = Insight(project_id=project_id, **result)
        session.add(insight)
        created.append(insight)

    if created:
        await session.commit()
        for insight in created:
            await session.refresh(insight)
    return created
