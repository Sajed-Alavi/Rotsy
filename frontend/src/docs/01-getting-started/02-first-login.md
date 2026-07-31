# Your first login

## Signing in

Navigate to the dashboard and sign in with the bootstrap administrator account created on first startup. Its username and password come from `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD` in the deployment's `.env`.

The app refuses to start with a placeholder or weak bootstrap password, so if you can reach the login page at all, a real password was set.

## Sessions

Authentication uses two httpOnly cookies — a short-lived access token and a longer-lived refresh token. They are httpOnly deliberately: JavaScript cannot read them, so a cross-site scripting bug cannot steal your session.

That also means **a browser session is not usable from a script**. For automation, issue an API token instead — see [Tokens and webhooks](/docs/tokens-and-webhooks).

> If you are deploying behind TLS, `COOKIE_SECURE` must be `true`. The app refuses to boot if it is `false` while your frontend origin is `https://`, because that combination is almost always a mistake rather than deliberate local testing.

## Change the bootstrap password

Go to **Settings → Password** and change it. The bootstrap credentials are in a file on disk and in your deployment history; treat them as temporary.

## What you see first

The **Dashboard** summarises health, repository counts, critical and high CVE totals, blobstore usage and recent jobs. Every tile links to the section that owns it.

If the Nexus connection is not configured yet, most tiles will be empty. That is the next step.

## Next

[Connecting to Nexus](/docs/connecting-nexus).
