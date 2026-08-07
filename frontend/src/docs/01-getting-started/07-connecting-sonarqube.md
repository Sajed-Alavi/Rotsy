# Connecting SonarQube

SonarQube is Rotsy's analysis engine — Rotsy creates the Sonar project, issues its analysis token, runs the scanner, and reads the results back. You should not need to open the SonarQube UI for day-to-day use, including for multi-branch projects on Community Edition (see below).

## Configure the connection

**Settings → Integrations → SonarQube → Configure**:

| Field | Notes |
|---|---|
| Server URL | The SonarQube base URL, e.g. `https://sonarqube.internal` |
| Token | An administrator token — needed to create projects and issue per-project analysis tokens |

Click **Test** before saving; it checks `/api/system/status` on the given server without persisting anything. The token is stored encrypted at rest (same mechanism as the Nexus password) and is never returned by the API once saved — the card only ever shows whether one is set.

## Version compatibility

Rotsy checks the SonarQube major version against a documented minimum (currently 9.x). Below that, the card shows a compatibility warning — it does not block the connection, since older instances may still work for basic analysis, but is a signal something might behave unexpectedly.

Rotsy does not attempt to upgrade SonarQube itself. **Check for Updates**, under **Code Quality → Settings**, checks whether a newer SonarQube release is available and shows the current vs. latest version — it only reports, and never applies an upgrade itself.

## Running analysis

There's no separate "connect a project" step to perform in advance. From **Code Quality → Overview**, pick a synced GitHub or GitLab repository and a branch, and click **Run Analysis**. The first analysis of a repository auto-detects its language and provisions the matching Sonar project automatically — nothing to create by hand in the SonarQube UI, and no separate API call.

Supported languages — anything that doesn't need a build step to analyze:

- Python
- JavaScript
- TypeScript
- Go
- PHP
- Ruby
- CSS
- HTML

Compiled or build-dependent languages (Java, C#, C++, ...) aren't supported — full analysis for those needs a build step Rotsy doesn't run. If a repository's dominant language isn't in the list above, analysis fails immediately with "not analyzable without a build step" rather than silently analyzing nothing.

### Branches on Community Edition

Community Edition rejects analyzing more than one branch under a single Sonar project outright. Rotsy works around this by giving each non-default branch its own Sonar project, auto-provisioned the first time that branch is analyzed — this applies uniformly whether you're analyzing one branch or a hundred, so it's never something to configure per-branch. See [Automatic analysis and Smart Insights](/docs/automatic-analysis-and-insights).

## Troubleshooting

| Symptom | Cause |
|---|---|
| "Not Configured" | No URL/token saved and no `SONAR_URL`/`SONAR_ADMIN_TOKEN` env fallback |
| "Error" health | Server unreachable, wrong token, or SonarQube itself not fully started — the card's error message states which |
| Analysis fails with "not analyzable without a build step" | The repository's dominant language isn't in the supported list above |
| Analysis stuck at "waiting for quality gate" | SonarQube's background compute-engine task is slow or stalled; Rotsy polls for up to 10 minutes before reporting a timeout |
| "Developer Edition or above is required" | Shouldn't happen through normal use — Rotsy routes non-default-branch analysis through a separate, auto-provisioned Sonar project specifically to avoid this Community Edition limitation |
