"""Vulnerability-database status parsing.

Pure functions from ``app.modules.nexus.db.status`` — no scanner binary and
no cache directory needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.nexus.db import Readiness
from app.modules.nexus.db.status import as_datetime, dir_size, parse_iso


def test_parse_iso_normalises_z_suffix():
    assert parse_iso("2026-07-24T00:34:54Z").startswith("2026-07-24T00:34:54")


def test_parse_iso_rejects_go_zero_time():
    """Trivy writes 0001-01-01T00:00:00Z for unset fields such as DownloadedAt."""
    assert parse_iso("0001-01-01T00:00:00Z") is None


def test_parse_iso_handles_empty_and_junk():
    assert parse_iso("") is None
    assert parse_iso(None) is None
    assert parse_iso("not a date") == "not a date"


def test_as_datetime_assumes_utc_when_naive():
    dt = as_datetime("2026-07-24T00:34:54")
    assert dt.tzinfo == timezone.utc


def test_as_datetime_preserves_offset():
    dt = as_datetime("2026-07-24T00:34:54+02:00")
    assert dt.utcoffset() == timedelta(hours=2)


def test_as_datetime_on_junk_returns_none():
    assert as_datetime("nope") is None
    assert as_datetime(None) is None


def test_dir_size_of_missing_path_is_zero(tmp_path):
    assert dir_size(tmp_path / "does-not-exist") == 0


def test_dir_size_sums_nested_files(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.bin").write_bytes(b"x" * 10)
    (tmp_path / "sub" / "b.bin").write_bytes(b"y" * 5)
    assert dir_size(tmp_path) == 15


def test_readiness_serialises_for_the_api():
    built = datetime.now(timezone.utc).isoformat()
    payload = Readiness("trivy", True, "", stale=False, built=built).to_json()
    assert payload == {
        "scanner": "trivy", "ready": True, "reason": "", "stale": False, "built": built,
    }
