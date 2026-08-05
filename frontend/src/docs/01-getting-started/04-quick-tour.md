# A quick tour

A map of the sidebar, and which page answers which question.

## Overview

**Dashboard** — health, CVE totals, storage, recent jobs. The starting point.

**Projects** — the main DevSecOps view. Each Project ties a GitHub repository, its SonarQube analysis, and (where connected) Nexus artifacts together. Tabs: Overview (health score, latest quality gate, recent insights), Analysis (history + manual Run Analysis), Security and Artifacts (link out to the existing scanning/repository views), and Insights.

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

**Vulnerability Scanning** — the centrepiece, with six views of its own: Overview, Targets, Images, Reports, Findings and Database Management. See [Scanning on push](/docs/scanning-on-push).

**System & Scripts** — backups, archives and Nexus-to-Nexus sync.

## Integrations

**Access & Webhooks** — API tokens for CI, an index of every webhook, and which repositories are readable without logging in.

**Task Manager** — Nexus's own scheduled tasks. Notably the *Compact blob store* task, which is what actually reclaims disk after a delete.

## Administration

**Users**, **Roles & Permissions** — Rotsy's own RBAC, including per-image scoping.

**Audit Log** — who changed what.

**Settings** — five tabs: General (your profile and password), Integrations (Nexus, GitHub, SonarQube connection cards), Security (links to Access & Webhooks, Users, Roles), Scanning (registry discovery, scan-on-push webhook, scanner proxy), System (health, jobs, versions).

## Next

Read [Architecture](/docs/architecture) for how the pieces fit, [Connecting GitHub](/docs/connecting-github) and [Connecting SonarQube](/docs/connecting-sonarqube) to get automatic analysis working, or [Scanning on push](/docs/scanning-on-push) for image scanning.
