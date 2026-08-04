"""PDF export for a single vulnerability scan report.

Generates a print-ready summary (metadata, severity breakdown, full CVE
table, and a short derived-recommendations section) via reportlab's platypus
layer. Chosen over a frontend jsPDF approach: it needs no system packages
beyond the pure-Python `reportlab` wheel (fits this Dockerfile's minimal-apt
style), and a report's full finding list can exceed the UI's paginated page
size — querying :class:`Vulnerability` directly here (no limit) keeps the
export a single round-trip instead of paginating client-side.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.scans import ScanReport, Vulnerability

# Mirrors frontend/src/features/scan/components/VulnerabilityTable.jsx's
# `SEV_TONE` mapping into components/Badge.jsx's `TONES`
# (CRITICAL->bad/rose, HIGH->warn/amber, MEDIUM->info/sky, LOW/UNKNOWN->neutral/slate),
# recreated as reportlab colors so the PDF's severity coloring matches the web UI.
_SEVERITY_COLORS = {
    "CRITICAL": {"text": colors.HexColor("#be123c"), "bg": colors.HexColor("#fff1f2")},  # rose-700 / rose-50
    "HIGH":     {"text": colors.HexColor("#b45309"), "bg": colors.HexColor("#fffbeb")},  # amber-700 / amber-50
    "MEDIUM":   {"text": colors.HexColor("#0369a1"), "bg": colors.HexColor("#f0f9ff")},  # sky-700 / sky-50
    "LOW":      {"text": colors.HexColor("#475569"), "bg": colors.HexColor("#f1f5f9")},  # slate-600 / slate-100
    "UNKNOWN":  {"text": colors.HexColor("#475569"), "bg": colors.HexColor("#f1f5f9")},  # slate-600 / slate-100
}
_DEFAULT_SEVERITY_COLOR = _SEVERITY_COLORS["UNKNOWN"]

_SLATE_900 = colors.HexColor("#0f172a")
_SLATE_500 = colors.HexColor("#64748b")
_SLATE_200 = colors.HexColor("#e2e8f0")
_SLATE_50 = colors.HexColor("#f8fafc")

_PAGE_MARGIN = 0.6 * inch


def _split_image(image: str) -> tuple[str, str]:
    """Split ``"name:tag"`` on the rightmost colon.

    Matches the grouping used for the repo -> image -> tag scanning UI tree,
    so the PDF's "Image" / "Tag" fields agree with what the UI shows.
    """
    if ":" in image:
        name, _, tag = image.rpartition(":")
        return name, tag
    return image, ""


def _build_recommendations(vulnerabilities: list[Vulnerability], report: ScanReport) -> list[str]:
    """Derive plain-language guidance from the findings — no new DB column needed."""
    fixable = [v for v in vulnerabilities if v.severity in ("CRITICAL", "HIGH") and v.fixed_version]
    unfixable = [v for v in vulnerabilities if v.severity in ("CRITICAL", "HIGH") and not v.fixed_version]
    lines: list[str] = []

    if fixable:
        packages = list(dict.fromkeys(v.package for v in fixable if v.package))[:5]
        pkg_list = ", ".join(packages) if packages else "the affected packages"
        plural = "s" if len(fixable) != 1 else ""
        lines.append(
            f"{len(fixable)} Critical/High finding{plural} have available fixes — "
            f"prioritize upgrading {pkg_list}."
        )
    if unfixable:
        plural = "s" if len(unfixable) != 1 else ""
        lines.append(
            f"{len(unfixable)} Critical/High finding{plural} have no vendor-published fix "
            f"yet — track for a future rescan."
        )
    if not fixable and not unfixable:
        if report.critical == 0 and report.high == 0:
            lines.append("No Critical or High severity findings were detected in this scan.")
        else:
            lines.append("Critical/High findings were recorded without fix-availability detail.")
    if report.medium or report.low:
        lines.append(
            f"{report.medium} Medium and {report.low} Low severity finding(s) remain — "
            f"address after Critical/High items are resolved."
        )
    return lines


def _numbered_canvas_maker(footer_left: str):
    """Build a canvas class that renders "Page X of Y" once the page count is known.

    Standard reportlab two-pass recipe: buffer each page's drawing state in
    ``showPage``, then replay them in ``save`` once the total page count is
    known, stamping the footer on each before it's actually emitted.
    """

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


async def build_report_pdf(session: AsyncSession, report: ScanReport) -> bytes:
    """Render one :class:`ScanReport` and all of its findings as a PDF.

    Queries every :class:`Vulnerability` row for the report directly (no
    limit) so the export always contains the complete finding list, even for
    reports whose findings exceed the UI's paginated page size.
    """
    # Deferred import: the endpoint in routers/scan/reports.py imports this
    # module at its own module-load time, so importing that module back here
    # at *our* module-load time would be circular. By the time
    # build_report_pdf() actually runs, that module is already fully loaded,
    # so a call-time import works and lets us reuse its severity ordering
    # instead of re-sorting findings here.
    from ..routers.scan.reports import _ordered_findings

    stmt = _ordered_findings(select(Vulnerability).where(Vulnerability.report_id == report.id))
    vulnerabilities = list((await session.execute(stmt)).scalars().all())

    image_name, tag = _split_image(report.image)
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=_PAGE_MARGIN, bottomMargin=_PAGE_MARGIN,
        leftMargin=_PAGE_MARGIN, rightMargin=_PAGE_MARGIN,
        title=f"Vulnerability Report - {report.image}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("RotsyTitle", parent=styles["Title"], fontSize=18, textColor=_SLATE_900, spaceAfter=2)
    subtitle_style = ParagraphStyle("RotsySubtitle", parent=styles["Normal"], fontSize=9, textColor=_SLATE_500, spaceAfter=14)
    h2_style = ParagraphStyle("RotsyH2", parent=styles["Heading2"], fontSize=12, textColor=_SLATE_900, spaceBefore=14, spaceAfter=6)
    body_style = ParagraphStyle("RotsyBody", parent=styles["Normal"], fontSize=9.5, textColor=_SLATE_900, leading=13)
    cell_style = ParagraphStyle("RotsyCell", parent=styles["Normal"], fontSize=8, textColor=_SLATE_900, leading=10)

    story = []
    story.append(Paragraph("Rotsy Vulnerability Scan Report", title_style))
    story.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", subtitle_style,
    ))

    scan_date = report.started_at.strftime("%Y-%m-%d %H:%M UTC") if report.started_at else "—"
    meta_rows = [
        ["Repository", report.target_repo, "Scanner", report.scanner],
        ["Image", image_name, "Tag", tag or "—"],
        ["Scan date", scan_date, "Status", report.status],
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
    story.append(meta_table)

    total = report.critical + report.high + report.medium + report.low + report.unknown
    story.append(Paragraph("Summary", h2_style))
    story.append(Paragraph(f"<b>{total}</b> total findings across all severities.", body_style))
    story.append(Spacer(1, 6))

    sev_rows = [
        ["Critical", "High", "Medium", "Low", "Unknown"],
        [str(report.critical), str(report.high), str(report.medium), str(report.low), str(report.unknown)],
    ]
    sev_table = Table(sev_rows, colWidths=[1.34 * inch] * 5)
    sev_style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTSIZE", (0, 1), (-1, 1), 13),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 1), (-1, 1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, _SLATE_200),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _SLATE_200),
    ]
    for col, sev in enumerate(("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")):
        tone = _SEVERITY_COLORS.get(sev, _DEFAULT_SEVERITY_COLOR)
        sev_style.append(("BACKGROUND", (col, 0), (col, 0), tone["bg"]))
        sev_style.append(("TEXTCOLOR", (col, 0), (col, 0), tone["text"]))
        sev_style.append(("TEXTCOLOR", (col, 1), (col, 1), tone["text"]))
    sev_table.setStyle(TableStyle(sev_style))
    story.append(sev_table)

    story.append(Paragraph("Recommendations", h2_style))
    for line in _build_recommendations(vulnerabilities, report):
        story.append(Paragraph(f"&bull; {line}", body_style))
        story.append(Spacer(1, 3))

    story.append(Paragraph(f"Findings ({len(vulnerabilities)})", h2_style))
    if vulnerabilities:
        header = ["CVE", "Severity", "Package", "Installed", "Fixed"]
        finding_rows = [header]
        for v in vulnerabilities:
            finding_rows.append([
                Paragraph(v.cve or "—", cell_style),
                v.severity,
                Paragraph(v.package or "—", cell_style),
                Paragraph(v.installed_version or "—", cell_style),
                Paragraph(v.fixed_version or "—", cell_style),
            ])
        finding_table = Table(
            finding_rows,
            colWidths=[1.15 * inch, 0.85 * inch, 1.75 * inch, 1.35 * inch, 1.4 * inch],
            repeatRows=1,
        )
        finding_style = [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 0), (-1, 0), _SLATE_900),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("GRID", (0, 0), (-1, -1), 0.4, _SLATE_200),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SLATE_50]),
        ]
        for row_idx, v in enumerate(vulnerabilities, start=1):
            tone = _SEVERITY_COLORS.get(v.severity, _DEFAULT_SEVERITY_COLOR)
            finding_style.append(("TEXTCOLOR", (1, row_idx), (1, row_idx), tone["text"]))
            finding_style.append(("FONTNAME", (1, row_idx), (1, row_idx), "Helvetica-Bold"))
        finding_table.setStyle(TableStyle(finding_style))
        story.append(finding_table)
    else:
        story.append(Paragraph("No vulnerabilities were recorded for this report.", body_style))

    footer_left = f"Rotsy · {report.target_repo}/{report.image}"
    doc.build(story, canvasmaker=_numbered_canvas_maker(footer_left))
    return buf.getvalue()
