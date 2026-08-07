# A quick tour

A map of the sidebar, and which page answers which question.

## Overview

**Dashboard** — health, CVE totals, storage, recent jobs. The starting point.

**Projects** — groups a GitHub/GitLab repository together with its Nexus artifacts. Tabs: Overview (health score, latest quality gate, recent insights), Repositories (connect GitHub/GitLab repositories — discovered or by name — and toggle each one's auto-analyze branches), Security and Artifacts (link out to the existing scanning/repository views), and Insights. Running and browsing SonarQube analysis itself lives in the global **Code Quality** section below, not on the Project.

**Browse Files** — what a repository actually contains. Two views: *Images* (each image as a folder expanding to its tags, with sizes, push times and a delete action) and *Files* (the raw asset tree, with downloads proxied through the backend so your browser never sees Nexus credentials).

**Storage Analyzer** — where the space went, per repository, streamed live as it walks the assets.

## Monitoring

**Metrics** — repository and blobstore size over time.

**Background Jobs** — every queued and running job, with live progress. Scans, database downloads, backups and analyses all appear here.

**Alerts** — threshold rules that fire a webhook when storage or usage crosses a line.

## Repositories

**Repositories** — create, configure and delete repositories.

**Blobstores** — the underlying storage, file or S3.

**Retention & Cleanup** — policies that delete old tags on a schedule, with a dry-run preview before anything is removed.

## Security

**Code Quality** — pick any synced GitHub/GitLab repository and branch and run SonarQube analysis, independent of which Project (if any) it belongs to. Four tabs: Overview (the repo/branch picker and Run Analysis), Analysis Runs (history, per-run issues/hotspots, PDF export), Findings (every open issue/hotspot across the latest run of every repository), and Settings (connection health, Check for Updates). See [Automatic analysis and Smart Insights](/docs/automatic-analysis-and-insights).

**Vulnerability Scanning** — the centrepiece, with six views of its own: Overview, Targets, Images, Reports, Findings and Database Management. See [Scanning on push](/docs/scanning-on-push).

**System & Scripts** — backups, archives and Nexus-to-Nexus sync.

## Integrations

**Access & Webhooks** — API tokens for CI, an index of every webhook, and which repositories are readable without logging in.

**Task Manager** — Nexus's own scheduled tasks. Notably the *Compact blob store* task, which is what actually reclaims disk after a delete.

## Administration

**Users**, **Roles & Permissions** — Rotsy's own RBAC, including per-image scoping.

**Audit Log** — who changed what.

**Settings** — five tabs: General (your profile and password), Integrations (Nexus, GitHub, GitLab, SonarQube connection cards), Security (links to Access & Webhooks, Users, Roles), Scanning (registry discovery, scan-on-push webhook, scanner proxy), System (health, jobs, versions).

## Next

Read [Architecture](/docs/architecture) for how the pieces fit, [Connecting GitHub](/docs/connecting-github) or [Connecting GitLab](/docs/connecting-gitlab) plus [Connecting SonarQube](/docs/connecting-sonarqube) to get automatic analysis working, or [Scanning on push](/docs/scanning-on-push) for image scanning.
