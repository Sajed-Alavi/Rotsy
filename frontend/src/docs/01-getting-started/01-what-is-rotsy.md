# What is Rotsy?

Rotsy is a DevSecOps intelligence platform: a single console over **GitHub**, **SonarQube**, and **Sonatype Nexus Repository Manager**, with static container-image vulnerability scanning and automatic code analysis as its centrepiece.

Each of those tools is good at one job and bad at showing you the picture across all three. *Which project's coverage just dropped? Which commit introduced a new vulnerability? Which images have critical CVEs, and what is eating disk?* Rotsy sits in front of GitHub, SonarQube, and Nexus, orchestrates them, and answers those from one place — a **Project** ties a repository, its SonarQube analysis, and its Nexus artifacts together instead of leaving you to check three tools separately.

Rotsy does not replace any of them. GitHub still hosts your code, SonarQube still performs the static analysis, Nexus still stores the artifacts. Rotsy owns the orchestration, the correlation between them, and the product experience — including **Smart Insights**, which interpret what changed between two analyses instead of just displaying raw numbers.

## What it adds

| Capability | What it means |
|---|---|
| Automatic analysis | Push to GitHub → Rotsy clones, runs SonarQube, and reports results — no CI YAML, no manual scan |
| Smart Insights | Deterministic, evidence-backed findings from comparing consecutive analyses (new issues, coverage regressions, quality gate changes) |
| Project Health Score | One 0–100 number per project, from a documented, fixed formula — no black box |
| Vulnerability scanning | Trivy and Grype scan every pushed image and store the findings |
| Image browsing | See images and tags, not a flat wall of layer blobs |
| Storage analysis | Find what is consuming space, per repository |
| Retention | Policy-driven cleanup of old tags |
| Metrics and alerts | Track growth over time, fire a webhook on thresholds |
| RBAC | Users, roles and per-image scoping, independent of Nexus's or GitHub's own |
| Audit trail | Who changed what, and when |

## Three rules that shape everything

These are not aspirations. Each one is enforced in code.

**1. Static analysis only.** No container is ever started, run, or spun up. Images are read over the Docker Registry v2 API and analysed as data. There is no Docker socket mounted and no Docker client in the image. See [The static-only guarantee](/docs/static-only-guarantee).

**2. Zero client-side registry configuration.** Nexus serves each Docker repository on its own connector port. Rotsy asks Nexus what those ports are rather than making you configure them. There is no `DOCKER_REGISTRY_URL` setting. See [Registry discovery](/docs/registry-discovery).

**3. Event-driven scanning.** An image is scanned when it is pushed, or when you ask. Never on startup, never on a schedule, never twice by accident. See [How scanning is triggered](/docs/scanning-on-push).

## What it is not

Rotsy does not replace Nexus and does not proxy artifact traffic. Your clients still `docker pull` from Nexus directly. It does not replace SonarQube's analysis engine or GitHub's hosting — it drives sonar-scanner and the GitHub API on your behalf, and keeps its own database of results, metrics and insights.

It is not a CI/CD system: no pipelines, no YAML, no build-step orchestration beyond what sonar-scanner itself needs. It is not a deployment or Kubernetes tool. It also does not do runtime security — it tells you what vulnerabilities are *in* an image or *in* a commit, not what a running container is doing.

## Next

Start with [Your first login](/docs/first-login), then [Connecting Nexus](/docs/connecting-nexus), [Connecting GitHub](/docs/connecting-github), and [Connecting SonarQube](/docs/connecting-sonarqube).
