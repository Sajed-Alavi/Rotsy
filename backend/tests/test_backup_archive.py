"""Backup archiver regression tests.

CRIT-03 — path traversal guards on asset paths and repository directory names.
LOW-03  — run ids carry entropy, so two runs starting in the same second no
          longer share a directory and corrupt each other's output.
LOW-04  — free-space checks are bounded by bytes written as well as by asset
          count, so a burst of large blobs cannot outrun the periodic check.
"""

from __future__ import annotations

import re

import pytest

from app.services import backup_archive
from app.services.backup_archive import InvalidRepositoryName, _new_run_id, _safe_relpath, safe_repo_dirname


def test_safe_relpath_strips_traversal_segments():
    assert str(_safe_relpath("/../../etc/passwd")) == "etc/passwd"


def test_safe_relpath_normal_path():
    assert str(_safe_relpath("/foo/bar.txt")) == "foo/bar.txt"


def test_safe_relpath_empty_falls_back_to_placeholder():
    assert str(_safe_relpath("/../..")) == "_"


def test_safe_repo_dirname_accepts_normal_name():
    assert safe_repo_dirname("my-repo") == "my-repo"


@pytest.mark.parametrize(
    "bad_repo",
    ["/etc", "../../etc", "..", ".", "a/b", "", "a\\b", "/"],
)
def test_safe_repo_dirname_rejects_unsafe_names(bad_repo):
    with pytest.raises(InvalidRepositoryName):
        safe_repo_dirname(bad_repo)


# --- LOW-03: run id uniqueness ----------------------------------------------
def test_run_id_keeps_sortable_timestamp_prefix():
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", _new_run_id())


def test_run_ids_generated_together_are_distinct():
    """Same-second runs previously collided on an identical timestamp-only id,
    sharing a run_dir and interleaving writes into one corrupt manifest."""
    ids = {_new_run_id() for _ in range(200)}
    assert len(ids) == 200


# --- LOW-04: disk check thresholds ------------------------------------------
def test_disk_check_has_a_byte_bound_as_well_as_a_count_bound():
    """The asset-count bound alone let 50 large layer blobs fill the volume
    inside a single check window."""
    assert backup_archive._DISK_CHECK_EVERY > 0
    assert backup_archive._DISK_CHECK_EVERY_BYTES > 0


def test_ensure_disk_space_raises_below_threshold(tmp_path):
    import shutil
    free = shutil.disk_usage(tmp_path).free
    with pytest.raises(RuntimeError):
        backup_archive._ensure_disk_space(tmp_path, free + 1)


def test_ensure_disk_space_passes_above_threshold(tmp_path):
    backup_archive._ensure_disk_space(tmp_path, 1)
