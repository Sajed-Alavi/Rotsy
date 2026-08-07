# Automatic analysis and Smart Insights

## The flow

```
git push  →  GitHub/GitLab webhook  →  Rotsy queues a job
   →  clone the commit  →  run sonar-scanner  →  SonarQube analyzes
   →  Rotsy collects the quality gate + metrics  →  Smart Insights compare
   →  commit status posted back to GitHub/GitLab
```

No GitHub Actions or GitLab CI YAML, no manual `sonar-scanner` invocation. This triggers on push to whichever branches a repository's auto-analyze setting covers (the default branch, unless you've added others — see below). You can also trigger it on demand — pick a repository and branch on **Code Quality → Overview** and click **Run Analysis**; there is one analysis implementation behind both paths, not two that can drift apart.

## Auto-analyze is per-repository, per-branch

Each connected repository has its own auto-analyze toggle and branch list (Project → **Repositories** tab, per row). By default only the repository's default branch triggers on push. Adding a branch there means every push to it queues analysis too; turning auto-analyze off entirely means pushes are ignored and only **Run Analysis** does anything.

## One Sonar project per analyzed branch (Community Edition)

SonarQube Community Edition analyzes exactly one branch per Sonar project — the `sonar.branch.name` parameter is rejected outright for anything else. Rather than surface that limitation to you, Rotsy routes around it: analyzing a repository's default branch uses its normal Sonar project, and analyzing any other branch auto-provisions a **separate** Sonar project scoped to that branch (created, given the same quality gate, and reused on every subsequent analysis of that branch — all automatic). This holds regardless of how many branches or repositories you analyze; nothing about it is manual or something you configure per branch.

## Watching it run

Analysis progress is visible in **Background Jobs**, reusing the same job/SSE mechanism every other long-running Rotsy operation uses (scans, backups, database updates) — no separate infrastructure for this. A `clone_and_analyze` job's row links straight to **Code Quality → Analysis Runs**. Stages: queued, cloning, scanner started, uploading analysis, waiting for quality gate, collecting results, generating insights, updating commit status, completed (or failed, with a reason).

## Smart Insights

Insights are generated after every successful analysis by comparing it to that repository/branch's previous successful run. They are deterministic — fixed rules and thresholds, no LLM, nothing inferred without evidence. Each insight records exactly the numbers that triggered it (e.g. `{"issues_count": 12, "previous_issues_count": 5, "delta": 7}`), so "why does this insight exist" always has an answer.

Current rules:

| Insight | Fires when |
|---|---|
| Quality gate failed | Latest quality gate is not `OK` |
| Quality gate regressed | Was `OK` last time, isn't now |
| New issues introduced | Issue count increased since the previous run |
| Coverage dropped | Coverage fell by more than 5 percentage points |
| Duplication increased | Duplication rose by more than 3 percentage points |

## Project Health Score

A single 0–100 number per project (Project Overview tab), starting at 100 and deducting fixed, published amounts for a failing quality gate, vulnerabilities, bugs, low coverage, high duplication, and severity-weighted recent insights. There's no hidden weighting — the exact factors are documented in `core/health.py` and returned by the API alongside the score itself (`GET /api/projects/{id}/health`) so you can see which ones actually applied.

The score currently reflects SonarQube analysis only, and only for repositories mapped to that Project (a repository analyzed through Code Quality without being mapped to any Project doesn't feed a health score — it's still fully analyzed, browsable, and exportable, just not attributed to a Project's number). It does not yet fold in Trivy/Grype findings from Nexus-hosted images — that requires correlating a specific commit to a specific artifact, which Rotsy does not do reliably yet (see below).

## The PDF export

Every analysis run's detail view has **Download PDF** — metadata, quality gate, metrics, every issue and hotspot, and a **Suggested Fixes** table: one row per distinct rule that fired, with a short remediation hint pulled live from SonarQube's own rule documentation (the same "how to fix it" text its UI shows), so the PDF is useful to someone who never opens SonarQube at all. Fetching that text is best-effort — a report still downloads in full if SonarQube happens to be unreachable at export time, just without that section.

## What's not correlated yet

A Project conceptually spans a repository, its SonarQube analysis, and its Nexus artifacts. Today, only the mapping between repository and Project is wired together automatically, and analysis itself is decoupled — Code Quality runs against any synced repository whether or not it's mapped to a Project. Rotsy does not yet track which Nexus image corresponds to which commit or Project — the Security and Artifacts tabs on a Project page link to the existing global scanning/repository views rather than claiming a filtered, per-project list that doesn't actually exist. This is a known gap, not a hidden one.
