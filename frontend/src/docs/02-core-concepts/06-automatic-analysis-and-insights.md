# Automatic analysis and Smart Insights

## The flow

```
git push  →  GitHub webhook  →  Rotsy queues a job
   →  clone the commit  →  run sonar-scanner  →  SonarQube analyzes
   →  Rotsy collects the quality gate + metrics  →  Smart Insights compare
   →  commit status posted back to GitHub
```

No GitHub Actions workflow, no CI YAML, no manual `sonar-scanner` invocation. This triggers on push to the repository's default branch. You can also trigger it on demand — **Run Analysis** on a Project's Analysis tab enqueues the exact same job a push would; there is one analysis implementation, not two paths that can drift apart.

## Watching it run

Analysis progress is visible in **Background Jobs**, reusing the same job/SSE mechanism every other long-running Rotsy operation uses (scans, backups, database updates) — no separate infrastructure for this. Stages: queued, cloning, scanner started, uploading analysis, waiting for quality gate, collecting results, generating insights, updating GitHub status, completed (or failed, with a reason).

## Smart Insights

Insights are generated after every successful analysis by comparing it to the project's previous successful run. They are deterministic — fixed rules and thresholds, no LLM, nothing inferred without evidence. Each insight records exactly the numbers that triggered it (e.g. `{"issues_count": 12, "previous_issues_count": 5, "delta": 7}`), so "why does this insight exist" always has an answer.

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

The score currently reflects SonarQube analysis only. It does not yet fold in Trivy/Grype findings from Nexus-hosted images — that requires correlating a specific commit to a specific artifact, which Rotsy does not do reliably yet (see below).

## What's not correlated yet

A Project conceptually spans a repository, its SonarQube analysis, and its Nexus artifacts. Today, only the first two are wired together automatically. Rotsy does not yet track which Nexus image corresponds to which commit or Project — the Security and Artifacts tabs on a Project page link to the existing global scanning/repository views rather than claiming a filtered, per-project list that doesn't actually exist. This is a known gap, not a hidden one.
