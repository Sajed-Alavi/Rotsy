# Troubleshooting scans

A failed report always carries a reason. Start with the report — click the row in **Reports**, or `GET /api/scan/reports/{id}` — then match it below.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `no <scanner> vulnerability database on disk` | Preflight found no database | Run an update or offline import until both read **ready** |
| Repository shows as `unresolved` in registry discovery | No connector port on the repository, or the service account cannot read repository config | Set an HTTP/HTTPS connector port in Nexus; grant repository-admin read |
| `probe.reachable: false` on a discovered endpoint | Connector port not reachable from the backend container | Confirm Nexus listens on that port; check `extra_hosts` maps `host.docker.internal` |
| `unauthorized` / `denied` from a scanner | The Docker connector rejected the Nexus credentials | Confirm the account can pull from that repository. Even with *Force basic authentication* off, private content still needs a valid account |
| `no such host` | The registry host does not resolve inside the container | Use `host.docker.internal`, not `localhost` or `127.0.0.1`, in the Nexus URL — the registry host is derived from it |
| `x509` / certificate errors | HTTPS connector with a certificate the container does not trust | Prefer a plaintext connector on a trusted network, or install the CA in the image |
| Both scanners fail on one image, others fine | Manifest list with no `linux/amd64` entry | Expected — the scanners default to `linux/amd64` |

## A failure is never reported as a clean scan

Worth repeating, because it is the difference between a security tool and a decorative one: if the database is missing or the registry is unreachable, the scan **fails**. It does not return zero findings.

Zero findings and "could not check" look identical on a dashboard, and conflating them is how people ship vulnerable images believing they are clean.

## Five root causes that used to produce FAILED

Useful history if you are reading older reports, and useful context for why the system is arranged as it is.

1. **The scanners were pointed at an endpoint that does not exist.** The image reference was built from a hand-configured registry URL, falling back to `{nexus-host}:8081/{repo}/{image}` when blank. Nexus does not serve the v2 API there, so every scan 404'd. Fixed by [registry discovery](/docs/registry-discovery).
2. **TLS handling came from the wrong setting.** Plaintext-vs-TLS was driven by `NEXUS_VERIFY_SSL`, which describes the *REST* connection — so a plaintext connector behind an HTTPS Nexus was probed over TLS. Fixed by deriving the scheme per connector.
3. **The scanners updated their databases mid-scan.** Both do by default, and Grype outright refuses a database older than five days. On a restricted network that download fails and takes the scan with it. Fixed with `--skip-db-update` / `GRYPE_DB_AUTO_UPDATE=false`, plus a preflight that reports a missing database as such.
4. **Grype resolved images through container runtimes.** With no scheme it tries docker, podman and containerd first — none of which exist in the container, and none of which it should touch. Fixed with an explicit `registry:` reference. See [The static-only guarantee](/docs/static-only-guarantee).
5. **Failures were undiagnosable.** The reason was truncated to 500 characters and buried in a JSON blob. The reason, command, exit code and output tail are now persisted and shown.

Separately, component listing was unpaginated, so only the first page of any repository was ever considered. It pages properly now.

## Still stuck

Check **Background Jobs** for the job behind the scan — it carries the full progress log. Registry discovery state is in **Settings**, with a reachability probe per endpoint.
