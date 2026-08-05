# Connecting SonarQube

SonarQube is Rotsy's analysis engine — Rotsy creates the Sonar project, issues its analysis token, runs the scanner, and reads the results back. You should not need to open the SonarQube UI for day-to-day use.

## Configure the connection

**Settings → Integrations → SonarQube → Configure**:

| Field | Notes |
|---|---|
| Server URL | The SonarQube base URL, e.g. `https://sonarqube.internal` |
| Token | An administrator token — needed to create projects and issue per-project analysis tokens |

Click **Test** before saving; it checks `/api/system/status` on the given server without persisting anything. The token is stored encrypted at rest (same mechanism as the Nexus password) and is never returned by the API once saved — the card only ever shows whether one is set.

## Version compatibility

Rotsy checks the SonarQube major version against a documented minimum (currently 9.x). Below that, the card shows a compatibility warning — it does not block the connection, since older instances may still work for basic analysis, but is a signal something might behave unexpectedly.

Rotsy does not attempt to upgrade SonarQube itself. If a newer version is available, that is left to you and your deployment.

## Connect a project

SonarQube analysis is opt-in per Project and limited to languages that don't need a build step:

- Python
- JavaScript
- TypeScript

`POST /api/modules/sonar/projects` with `{"project_id": ..., "language": "python"}` creates the matching Sonar project automatically and connects it. There's no separate step in the SonarQube UI.

Compiled or build-dependent languages (Java, Go, C#, ...) are not supported yet — coverage and full analysis for those needs a build step Rotsy doesn't run.

## Troubleshooting

| Symptom | Cause |
|---|---|
| "Not Configured" | No URL/token saved and no `SONAR_URL`/`SONAR_ADMIN_TOKEN` env fallback |
| "Error" health | Server unreachable, wrong token, or SonarQube itself not fully started — the card's error message states which |
| Analysis fails with "not analyzable without a build step" | The project's language isn't Python/JS/TS — see the language list above |
| Analysis stuck at "waiting for quality gate" | SonarQube's background compute-engine task is slow or stalled; Rotsy polls for up to 10 minutes before reporting a timeout |
