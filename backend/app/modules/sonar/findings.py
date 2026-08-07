"""Normalize and persist Sonar issues/hotspots for one analysis run.

Kept separate from ``workers/analysis_worker.py`` for the same reason
``quality_gates.py`` is: the worker orchestrates the overall
clone -> scan -> poll -> collect flow, this module owns the
"turn Sonar's raw JSON into rows" and "replace this run's findings" details.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...models import SonarHotspot, SonarIssue
from .connector import SonarClient


def _parse_dt(raw: str | None) -> datetime | None:
    """Sonar timestamps look like ``2026-08-01T12:34:56+0000`` — ``fromisoformat``
    wants a colon in the UTC offset, which Sonar's format omits."""
    if not raw:
        return None
    try:
        if len(raw) >= 5 and raw[-5] in "+-" and ":" not in raw[-5:]:
            raw = f"{raw[:-2]}:{raw[-2:]}"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _strip_project_prefix(component: str, project_key: str) -> str:
    """Sonar's ``component`` is ``"<projectKey>:<path>"`` for a file-level
    finding, or bare ``project_key`` for a project-level one. Stripping the
    prefix matches what the file actually looks like in the repository —
    the project key is redundant once every row in the table already belongs
    to one run/project."""
    prefix = f"{project_key}:"
    return component[len(prefix):] if component.startswith(prefix) else component


def _issue_row(raw: dict[str, Any], project_key: str, analysis_run_id: int) -> SonarIssue:
    return SonarIssue(
        analysis_run_id=analysis_run_id,
        issue_key=raw.get("key", ""),
        rule=raw.get("rule", ""),
        severity=raw.get("severity", "INFO"),
        type=raw.get("type", "CODE_SMELL"),
        message=raw.get("message", ""),
        component=_strip_project_prefix(raw.get("component", ""), project_key),
        line=raw.get("line"),
        status=raw.get("status", ""),
        assignee=raw.get("assignee", ""),
        author=raw.get("author", ""),
        tags=raw.get("tags") or [],
        effort=raw.get("effort", ""),
        debt=raw.get("debt", ""),
        # Newer Sonar versions report Clean Code impacts instead of a single
        # attribute; take the first impact's software quality as the closest
        # equivalent when the legacy field is absent.
        clean_code_attribute=raw.get("cleanCodeAttribute")
        or (raw.get("impacts") or [{}])[0].get("softwareQuality"),
        creation_date=_parse_dt(raw.get("creationDate")),
        update_date=_parse_dt(raw.get("updateDate")),
    )


def _hotspot_row(raw: dict[str, Any], project_key: str, analysis_run_id: int) -> SonarHotspot:
    return SonarHotspot(
        analysis_run_id=analysis_run_id,
        hotspot_key=raw.get("key", ""),
        component=_strip_project_prefix(raw.get("component", ""), project_key),
        line=raw.get("line"),
        message=raw.get("message", ""),
        status=raw.get("status", ""),
        vulnerability_probability=raw.get("vulnerabilityProbability", ""),
        security_category=raw.get("securityCategory", ""),
        author=raw.get("author", ""),
        creation_date=_parse_dt(raw.get("creationDate")),
        update_date=_parse_dt(raw.get("updateDate")),
    )


async def sync_findings(
    session: AsyncSession, client: SonarClient, project_key: str, analysis_run_id: int,
) -> tuple[int, int]:
    """Fetch issues + hotspots for ``project_key`` and replace whatever this
    run previously had — same replace-on-rerun approach as
    :class:`~app.models.sonar.QualityGateResult` (a manual re-run of the same
    commit reuses the ``AnalysisRun`` row, so its findings must be replaced,
    not appended to). Returns ``(issue_count, hotspot_count)``.

    Caller commits — this only stages the delete + inserts, matching how the
    rest of ``handle_clone_and_analyze`` batches its writes into one commit
    per session block.
    """
    raw_issues = await client.issues(project_key)
    raw_hotspots = await client.hotspots(project_key)

    await session.execute(SonarIssue.__table__.delete().where(SonarIssue.analysis_run_id == analysis_run_id))
    await session.execute(SonarHotspot.__table__.delete().where(SonarHotspot.analysis_run_id == analysis_run_id))

    for raw in raw_issues:
        session.add(_issue_row(raw, project_key, analysis_run_id))
    for raw in raw_hotspots:
        session.add(_hotspot_row(raw, project_key, analysis_run_id))

    return len(raw_issues), len(raw_hotspots)
