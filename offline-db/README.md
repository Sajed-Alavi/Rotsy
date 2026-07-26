# Offline scanner DB archives

On a restricted / air-gapped network (Docker Hub, ghcr.io, github.com blocked)
Trivy and Grype cannot download their vulnerability databases at runtime. Put
the **pre-downloaded** archives here; the backend imports them with **no
network access**.

This folder is mounted into the backend container read-only at
`/app/offline-db` (see `docker-compose.yml`). Drop files here on the host, then
trigger the import from the UI ("Import offline DBs" on the Scanning page) or:

```bash
curl -X POST http://localhost:8000/api/scan/db-import --cookie "access_token=..."
```

Check what the backend has detected:

```bash
curl http://localhost:8000/api/scan/db-offline --cookie "access_token=..."
```

## Expected filenames (case-insensitive)

| Scanner | Required file | Optional |
|---|---|---|
| Trivy | `db.tar.gz` (or `trivy-db.tar.gz`) | `javadb.tar.gz` (or `trivy-java-db.tar.gz`) |
| Grype | `grype-db.tar.gz` / `grype-db.tar.zst` (or `vulnerability-*.tar.*`) | — |

## How to obtain the archives (on a machine WITH internet)

Use the helper script `../scripts/fetch-offline-db.sh`, or manually:

### Trivy DB (~50 MB)
```bash
# needs oras: https://oras.land
oras pull registry-1.docker.io/aquasec/trivy-db:2 --output .
# -> produces db.tar.gz   (copy it into this folder)

# optional Java DB (~30 MB)
oras pull ghcr.io/aquasecurity/trivy-java-db:1 --output .
# -> produces javadb.tar.gz
```

### Grype DB (~150 MB)
```bash
# The listing tells you the latest archive URL for your grype schema.
curl -s https://grype.anchore.io/databases/listing.json | less
# Download the newest "url" for schema v5, save it as grype-db.tar.gz here.
# grype validates the checksum on import, so grab the matching entry.
```

Then copy `db.tar.gz`, `javadb.tar.gz`, `grype-db.tar.gz` into this folder and
run the import.

> Files in this folder (except this README) are git-ignored — they're large
> binaries, not source.
