"""Thin client over the SonarQube Web API.

Same shape as ``app.modules.nexus.connector.NexusClient`` — one small class
wrapping the handful of endpoints Rotsy actually needs, not a general Sonar
SDK. Auth is HTTP Basic with the admin token as the username and an empty
password, which is how Sonar's Web API accepts a token.

Takes an explicit ``url``/``token`` pair rather than ``Settings`` directly, so
callers can build one from either the dashboard-managed connection
(:func:`app.core.config_store.get_sonar_connection`, DB-first with env
fallback) or from candidate credentials that haven't been saved yet (the
"Test Connection" flow) — same shape as how the Nexus test-connection
endpoint validates before saving.
"""

from __future__ import annotations

import httpx


class SonarError(Exception):
    pass


class SonarClient:
    def __init__(self, url: str, token: str) -> None:
        if not url or not token:
            raise SonarError("SonarQube URL and token are required")
        self._base_url = url.rstrip("/")
        self._auth = (token, "")

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Every Sonar call goes through here so exactly one place turns a
        failure into :class:`SonarError` — including *transport*-level
        failures (connection refused, DNS failure, timeout), which
        ``httpx`` raises as its own exception types, not as an HTTP status.
        Every call site used to check ``resp.status_code >= 400`` only,
        which left those transport errors as raw, unhandled exceptions that
        escaped every ``except SonarError`` in the routers above as a bare
        500 — Sonar being unreachable is the single most likely failure mode
        here, and it was exactly the one case none of this code actually caught.
        """
        try:
            async with httpx.AsyncClient(base_url=self._base_url, auth=self._auth, timeout=20.0) as client:
                resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SonarError(f"Unable to reach SonarQube at {self._base_url}: {exc}") from exc
        if resp.status_code >= 400:
            raise SonarError(f"{method} {path} failed: {resp.status_code} {resp.text[:300]}")
        return resp

    async def server_status(self) -> dict:
        """``/api/system/status`` — id, version, and startup status (UP when ready).

        Used for the Settings -> Integrations health check; deliberately the
        cheapest possible call (no auth required by Sonar for this endpoint,
        though we send it anyway since the client always carries it).
        """
        resp = await self._request("GET", "/api/system/status")
        return resp.json()

    async def check_upgrades(self) -> list[dict]:
        """``/api/system/upgrades`` — SonarQube versions newer than the
        running one, per Sonar's own update center. Read-only: this reports
        what's available, it does not install anything. Rotsy does not
        control the underlying Sonar deployment (container, VM, or bare
        install all look the same from here), so an in-place upgrade is
        outside what this can safely automate — this is the "guided update
        information" the Settings card shows instead of pretending to
        perform one.
        """
        resp = await self._request("GET", "/api/system/upgrades")
        return resp.json().get("upgrades", [])

    async def project_exists(self, project_key: str) -> bool:
        resp = await self._request("GET", "/api/projects/search", params={"projects": project_key})
        return bool(resp.json().get("components"))

    async def ensure_project(self, project_key: str, name: str) -> None:
        """Idempotent: create the Sonar project if it doesn't already exist."""
        if await self.project_exists(project_key):
            return
        await self._request("POST", "/api/projects/create", params={"project": project_key, "name": name})

    async def issue_analysis_token(self, project_key: str) -> str:
        """A project-scoped token for sonar-scanner to authenticate with.

        Sonar tokens can't be re-fetched once issued, and re-issuing the same
        named token first requires revoking it — simplest correct approach for
        one-token-per-project is: revoke if present, then generate fresh.
        """
        token_name = f"rotsy-{project_key}"
        # Revoking a token that doesn't exist is a no-op on Sonar's side, not
        # an error — no need to check whether one already exists first.
        await self._request("POST", "/api/user_tokens/revoke", params={"name": token_name})
        resp = await self._request(
            "POST", "/api/user_tokens/generate",
            params={"name": token_name, "projectKey": project_key, "type": "PROJECT_ANALYSIS_TOKEN"},
        )
        return resp.json()["token"]

    async def task_status(self, task_id: str) -> str:
        """Status of the async compute-engine task a scan produced.

        One of PENDING | IN_PROGRESS | SUCCESS | FAILED | CANCELED.
        """
        resp = await self._request("GET", "/api/ce/task", params={"id": task_id})
        return resp.json()["task"]["status"]

    async def quality_gate(self, project_key: str) -> dict:
        resp = await self._request("GET", "/api/qualitygates/project_status", params={"projectKey": project_key})
        return resp.json()["projectStatus"]

    async def list_quality_gates(self) -> list[dict]:
        """Every gate defined on the Sonar instance — including ones the
        operator created or edited directly in SonarQube's own UI. Rotsy
        never assumes it's the only thing managing gates; this is how the
        "connect a project" flow lets the operator pick one instead of being
        stuck with whatever Rotsy would create by default."""
        resp = await self._request("GET", "/api/qualitygates/list")
        return resp.json().get("qualitygates", [])

    async def get_quality_gate_by_name(self, name: str) -> dict | None:
        for gate in await self.list_quality_gates():
            if gate.get("name") == name:
                return gate
        return None

    async def create_quality_gate(self, name: str) -> int:
        resp = await self._request("POST", "/api/qualitygates/create", params={"name": name})
        return resp.json()["id"]

    async def add_quality_gate_condition(self, gate_name: str, metric: str, op: str, error_threshold: str) -> None:
        await self._request(
            "POST", "/api/qualitygates/create_condition",
            params={"gateName": gate_name, "metric": metric, "op": op, "error": error_threshold},
        )

    async def get_quality_gate_conditions(self, gate_name: str) -> list[dict]:
        """Every condition currently on a gate, by metric — including ones
        SonarQube itself added. As of the CAYC ("Clean as You Code") push,
        Sonar auto-populates ``POST /api/qualitygates/create`` with its own
        conditions (``new_violations>0``, ``new_security_hotspots_reviewed<100``,
        ``new_duplicated_lines_density>3``, ``new_coverage<80``) regardless of
        what the caller asked for — this is what :func:`ensure_quality_gate`
        reconciles against, not just "does a gate with this name exist"."""
        resp = await self._request("GET", "/api/qualitygates/show", params={"name": gate_name})
        return resp.json().get("conditions", [])

    async def update_quality_gate_condition(self, condition_id: str, metric: str, op: str, error_threshold: str) -> None:
        await self._request(
            "POST", "/api/qualitygates/update_condition",
            params={"id": condition_id, "metric": metric, "op": op, "error": error_threshold},
        )

    async def delete_quality_gate_condition(self, condition_id: str) -> None:
        await self._request("POST", "/api/qualitygates/delete_condition", params={"id": condition_id})

    async def assign_quality_gate(self, gate_name: str, project_key: str) -> None:
        await self._request("POST", "/api/qualitygates/select", params={"gateName": gate_name, "projectKey": project_key})

    async def measures(self, project_key: str, metric_keys: list[str]) -> dict[str, float]:
        resp = await self._request(
            "GET", "/api/measures/component",
            params={"component": project_key, "metricKeys": ",".join(metric_keys)},
        )
        out: dict[str, float] = {}
        for m in resp.json().get("component", {}).get("measures", []):
            try:
                out[m["metric"]] = float(m["value"])
            except (KeyError, ValueError):
                continue
        return out

    # -------------------------------------------------------------------
    # Findings — issues (bugs/vulnerabilities/code smells) and hotspots.
    # Both endpoints are paginated (Sonar's own max page size is 500); a page
    # cap bounds the worst case (a project with tens of thousands of issues)
    # to a handful of requests instead of an unbounded loop, matching the
    # existing UI/export page-size caps in ``schemas/scan.py``.
    # -------------------------------------------------------------------
    _FINDINGS_PAGE_SIZE = 500
    _FINDINGS_PAGE_CAP = 10  # 10 * 500 = 5000 findings max per fetch

    async def issues(self, project_key: str) -> list[dict]:
        """Every open issue (bug/vulnerability/code smell) for a project's
        latest analysis — ``/api/issues/search`` filtered to the current
        branch's default, unresolved issues (resolved/closed issues describe
        history Rotsy doesn't track per commit, see :class:`app.models.sonar.SonarIssue`).
        """
        return await self._paginated(
            "/api/issues/search",
            {"componentKeys": project_key, "resolved": "false"},
            "issues",
        )

    async def hotspots(self, project_key: str) -> list[dict]:
        """Every security hotspot for a project's latest analysis —
        ``/api/hotspots/search``."""
        return await self._paginated(
            "/api/hotspots/search",
            {"projectKey": project_key},
            "hotspots",
        )

    async def _paginated(self, path: str, params: dict, items_key: str) -> list[dict]:
        items: list[dict] = []
        for page in range(1, self._FINDINGS_PAGE_CAP + 1):
            resp = await self._request(
                "GET", path,
                params={**params, "ps": self._FINDINGS_PAGE_SIZE, "p": page},
            )
            body = resp.json()
            batch = body.get(items_key, [])
            items.extend(batch)
            paging = body.get("paging", {})
            total = paging.get("total", len(items))
            if len(items) >= total or len(batch) < self._FINDINGS_PAGE_SIZE:
                break
        return items
