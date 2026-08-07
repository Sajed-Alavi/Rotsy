# Connecting GitHub

Rotsy connects to GitHub as a **GitHub App**, not a personal access token — narrower permissions, installable per organization or account, revocable without touching anyone's personal credentials. Nothing about this is configured by hand: **Settings → Integrations → GitHub** drives all of it.

## Create the App

Click **Connect to GitHub**. This walks GitHub's own **App Manifest** flow: Rotsy generates the manifest, you confirm it on GitHub, GitHub creates the App and redirects back here with its credentials — App id, private key, webhook secret — which Rotsy saves automatically. There are no environment variables to set by hand and nothing to paste in; the only manual step is GitHub's own confirmation page.

One App is shared by every Project on this Rotsy instance — there is no per-project GitHub configuration.

## Two ways to connect a repository

**Install the App** on an organization or account, then **Sync Repositories** — this discovers every repository the installation was granted. Push to any of them fires a webhook automatically, so analysis and commit-status updates just work with no further setup. This is the normal path for repositories you own or administer.

**Connect a public repository by URL** (same page, further down) — paste `owner/repo` and a Project id, no App installation needed. Use this for a repository you don't own or administer. Trade-off: GitHub only delivers push events to repositories the App is installed on, so this repository won't auto-analyze on push — only when you click **Run Analysis**.

Either way, connecting a repository is a couple of clicks in the UI — never a raw API call.

## Map a repository to a Project

Repositories discovered through an installation aren't attached to a Project yet. Open a Project's **Repositories** tab and pick from the list of unmapped GitHub (and GitLab) repositories, or paste a repository name directly — mapping is immediate and works the same whether you're connecting one repository or a hundred.

## What happens next

Every push to a mapped, App-installed repository's watched branch(es) fires a webhook, verified against the App's webhook secret, deduplicated against replayed deliveries, and queued for analysis automatically. See [Automatic analysis and Smart Insights](/docs/automatic-analysis-and-insights).

## Troubleshooting

| Symptom | Cause |
|---|---|
| "Not Configured" on the GitHub card | The App hasn't been created yet — click Connect to GitHub |
| "Configured — No Installations" | The App exists but hasn't been installed on any org/account yet — click Install GitHub App |
| Analysis never triggers on push | Confirm the repository is mapped to a Project *and* that its auto-analyze toggle (Project → Repositories tab, per-row) is on and covers the pushed branch — a repository connected by URL (no App installation) never auto-triggers, only Run Analysis does |
| Commit status never appears on GitHub | The App's installation token may have expired or been revoked — check the backend logs for the analysis job |
