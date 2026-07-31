# Workflow: air-gapped deployment

End to end, for a network with no outbound internet access.

## 1. Prepare on a connected machine

```bash
git clone <repo> && cd nexus-project
./scripts/scanner/fetch-offline-db.sh
```

You now have the Trivy and Grype database archives.

Also pull and export the container images the stack needs, since the restricted host cannot fetch them either:

```bash
docker compose pull
docker save postgres:16.6-alpine redis:7.4.1-alpine -o infra-images.tar
```

Build the backend and frontend images here too, and export them the same way.

## 2. Transfer

Move to the restricted host:

- the repository
- `infra-images.tar` and your built application images
- the database archives

## 3. Load and configure

```bash
docker load -i infra-images.tar
cp .env.example .env
```

Edit `.env`. Generate real secrets with `openssl rand -hex 32` — the app refuses to start on placeholders. Set `SCANNER_DB_OFFLINE_MODE=true` so the *scheduled* refresh imports rather than attempting a download that cannot succeed.

## 4. Place the databases

Copy the archives into `./offline-db/`. Expected filenames are in [Offline and air-gapped installs](/docs/offline-airgapped).

## 5. Start and import

```bash
docker compose up -d
```

Sign in, configure the Nexus connection, then go to **Vulnerability Scanning → Database Management** and click **Import offline DBs**. The page lists what it found in the directory, so you can confirm the files are visible to the container before importing.

## 6. Verify

Both scanners should read `ready`. Enable a repository under Targets and scan one image manually to confirm the whole path works.

## Keeping current afterwards

Vulnerability databases go stale in days, not months. Establish a routine — re-run `fetch-offline-db.sh` on the connected machine, transfer, import. An air-gapped scanner running a six-month-old database is providing false assurance rather than security.
