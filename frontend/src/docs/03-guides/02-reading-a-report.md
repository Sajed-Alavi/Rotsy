# Reading a report

## The three levels

The scanning section is a drill-down, and it helps to know which level you are at.

**Images** — one row per image the system knows about, with its state and current severity counts. Start here when you want to know *whether* something is a problem.

**Reports** — one row per scanner run. An image scanned by both Trivy and Grype has two reports. Start here when you want to know *what happened* on a particular run, including failures.

**Findings** — every CVE across every report, filterable and searchable. Start here when you are hunting a specific vulnerability across your estate: "is CVE-2024-3094 anywhere?"

## Severities

Findings are normalised to `critical`, `high`, `medium`, `low` and `unknown`. Where a scanner reports a CVSS score but no severity label, the score is mapped to a band.

`unknown` is not "safe". It means neither the scanner nor its database assigned a severity — common for very new CVEs and for ecosystems with sparse metadata. Treat it as unclassified, not harmless.

## Why two scanners disagree

Trivy and Grype use different vulnerability databases and different matching logic, so their counts will differ on the same image. This is expected and useful: agreement raises confidence, and a finding in only one is worth a look rather than a bug report.

Neither is a superset of the other.

## Failed reports

A report with status `failed` carries the reason in its error field, shown inline in both the Reports table and the Images row. Common causes are covered in [Troubleshooting scans](/docs/troubleshooting).

A failure is never reported as a clean scan. If the database could not be downloaded and no usable database is on disk, the scan fails and says so, rather than returning zero findings — zero findings and "could not check" look identical in a dashboard, and conflating them is how people ship vulnerable images believing they are clean.

## Clearing reports

Individual reports can be deleted from the Reports view; **clear all** wipes them. Reports are a cache of scanner output, not an audit record — deleting them loses history but changes nothing about what is in your registry. The image ledger is separate and survives.
