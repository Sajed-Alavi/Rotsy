"""How vulnerability findings are ordered — one definition, several callers.

Severity ranking lives here rather than in the router that first needed it:
the PDF report builder in ``services/`` must order findings identically to
the API that lists them, and was importing the router's private helper to do
it — a ``services/`` -> ``routers/`` import, backwards against this project's
layering.
"""

from __future__ import annotations

from sqlalchemy import asc, case, desc

from ..models import Vulnerability

# Rank severities explicitly. Ordering keyed off the first letter of the
# severity string put CRITICAL after nothing in particular and collated
# MEDIUM with anything else starting "M".
SEVERITY_RANK = case(
    {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3},
    value=Vulnerability.severity,
    else_=4,
)

SORT_COLUMNS = {
    "severity": None,  # handled specially — falls back to the rank case above
    "cvss": Vulnerability.cvss,
    "cve": Vulnerability.cve,
    "package": Vulnerability.package,
}


def ordered_findings(stmt, sort: str = "severity", order: str = "desc"):
    """Most serious first by default: severity rank, then CVSS descending.

    ``sort``/``order`` let the caller pick a different column; ``severity``
    (the default) always orders by rank first, CVSS descending as a tiebreak,
    regardless of ``order`` — the other three columns honor ``order`` directly.
    """
    if sort not in SORT_COLUMNS or sort == "severity":
        return stmt.order_by(SEVERITY_RANK, desc(Vulnerability.cvss))
    column = SORT_COLUMNS[sort]
    direction = asc if order == "asc" else desc
    return stmt.order_by(direction(column), SEVERITY_RANK)
