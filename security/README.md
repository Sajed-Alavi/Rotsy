# Security tracking — Sharpy

This directory is the vulnerability record for Sharpy (the FastAPI + React
management/scanning console in front of Sonatype Nexus). It exists so a
finding's severity, root cause, fix, and validation status are visible in one
place instead of scattered across commits and memory.

## Layout

```
security/
  README.md                      — this file
  VULNERABILITY-INVENTORY.md     — index of every finding: ID, severity, component, status
  findings/                      — one file per finding, grouped by severity
    critical/                      CRIT-*
    high/                          HIGH-*
    medium/                        MED-*
    low/                           LOW-*
  reports/                       — dated point-in-time audit and remediation summaries
  cve/                           — dependency and container-image CVE tracking
```

Findings are grouped by severity rather than kept flat: severity is how the list
is actually read ("what is still Critical?"), and a flat directory of sixteen
files answered that only by filename prefix.

`cve/` is separate from `findings/` on purpose. A finding is a defect in *this*
codebase with a root cause and a fix here; a dependency CVE is a version number
that has to move. They have different lifecycles — a dependency CVE recurs every
time upstream publishes — so they get different documents.

## How a finding is tracked

Every finding gets one file in `findings/<severity>/` named `<ID>-<slug>.md`
using this template:

```
Vulnerability Name:
Severity:              Critical | High | Medium | Low
Affected Component:
Description:
Root Cause:
Security Impact:
Recommended Fix:
Implementation Status:  Fixed | Deferred (backlog)
Testing Result:
```

`Fixed` entries name the regression test that validates them
(`backend/tests/...`). `Deferred (backlog)` entries still get a concrete
recommended fix so the next pass is actionable rather than "go audit again
from scratch."

`Affected Component` cites file paths and the specific function or symbol.
Line numbers are deliberately **not** used: they go stale on the first edit to
an unrelated part of the file, and a wrong line number is worse than none. Cite
the symbol and let the reader search for it.

## Adding a new finding

1. Pick the next ID in sequence for its severity (`CRIT-`, `HIGH-`, `MED-`,
   `LOW-`).
2. Add a row to `VULNERABILITY-INVENTORY.md`, and update the totals line.
3. Write `findings/<severity>/<ID>-<slug>.md` using the template above — cite
   real file paths and symbols, not general categories.
4. If you fix it, add or extend a test under `backend/tests/` and reference it
   in `Testing Result`.

For a dependency or base-image CVE, add a row to
`cve/dependency-cve-review.md` instead, naming the fixed version and the file
the pin lives in.

## Severity guide (as used here)

- **Critical** — remotely exploitable by any authenticated (or unauthenticated,
  for pre-auth issues) user, with a direct path to full compromise: arbitrary
  data access outside the intended boundary, forged authentication, or
  privileged-account takeover.
- **High** — a real access-control or integrity bypass, or a dependency with
  an unfixed CVE sitting in a security-relevant path (auth, crypto), but
  either requires a specific configuration or doesn't reach full compromise
  on its own.
- **Medium** — a real weakness that increases blast radius or aids a
  follow-on attack (SSRF via an admin-only feature, weak defaults, mass
  assignment) but needs an additional condition or grants limited impact.
- **Low** — best-practice gaps, unmaintained-but-not-actively-exploited
  dependencies, or narrow information disclosure with low sensitivity.
