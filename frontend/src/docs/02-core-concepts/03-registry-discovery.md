# Registry discovery

Nexus does not serve the Docker Registry v2 API on its main port. Every Docker repository gets its own connector port, and that port is part of the repository's configuration:

```
nexus REST API           http://nexus-host:8081
docker repo "team-a"     http://nexus-host:15987/v2/...
docker repo "team-b"     http://nexus-host:15988/v2/...
```

Most tools make you configure this by hand. That means a list of ports duplicated outside Nexus, going stale every time someone adds a repository.

## How it actually works

Nexus is the authority on those ports, so Sharpy asks it:

1. `GET /service/rest/v1/repositorySettings` — one call, full configuration for every repository including the `docker` connector block.
2. `GET /service/rest/v1/repositories/docker/{type}/{name}` — per-repository fallback when the first is unavailable.

The **host** comes from the live Nexus base URL, since connectors listen on the same interface. The **scheme** comes from the connector the repository declares: an `httpsPort` means TLS, an `httpPort` means plaintext.

Results are cached for 120 seconds. A scan targeting an unknown repository forces an immediate re-probe, so a repository created seconds ago is scannable at once.

## There is nothing to configure

No `DOCKER_REGISTRY_URL`, no registry field in the UI. **Settings → Registry discovery** shows a read-only table of what was found, with a reachability check per endpoint, plus an explicit list of any Docker repository that could not be resolved and why.

## The one requirement

The Nexus service account needs **repository-admin read** privileges. Without it Nexus returns repositories but omits the connector block, and discovery reports every Docker repository as unresolved with that exact reason. `nx-admin` covers it.

If scanning "cannot find the registry", this is almost always why.
