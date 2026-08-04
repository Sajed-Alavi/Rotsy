# Workflow: CI/CD integration

Gating a pipeline on scan results.

## 1. Issue a scoped token

**Access & Webhooks → API Tokens → New token**, scoped to `scan:read` and `scan:execute`. Store it as a masked CI variable.

Scope it deliberately. A token that can only read and trigger scans cannot delete a repository if the runner is compromised.

## 2. Push, then wait for the scan

If the Nexus webhook is configured, the push itself triggers the scan — you do not need to ask. Poll for the result:

```bash
TOKEN="$ROTSY_TOKEN"
HOST="https://rotsy.example.com"

# Optional: trigger explicitly rather than relying on the webhook
curl -sf -X POST "$HOST/api/scan/image" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repo":"docker-hosted","image":"myapp:'"$CI_COMMIT_SHA"'"}'
```

## 3. Gate on the result

```bash
for i in $(seq 1 60); do
  BODY=$(curl -sf "$HOST/api/scan/reports?limit=20" -H "Authorization: Bearer $TOKEN")
  STATUS=$(echo "$BODY" | jq -r --arg img "myapp:$CI_COMMIT_SHA" \
    '[.[] | select(.image==$img)] | first | .status // "pending"')
  [ "$STATUS" = "success" ] && break
  [ "$STATUS" = "failed" ] && { echo "scan failed"; exit 1; }
  sleep 10
done

CRIT=$(echo "$BODY" | jq -r --arg img "myapp:$CI_COMMIT_SHA" \
  '[.[] | select(.image==$img)] | map(.critical) | add // 0')

if [ "$CRIT" -gt 0 ]; then
  echo "$CRIT critical vulnerabilities — failing the build"
  exit 1
fi
```

## Design notes

**Treat a failed scan as a failed build, not a pass.** The example exits non-zero on `failed`. A scan that could not run tells you nothing about the image, and defaulting to "proceed" is how vulnerable images ship.

**Give it a real timeout.** A first scan of a large image takes minutes. The loop above allows ten.

**Consider gating on critical only.** Gating on high as well is defensible but will block on findings with no available fix. Start strict on critical, and use the Findings view for the rest.
