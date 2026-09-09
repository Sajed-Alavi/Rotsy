"""Health probes and correlation ids.

The health tests pin the property that motivated splitting the endpoints:
liveness must not depend on anything but the process itself, so a dependency
blip cannot get a working backend restarted.
"""

from __future__ import annotations

import logging

from app.core import correlation


# --- liveness / readiness --------------------------------------------------


async def test_liveness_needs_no_authentication(api):
    """A container healthcheck cannot hold a session cookie. Before the split
    every endpoint required one, which is why the backend had no healthcheck
    at all while Postgres and Redis both did."""
    resp = await api.client.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


async def test_liveness_ignores_dependencies(api):
    """Nothing is wired up in tests — no Nexus, no Redis, no app.state — and
    liveness must still answer 200. That is the whole contract."""
    resp = await api.client.get("/api/health/live")
    assert resp.status_code == 200


async def test_readiness_reports_database_and_redis(api):
    resp = await api.client.get("/api/health/ready")
    body = resp.json()
    assert resp.status_code == 200
    assert body["database"] is True
    # No cache is attached in tests, so Redis is reported down — and readiness
    # is still 200, because losing Redis degrades the app rather than
    # disqualifying it from serving.
    assert body["redis"] is False
    assert body["status"] == "ready"


async def test_detailed_health_still_requires_authentication(api):
    """Unchanged from before the split — the Dashboard's view stays gated."""
    resp = await api.client.get("/api/health")
    assert resp.status_code == 401


# --- correlation ids -------------------------------------------------------


async def test_every_response_carries_a_request_id(api):
    resp = await api.client.get("/api/health/live")
    assert resp.headers.get(correlation.HEADER_NAME)


async def test_an_inbound_request_id_is_honoured(api):
    """So a caller or reverse proxy can match its own logs to Rotsy's instead
    of each side inventing a different id for the same request."""
    resp = await api.client.get(
        "/api/health/live", headers={correlation.HEADER_NAME: "caller-supplied-id"},
    )
    assert resp.headers[correlation.HEADER_NAME] == "caller-supplied-id"


async def test_an_overlong_inbound_request_id_is_truncated(api):
    resp = await api.client.get(
        "/api/health/live", headers={correlation.HEADER_NAME: "x" * 500},
    )
    assert len(resp.headers[correlation.HEADER_NAME]) == 64


async def test_two_requests_get_different_ids(api):
    first = await api.client.get("/api/health/live")
    second = await api.client.get("/api/health/live")
    assert first.headers[correlation.HEADER_NAME] != second.headers[correlation.HEADER_NAME]


def test_the_log_filter_always_supplies_a_value():
    """The log format references ``correlation_id`` on every record, including
    records from libraries that never set one — a missing attribute would
    raise inside logging itself."""
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    correlation.set_correlation_id("")

    assert correlation.CorrelationIdFilter().filter(record) is True
    assert record.correlation_id == "-"


def test_the_log_filter_reports_the_current_id():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    correlation.set_correlation_id("job-abc123")

    correlation.CorrelationIdFilter().filter(record)
    assert record.correlation_id == "job-abc123"
    correlation.set_correlation_id("")


def test_job_ids_are_distinguishable_from_request_ids():
    assert correlation.new_correlation_id("job").startswith("job-")
    assert "-" not in correlation.new_correlation_id()
