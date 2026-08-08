"""PDF export for a single SonarQube analysis run.

Same shape and layout approach as :mod:`app.services.scan_report_pdf`
(metadata, a summary, and a full finding table) via reportlab's platypus
layer — one export pattern for both scanning modules rather than two, since
neither report needs anything the other's approach can't already do.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AnalysisRun, GitHubRepository, GitLabRepository, QualityGateResult, SonarHotspot, SonarIssue, SonarProject,
)
from ..modules.sonar.connector import SonarClient, SonarError

logger = logging.getLogger(__name__)

# Mirrors Badge.jsx's tone palette, recreated as reportlab colors — rose for
# the two severities that block Rotsy's own Quality Gate (see
# modules/sonar/provisioning.py's "Rotsy Standard" conditions), amber/sky/slate
# stepping down for the rest, so the PDF's coloring matches the web UI.
_SEVERITY_COLORS = {
    "BLOCKER":  {"text": colors.HexColor("#be123c"), "bg": colors.HexColor("#fff1f2")},  # rose-700 / rose-50
    "CRITICAL": {"text": colors.HexColor("#be123c"), "bg": colors.HexColor("#fff1f2")},
    "MAJOR":    {"text": colors.HexColor("#b45309"), "bg": colors.HexColor("#fffbeb")},  # amber-700 / amber-50
    "MINOR":    {"text": colors.HexColor("#0369a1"), "bg": colors.HexColor("#f0f9ff")},  # sky-700 / sky-50
    "INFO":     {"text": colors.HexColor("#475569"), "bg": colors.HexColor("#f1f5f9")},  # slate-600 / slate-100
}
_DEFAULT_SEVERITY_COLOR = _SEVERITY_COLORS["INFO"]

_GATE_COLORS = {
    "OK":    {"text": colors.HexColor("#047857"), "bg": colors.HexColor("#ecfdf5")},  # emerald-700 / emerald-50
    "WARN":  {"text": colors.HexColor("#b45309"), "bg": colors.HexColor("#fffbeb")},
    "ERROR": {"text": colors.HexColor("#be123c"), "bg": colors.HexColor("#fff1f2")},
}

_SLATE_900 = colors.HexColor("#0f172a")
_SLATE_500 = colors.HexColor("#64748b")
_SLATE_200 = colors.HexColor("#e2e8f0")
_SLATE_50 = colors.HexColor("#f8fafc")

_PAGE_MARGIN = 0.6 * inch

# Explicit rank so HIGH sorts first — plain string ordering would put HIGH
# after MEDIUM alphabetically.
_HOTSPOT_PROBABILITY_RANK = case(
    {"HIGH": 0, "MEDIUM": 1, "LOW": 2}, value=SonarHotspot.vulnerability_probability, else_=3,
)


async def _repository_label_and_url(session: AsyncSession, sonar_project: SonarProject) -> tuple[str, str | None]:
    if sonar_project.github_repository_id:
        repo = await session.get(GitHubRepository, sonar_project.github_repository_id)
        if repo is None:
            return sonar_project.sonar_project_key, None
        return repo.full_name, f"https://github.com/{repo.full_name}"
    if sonar_project.gitlab_repository_id:
        repo = await session.get(GitLabRepository, sonar_project.gitlab_repository_id)
        if repo is None:
            return sonar_project.sonar_project_key, None
        # ``gitlab_url`` is the server-to-server address the backend uses to
        # reach GitLab (e.g. host.docker.internal in this dev compose setup)
        # — not resolvable from whatever machine opens this PDF. Swap in the
        # same browser-facing host a human already uses to sign into GitLab
        # itself, mirroring the WEBHOOK_BASE_URL split in config.py.
        browser_url = repo.gitlab_url.replace("host.docker.internal", "localhost")
        return repo.full_path, f"{browser_url.rstrip('/')}/{repo.full_path}"
    return sonar_project.sonar_project_key, None


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_WHITESPACE_RE = re.compile(r"\s+")
_FIX_HINT_MAX_LEN = 220


def _short_fix_hint(rule: dict) -> str | None:
    """A one- or two-sentence, plain-text remediation hint from a Sonar
    rule's own ``descriptionSections`` — the same "how to fix it" text
    SonarQube's own UI shows per rule, condensed for a summary table rather
    than reproduced in full (code samples and all, which the section's raw
    HTML also contains and which is too long for this table)."""
    sections = {s.get("key"): s.get("content", "") for s in rule.get("descriptionSections", [])}
    html = sections.get("how_to_fix") or sections.get("assess_the_problem") or sections.get("root_cause")
    if not html:
        return None
    # Only the text up to the first heading/code block — that's the plain
    # explanation; headings after it ("Noncompliant code example", ...)
    # introduce the code samples this hint deliberately leaves out.
    html = re.split(r"<h[1-6]|<pre", html, maxsplit=1)[0]
    text = _HTML_WHITESPACE_RE.sub(" ", _HTML_TAG_RE.sub(" ", html)).strip()
    if not text:
        return None
    if len(text) > _FIX_HINT_MAX_LEN:
        text = text[:_FIX_HINT_MAX_LEN].rsplit(" ", 1)[0] + "…"
    return text


async def _fetch_fix_hints(client: SonarClient | None, rule_keys: set[str]) -> dict[str, str]:
    """Best-effort: a PDF export shouldn't fail, or lose the rest of its
    content, just because SonarQube is briefly unreachable or a rule was
    removed since the analysis ran — so any failure here just means that
    rule's row is skipped from the Suggested Fixes table, not that the
    whole report fails."""
    if client is None:
        return {}
    hints: dict[str, str] = {}
    for rule_key in sorted(rule_keys):
        try:
            rule = await client.rule(rule_key)
        except SonarError:
            logger.warning("Could not fetch rule %s for PDF fix hints", rule_key, exc_info=True)
            continue
        hint = _short_fix_hint(rule)
        if hint:
            hints[rule_key] = hint
    return hints


def _numbered_canvas_maker(footer_left: str):
    """Standard reportlab two-pass "Page X of Y" recipe — identical to
    ``scan_report_pdf._numbered_canvas_maker``, duplicated rather than shared
    since neither module imports the other (see the import-direction rule for
    modules; the same discipline applies to keeping these two report builders
    independent)."""

    class NumberedCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states: list[dict] = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_footer(total_pages)
                pdfcanvas.Canvas.showPage(self)
            pdfcanvas.Canvas.save(self)

        def _draw_footer(self, total_pages: int):
            self.saveState()
            self.setFont("Helvetica", 7.5)
            self.setFillColor(_SLATE_500)
            self.drawString(_PAGE_MARGIN, 0.4 * inch, footer_left)
            self.drawRightString(
                LETTER[0] - _PAGE_MARGIN, 0.4 * inch,
                f"Page {self._pageNumber} of {total_pages}",
            )
            self.restoreState()

    return NumberedCanvas


def _build_metadata_table(run: AnalysisRun, repo_label: str) -> Table:
    scan_date = run.started_at.strftime("%Y-%m-%d %H:%M UTC") if run.started_at else "—"
    meta_rows = [
        ["Repository", repo_label, "Branch", run.ref],
        ["Commit", run.commit_sha[:12], "Trigger", run.trigger],
        ["Analyzed", scan_date, "Status", run.status],
    ]
    meta_table = Table(meta_rows, colWidths=[1.0 * inch, 2.35 * inch, 1.0 * inch, 2.35 * inch])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), _SLATE_500),
        ("TEXTCOLOR", (2, 0), (2, -1), _SLATE_500),
        ("TEXTCOLOR", (1, 0), (1, -1), _SLATE_900),
        ("TEXTCOLOR", (3, 0), (3, -1), _SLATE_900),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, _SLATE_200),
    ]))
    return meta_table


def _build_quality_gate_flowables(gate: QualityGateResult | None, h2_style, cell_style) -> list:
    flowables = [Paragraph("Quality Gate", h2_style)]
    gate_status = gate.status if gate else "—"
    gate_tone = _GATE_COLORS.get(gate_status, {"text": _SLATE_500, "bg": _SLATE_50})
    gate_table = Table([[gate_status]], colWidths=[1.5 * inch])
    gate_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 0), (-1, -1), gate_tone["bg"]),
        ("TEXTCOLOR", (0, 0), (-1, -1), gate_tone["text"]),
        ("BOX", (0, 0), (-1, -1), 0.5, _SLATE_200),
    ]))
    flowables.append(gate_table)
    flowables.append(Spacer(1, 8))

    conditions = gate.conditions if gate else []
    if conditions:
        cond_rows = [["Metric", "Comparator", "Threshold", "Actual", "Status"]]
        for c in conditions:
            cond_rows.append([
                Paragraph(str(c.get("metricKey", c.get("metric", "—"))), cell_style),
                str(c.get("comparator", c.get("op", "—"))),
                str(c.get("errorThreshold", c.get("error", "—"))),
                str(c.get("actualValue", c.get("value", "—"))),
                str(c.get("status", "—")),
            ])
        cond_table = Table(cond_rows, colWidths=[2.2 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch, 1.5 * inch], repeatRows=1)
        cond_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 0), (-1, 0), _SLATE_900),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.4, _SLATE_200),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SLATE_50]),
        ]))
        flowables.append(cond_table)
    return flowables


def _build_metrics_table(run: AnalysisRun) -> Table:
    metric_rows = [
        ["Bugs", "Vulnerabilities", "Code Smells", "Hotspots", "Coverage", "Duplication"],
        [
            str(run.bugs if run.bugs is not None else "—"),
            str(run.vulnerabilities if run.vulnerabilities is not None else "—"),
            str(run.code_smells if run.code_smells is not None else "—"),
            str(run.security_hotspots if run.security_hotspots is not None else "—"),
            f"{run.coverage:.1f}%" if run.coverage is not None else "—",
            f"{run.duplication_pct:.1f}%" if run.duplication_pct is not None else "—",
        ],
    ]
    metric_table = Table(metric_rows, colWidths=[1.2 * inch] * 6)
    metric_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("FONTSIZE", (0, 1), (-1, 1), 13),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 1), (-1, 1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, _SLATE_200),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _SLATE_200),
    ]))
    return metric_table


def _build_issues_flowables(issues: list[SonarIssue], h2_style, body_style, cell_style) -> list:
    flowables = [Paragraph(f"Issues ({len(issues)})", h2_style)]
    if not issues:
        flowables.append(Paragraph("No open issues were recorded for this analysis.", body_style))
        return flowables
    header = ["Severity", "Type", "Rule", "File : Line", "Message", "Effort"]
    issue_rows = [header]
    for i in issues:
        location = f"{i.component}:{i.line}" if i.line else (i.component or "—")
        issue_rows.append([
            i.severity,
            i.type.replace("_", " ").title(),
            Paragraph(i.rule or "—", cell_style),
            Paragraph(location, cell_style),
            Paragraph(i.message or "—", cell_style),
            i.effort or "—",
        ])
    issue_table = Table(
        issue_rows,
        colWidths=[0.75 * inch, 0.85 * inch, 1.1 * inch, 1.55 * inch, 2.05 * inch, 0.6 * inch],
        repeatRows=1,
    )
    issue_style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), _SLATE_900),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, _SLATE_200),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SLATE_50]),
    ]
    for row_idx, i in enumerate(issues, start=1):
        tone = _SEVERITY_COLORS.get(i.severity, _DEFAULT_SEVERITY_COLOR)
        issue_style.append(("TEXTCOLOR", (0, row_idx), (0, row_idx), tone["text"]))
        issue_style.append(("FONTNAME", (0, row_idx), (0, row_idx), "Helvetica-Bold"))
    issue_table.setStyle(TableStyle(issue_style))
    flowables.append(issue_table)
    return flowables


def _build_suggested_fixes_flowables(
    issues: list[SonarIssue], fix_hints: dict[str, str], h2_style, cell_style,
) -> list:
    if not fix_hints:
        return []
    rules_present = [r for r in dict.fromkeys(i.rule for i in issues if i.rule) if r in fix_hints]
    flowables = [Paragraph(f"Suggested Fixes ({len(rules_present)})", h2_style)]
    fix_rows = [["Rule", "Issues", "How to fix"]]
    for rule_key in rules_present:
        count = sum(1 for i in issues if i.rule == rule_key)
        fix_rows.append([
            Paragraph(rule_key, cell_style),
            str(count),
            Paragraph(fix_hints[rule_key], cell_style),
        ])
    fix_table = Table(fix_rows, colWidths=[1.3 * inch, 0.6 * inch, 5.4 * inch], repeatRows=1)
    fix_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), _SLATE_900),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, _SLATE_200),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SLATE_50]),
    ]))
    flowables.append(fix_table)
    return flowables


def _build_hotspots_flowables(hotspots: list[SonarHotspot], h2_style, body_style, cell_style) -> list:
    flowables = [Paragraph(f"Security Hotspots ({len(hotspots)})", h2_style)]
    if not hotspots:
        flowables.append(Paragraph("No security hotspots were recorded for this analysis.", body_style))
        return flowables
    header = ["Probability", "File : Line", "Category", "Message"]
    hotspot_rows = [header]
    for h in hotspots:
        location = f"{h.component}:{h.line}" if h.line else (h.component or "—")
        hotspot_rows.append([
            h.vulnerability_probability or "—",
            Paragraph(location, cell_style),
            Paragraph(h.security_category or "—", cell_style),
            Paragraph(h.message or "—", cell_style),
        ])
    hotspot_table = Table(
        hotspot_rows, colWidths=[1.0 * inch, 1.7 * inch, 1.4 * inch, 2.8 * inch], repeatRows=1,
    )
    hotspot_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), _SLATE_900),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, _SLATE_200),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SLATE_50]),
    ]))
    flowables.append(hotspot_table)
    return flowables


async def build_analysis_report_pdf(
    session: AsyncSession, run: AnalysisRun, sonar_project: SonarProject,
    client: SonarClient | None = None,
) -> bytes:
    """Render one :class:`AnalysisRun` — metadata, quality gate, metrics,
    every issue, and every hotspot — as a PDF.

    Queries :class:`SonarIssue`/:class:`SonarHotspot` directly (no limit) so
    the export always has the complete finding list even when it exceeds the
    UI's paginated page size — same reasoning as ``build_report_pdf`` for
    vulnerability scans.

    ``client``, if given, is used to pull a short remediation hint per rule
    from SonarQube itself (the same "how to fix it" text its own UI shows)
    into a "Suggested Fixes" table — one row per distinct rule, not per
    issue, since the point is "what to change", not another copy of every
    finding. Omitted entirely (not an error) when ``client`` is ``None`` or
    a rule's description can't be fetched.
    """
    repo_label, repo_url = await _repository_label_and_url(session, sonar_project)
    gate = await session.scalar(
        select(QualityGateResult).where(QualityGateResult.analysis_run_id == run.id)
    )
    issues = list((
        await session.execute(
            select(SonarIssue).where(SonarIssue.analysis_run_id == run.id)
            .order_by(SonarIssue.severity, SonarIssue.component)
        )
    ).scalars().all())
    fix_hints = await _fetch_fix_hints(client, {i.rule for i in issues if i.rule})
    hotspots = list((
        await session.execute(
            select(SonarHotspot).where(SonarHotspot.analysis_run_id == run.id)
            .order_by(_HOTSPOT_PROBABILITY_RANK, SonarHotspot.component)
        )
    ).scalars().all())

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=_PAGE_MARGIN, bottomMargin=_PAGE_MARGIN,
        leftMargin=_PAGE_MARGIN, rightMargin=_PAGE_MARGIN,
        title=f"SonarQube Analysis Report - {repo_label}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("RotsyTitle", parent=styles["Title"], fontSize=18, textColor=_SLATE_900, spaceAfter=2)
    subtitle_style = ParagraphStyle("RotsySubtitle", parent=styles["Normal"], fontSize=9, textColor=_SLATE_500, spaceAfter=14)
    h2_style = ParagraphStyle("RotsyH2", parent=styles["Heading2"], fontSize=12, textColor=_SLATE_900, spaceBefore=14, spaceAfter=6)
    body_style = ParagraphStyle("RotsyBody", parent=styles["Normal"], fontSize=9.5, textColor=_SLATE_900, leading=13)
    link_style = ParagraphStyle("RotsyLink", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#0369a1"), spaceBefore=16)
    cell_style = ParagraphStyle("RotsyCell", parent=styles["Normal"], fontSize=8, textColor=_SLATE_900, leading=10)

    story = []
    story.append(Paragraph("Rotsy SonarQube Analysis Report", title_style))
    story.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", subtitle_style,
    ))

    story.append(_build_metadata_table(run, repo_label))
    story.extend(_build_quality_gate_flowables(gate, h2_style, cell_style))
    story.append(Paragraph("Metrics", h2_style))
    story.append(_build_metrics_table(run))
    story.extend(_build_issues_flowables(issues, h2_style, body_style, cell_style))
    story.extend(_build_suggested_fixes_flowables(issues, fix_hints, h2_style, cell_style))
    story.extend(_build_hotspots_flowables(hotspots, h2_style, body_style, cell_style))

    if repo_url:
        story.append(Paragraph(f'Repository: <link href="{repo_url}">{repo_url}</link>', link_style))

    footer_left = f"Rotsy · {repo_label}"
    doc.build(story, canvasmaker=_numbered_canvas_maker(footer_left))
    return buf.getvalue()
