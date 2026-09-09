"""Turning domain events into notifications.

This module is the only place that decides a given event is worth telling
somebody about, and what the message says. Producers publish facts; channels
render and deliver; the translation between the two lives here, so adding a
notification for a new event — or removing one — touches one file and no job
handler.

Wording is plain text with no channel markup: see ``message.py``.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from ..core import events
from .message import Attachment, Audience, Notification, Severity
from .service import dispatch

logger = logging.getLogger(__name__)

#: Built lazily so importing this module never pulls in the report renderer
#: (and, through it, reportlab) on a deployment that has no channels.
PdfLoader = Callable[[], Awaitable[bytes]]

_report_pdf_loader: Callable[[events.AnalysisCompleted], PdfLoader] | None = None


def set_report_pdf_loader(factory: Callable[[events.AnalysisCompleted], PdfLoader] | None) -> None:
    """Supply how an analysis report's PDF is produced for a completed run.

    Injected rather than imported so this package does not depend on the
    analysis domain: notifications know a report *can* be attached, not how
    one is built. The analysis worker registers this at startup.
    """
    global _report_pdf_loader
    _report_pdf_loader = factory


async def on_analysis_completed(event: events.AnalysisCompleted) -> None:
    passed = event.quality_gate == "OK"
    coverage = f"{event.coverage:.0f}%" if event.coverage is not None else "n/a"
    attachment = None
    if _report_pdf_loader is not None:
        attachment = Attachment(
            filename=f"sonar-{event.commit_sha[:8]}.pdf",
            media_type="application/pdf",
            load=_report_pdf_loader(event),
        )
    await dispatch(Notification(
        title=f"Analysis complete — {event.repo_name} ({event.ref})",
        audience=Audience.project(event.project_id),
        severity=Severity.SUCCESS if passed else Severity.WARNING,
        fields=(
            ("Quality gate", event.quality_gate),
            ("Issues", f"{event.issues_count} (bugs {event.bugs}, "
                       f"vulnerabilities {event.vulnerabilities}, code smells {event.code_smells})"),
            ("Coverage", coverage),
        ),
        attachment=attachment,
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
