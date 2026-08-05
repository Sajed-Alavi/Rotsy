"""Thin client over the SonarQube Web API.

Same shape as ``app.modules.nexus.connector.NexusClient`` — one small class
wrapping the handful of endpoints Rotsy actually needs, not a general Sonar
SDK. Auth is HTTP Basic with the admin token as the username and an empty
password, which is how Sonar's Web API accepts a token.
"""

from __future__ import annotations

import httpx

from ...config import Settings


class SonarError(Exception):
    pass


class SonarClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.SONAR_URL or not settings.SONAR_ADMIN_TOKEN:
            raise SonarError("SONAR_URL / SONAR_ADMIN_TOKEN are not configured")
        self._base_url = settings.SONAR_URL.rstrip("/")
        self._auth = (settings.SONAR_ADMIN_TOKEN, "")

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, auth=self._auth, timeout=20.0)

    async def server_status(self) -> dict:
        """``/api/system/status`` — id, version, and startup status (UP when ready).

        Used for the Settings -> Integrations health check; deliberately the
        cheapest possible call (no auth required by Sonar for this endpoint,
        though we send it anyway since the client always carries it).
        """
        async with await self._client() as client:
            resp = await client.get("/api/system/status")
        if resp.status_code >= 400:
            raise SonarError(f"system status failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    async def project_exists(self, project_key: str) -> bool:
        async with await self._client() as client:
            resp = await client.get("/api/projects/search", params={"projects": project_key})
        if resp.status_code >= 400:
            raise SonarError(f"project search failed: {resp.status_code} {resp.text[:300]}")
        return bool(resp.json().get("components"))

    async def ensure_project(self, project_key: str, name: str) -> None:
        """Idempotent: create the Sonar project if it doesn't already exist."""
        if await self.project_exists(project_key):
            return
        async with await self._client() as client:
            resp = await client.post(
                "/api/projects/create", params={"project": project_key, "name": name}
            )
        if resp.status_code >= 400:
            raise SonarError(f"project create failed: {resp.status_code} {resp.text[:300]}")

    async def issue_analysis_token(self, project_key: str) -> str:
        """A project-scoped token for sonar-scanner to authenticate with.

        Sonar tokens can't be re-fetched once issued, and re-issuing the same
        named token first requires revoking it — simplest correct approach for
        one-token-per-project is: revoke if present, then generate fresh.
        """
        token_name = f"rotsy-{project_key}"
        async with await self._client() as client:
            await client.post("/api/user_tokens/revoke", params={"name": token_name})
            resp = await client.post(
                "/api/user_tokens/generate",
                params={"name": token_name, "projectKey": project_key, "type": "PROJECT_ANALYSIS_TOKEN"},
            )
        if resp.status_code >= 400:
            raise SonarError(f"token generate failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()["token"]

    async def task_status(self, task_id: str) -> str:
        """Status of the async compute-engine task a scan produced.

        One of PENDING | IN_PROGRESS | SUCCESS | FAILED | CANCELED.
        """
        async with await self._client() as client:
            resp = await client.get("/api/ce/task", params={"id": task_id})
        if resp.status_code >= 400:
            raise SonarError(f"task status failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()["task"]["status"]

    async def quality_gate(self, project_key: str) -> dict:
        async with await self._client() as client:
            resp = await client.get(
                "/api/qualitygates/project_status", params={"projectKey": project_key}
            )
        if resp.status_code >= 400:
            raise SonarError(f"quality gate fetch failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()["projectStatus"]

    async def measures(self, project_key: str, metric_keys: list[str]) -> dict[str, float]:
        async with await self._client() as client:
            resp = await client.get(
                "/api/measures/component",
                params={"component": project_key, "metricKeys": ",".join(metric_keys)},
            )
        if resp.status_code >= 400:
            raise SonarError(f"measures fetch failed: {resp.status_code} {resp.text[:300]}")
        out: dict[str, float] = {}
        for m in resp.json().get("component", {}).get("measures", []):
            try:
                out[m["metric"]] = float(m["value"])
            except (KeyError, ValueError):
                continue
        return out
