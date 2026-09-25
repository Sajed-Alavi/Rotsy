"""Turning domain events into notifications.

The only place that decides an event is worth telling somebody about, and what
the message says. Producers publish facts; channels render and deliver; the
translation between the two lives here — so adding a notification, rewording
one, or removing it entirely touches this file and no job handler.

Wording is plain text with no channel markup (see ``message.py``).
"""

from __future__ import annotations

import logging

from .message import Attachment, Audience, Notification, Severity
from .ports import events
from .service import dispatch

logger = logging.getLogger(__name__)

#: Attachment kind for an analysis report. The renderer for it is registered
#: by whoever owns that data — see ``renderers.py``; this package never learns
#: how a report is built.
ANALYSIS_REPORT = "analysis_report"


async def on_analysis_completed(event: events.AnalysisCompleted) -> None:
    coverage = f"{event.coverage:.0f}%" if event.coverage is not None else "n/a"
    await dispatch(Notification(
        title=f"Analysis complete — {event.repo_name} ({event.ref})",
        audience=Audience.project(event.project_id),
        severity=Severity.SUCCESS if event.quality_gate == "OK" else Severity.WARNING,
        fields=(
            ("Quality gate", event.quality_gate),
            ("Issues", f"{event.issues_count} (bugs {event.bugs}, "
                       f"vulnerabilities {event.vulnerabilities}, code smells {event.code_smells})"),
            ("Coverage", coverage),
        ),
        attachment=Attachment(
            filename=f"sonar-{event.commit_sha[:8]}.pdf",
            media_type="application/pdf",
            kind=ANALYSIS_REPORT,
            params={
                "analysis_run_id": event.analysis_run_id,
                "sonar_project_id": event.sonar_project_id,
            },
        ),
        context={
            "analysis_run_id": event.analysis_run_id,
            "project_id": event.project_id,
            "quality_gate": event.quality_gate,
        },
    ))


async def on_analysis_failed(event: events.AnalysisFailed) -> None:
    await dispatch(Notification(
        title=f"Analysis failed — {event.repo_name} ({event.ref})",
        audience=Audience.project(event.project_id),
        severity=Severity.ERROR,
        body=event.error[:500],
        context={"project_id": event.project_id},
    ))


async def on_backup_failed(event: events.BackupFailed) -> None:
    await dispatch(Notification(
        title=f"Backup failed ({event.mode})",
        audience=Audience.administrators(),
        severity=Severity.ERROR,
        body=event.error[:500],
    ))


async def on_scanner_database_update_failed(event: events.ScannerDatabaseUpdateFailed) -> None:
    await dispatch(Notification(
        title="Scanner database update failed",
        audience=Audience.administrators(),
        severity=Severity.ERROR,
        body=event.error[:500],
        fields=(("Scanners", ", ".join(event.scanners)),),
    ))


def register() -> None:
    """Subscribe every handler above. Idempotent — see ``events.subscribe``."""
    events.subscribe(events.AnalysisCompleted, on_analysis_completed)
    events.subscribe(events.AnalysisFailed, on_analysis_failed)
    events.subscribe(events.BackupFailed, on_backup_failed)
    events.subscribe(events.ScannerDatabaseUpdateFailed, on_scanner_database_update_failed)
