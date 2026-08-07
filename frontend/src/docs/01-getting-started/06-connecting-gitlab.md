# Connecting GitLab

Rotsy connects to GitLab (gitlab.com or self-managed) with a **Personal Access Token**, not an App — GitLab has no equivalent of GitHub's App Manifest flow, so a token is the only credential it offers. **Settings → Integrations → GitLab** drives the whole thing; there's no environment variable to set for this, unlike the GitHub App.

## Two ways to connect

**Connect an account** — one token gives Rotsy access to every repository it can see. Good for a personal account or a single owner managing several projects. After connecting, **Sync Repositories** discovers everything the token can reach.

**Connect one repository** — its own independent token, scoped to exactly that repository, unrelated to any account-level connection. Use this when you want a narrower credential for a single project rather than one token covering everything.

Both need a token with **api** scope, generated on GitLab's own **User Settings → Access Tokens** page (or a project/group Access Token, if you want a bot credential instead of a personal one — see below).

## Webhooks are per-repository

Unlike GitHub's single App-level webhook, GitLab has no concept of an installation-wide webhook — Rotsy registers **one webhook per connected repository**, automatically, at connect time. If registration fails (the repository row shows no webhook), retry it from that repository's row without re-entering the token.

Two things commonly block automatic registration on a self-managed instance:

- **GitLab's own SSRF protection** rejects a webhook URL it considers a local/private address unless the instance explicitly allows it (Admin Area → Settings → Network → Outbound requests → "Allow requests to the local network from webhooks and integrations"). This is a GitLab-side setting Rotsy cannot change for you.
- **The token's role on the project must be at least Maintainer.** A token whose user (or bot user, for a project/group Access Token) only has Developer access can read the repository fine but GitLab refuses webhook creation with a 403 — bump the token's role on GitLab if this happens.

## Map a repository to a Project

Same as GitHub: open a Project's **Repositories** tab and pick from the discovered, unmapped GitLab repositories, or paste `namespace/repo` directly. Mapping is immediate.

## Community Edition and branches

SonarQube Community Edition doesn't support analyzing more than one branch under a single Sonar project — the `sonar.branch.name` parameter is rejected outright for anything but the default branch. Rotsy works around this transparently: analyzing a non-default branch auto-provisions its own, separate Sonar project (named after the branch) the first time it's analyzed. You never create these yourself; see [Automatic analysis and Smart Insights](/docs/automatic-analysis-and-insights).

## Troubleshooting

| Symptom | Cause |
|---|---|
| "This repository is already connected" | A GitLab repository can only be connected once (by full path), whether by account sync or direct connect |
| Push doesn't trigger analysis | Check the repository row for a registered webhook (retry registration if missing), and that its auto-analyze toggle is on and covers the pushed branch |
| Webhook registration fails | See the SSRF / token-role notes above — the error message states which |
| `401` reconnecting or syncing | The token is wrong, expired, or revoked — generate a fresh one on GitLab and reconnect; watch for stray whitespace if you're pasting it |
| Branch analysis fails with "Developer Edition or above is required" | Only possible if the branch workaround above didn't run — normal per-branch analysis on Community Edition goes through the auto-provisioned per-branch project instead |
