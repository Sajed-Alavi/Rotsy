# What is Sharpy?

Sharpy is a management console and API around **Sonatype Nexus Repository Manager**, with static container-image vulnerability scanning as its centrepiece.

Nexus is very good at storing artifacts. It is less good at answering the questions you actually have day to day: *which images do I have?*, *which of them have critical CVEs?*, *what is eating my disk?*, *who can read this repository?* Sharpy sits in front of Nexus and answers those.

## What it adds

| Capability | What it means |
|---|---|
| Vulnerability scanning | Trivy and Grype scan every pushed image and store the findings |
| Image browsing | See images and tags, not a flat wall of layer blobs |
| Storage analysis | Find what is consuming space, per repository |
| Retention | Policy-driven cleanup of old tags |
| Metrics and alerts | Track growth over time, fire a webhook on thresholds |
| RBAC | Users, roles and per-image scoping, independent of Nexus's own |
| Audit trail | Who changed what, and when |

## Three rules that shape everything

These are not aspirations. Each one is enforced in code.

**1. Static analysis only.** No container is ever started, run, or spun up. Images are read over the Docker Registry v2 API and analysed as data. There is no Docker socket mounted and no Docker client in the image. See [The static-only guarantee](/docs/static-only-guarantee).

**2. Zero client-side registry configuration.** Nexus serves each Docker repository on its own connector port. Sharpy asks Nexus what those ports are rather than making you configure them. There is no `DOCKER_REGISTRY_URL` setting. See [Registry discovery](/docs/registry-discovery).

**3. Event-driven scanning.** An image is scanned when it is pushed, or when you ask. Never on startup, never on a schedule, never twice by accident. See [How scanning is triggered](/docs/scanning-on-push).

## What it is not

Sharpy does not replace Nexus and does not proxy artifact traffic. Your clients still `docker pull` from Nexus directly. Sharpy reads Nexus's REST and registry APIs and keeps its own database of scan results, metrics and policy.

It also does not do runtime security. It tells you what vulnerabilities are *in* an image; it has no view of what a running container is doing.

## Next

Start with [Your first login](/docs/first-login).
