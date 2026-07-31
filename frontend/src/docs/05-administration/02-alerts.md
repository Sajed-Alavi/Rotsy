# Alerts

Threshold rules evaluated after each metric collection.

## Creating a rule

**Alerts → New rule**. A rule has a metric, a condition (`>`, `<`, `==`), a threshold, an optional repository filter, and an optional webhook URL.

Available metrics:

| Metric | Meaning |
|---|---|
| `storage.total` | Repository total bytes |
| `storage.asset_count` | Number of assets in a repository |
| `blobstore.used_pct` | Blobstore disk usage percentage |

`repo_filter` is a SQL `LIKE` pattern; null or `%` matches everything. For `blobstore.used_pct` it matches blobstore names rather than repository names.

## Webhooks are optional

A rule with no webhook still evaluates and still updates its last-triggered time, so firing history is meaningful before you have wired up a destination. It just skips delivery.

## Destination validation

Webhook URLs must be `http` or `https`, and loopback, private (RFC1918), link-local and cloud-metadata addresses are refused by default.

This is not paranoia about your intentions — the backend frequently has network access that the person creating the rule does not, so an unvalidated destination turns a convenience feature into a server-side request-forgery tool. Set `OUTBOUND_ALLOWED_HOSTS` to allow specific internal destinations for legitimate on-prem use.

Validation happens both when the rule is saved and when it fires.

## Deleting

Rules can be deleted from the same page.

## Where alerts do not help

Alerts evaluate metrics, not scan results. There is no "alert me on a new critical CVE" rule today. For that, poll `GET /api/scan/summary` with an [API token](/docs/tokens-and-webhooks) from whatever system you already use for notifications.
