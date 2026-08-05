# Connecting GitHub

Rotsy connects to GitHub as a **GitHub App**, not a personal access token — narrower permissions, installable per organization or account, revocable without touching anyone's personal credentials.

## One-time: create the App

An operator creates the App once, at `github.com/settings/apps`, and sets four environment variables for this Rotsy instance:

| Variable | What it is |
|---|---|
| `GITHUB_APP_ID` | The App's numeric id |
| `GITHUB_APP_SLUG` | The App's URL slug, used to build the install link |
| `GITHUB_APP_PRIVATE_KEY` | The PEM private key generated for the App |
| `GITHUB_WEBHOOK_SECRET` | The secret configured on the App's webhook, used to verify deliveries |

Every project on this Rotsy instance shares this one App — there is no per-project GitHub configuration.

## Install the App

**Settings → Integrations → GitHub** shows whether the App is configured and how many installations exist. Click **Install GitHub App** to pick an organization or account and grant it access to the repositories you want analyzed.

## Discover and map a repository

Once installed, Rotsy can see every repository the installation was granted (`GET /api/modules/github/repositories?unmapped=true`). Create a Project from the **Projects** page, then map a repository to it: `POST /api/modules/github/repositories/{repo_id}/map` with `{"project_id": ...}`. A dedicated repository-picker in the Project page is planned; today this last step is an API call.

## What happens next

Every push to the repository's default branch fires a webhook, verified against `GITHUB_WEBHOOK_SECRET`, deduplicated against replayed deliveries, and queued for analysis automatically. See [Automatic analysis and Smart Insights](/docs/automatic-analysis-and-insights).

## Troubleshooting

| Symptom | Cause |
|---|---|
| "Not Configured" on the GitHub card | One of the four env vars above is missing |
| "Configured — No Installations" | The App exists but hasn't been installed on any org/account yet — click Install |
| Analysis never triggers on push | Confirm the repository is mapped to a Project *and* that Project has a SonarQube project connected — an unmapped push is silently ignored, not an error |
| Commit status never appears on GitHub | The App's installation token may have expired or been revoked — check the backend logs for the analysis job |
