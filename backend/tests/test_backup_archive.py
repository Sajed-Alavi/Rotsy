"""CRIT-03 regression tests: path traversal guards in the backup archiver."""

from __future__ import annotations

import pytest

from app.services.backup_archive import InvalidRepositoryName, _safe_relpath, safe_repo_dirname


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
