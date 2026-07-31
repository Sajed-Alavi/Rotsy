"""CRIT-01 regression tests: path traversal guard on the asset download proxy.

Exercises the extracted validation helper directly rather than the full
FastAPI endpoint, since the endpoint's remaining work (streaming from Nexus)
needs a live Nexus connection this test suite doesn't have.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers.repositories import _validated_repository_path


def test_normal_path_allowed():
    assert _validated_repository_path("myrepo", "/foo/bar.jar") == "/repository/myrepo/foo/bar.jar"


def test_root_asset_allowed():
    assert _validated_repository_path("myrepo", "/bar.jar") == "/repository/myrepo/bar.jar"


@pytest.mark.parametrize(
    "path",
    [
        "/../../../../service/rest/v1/security/users",
        "/../otherrepo/asset.jar",
        "/../../../etc/passwd",
    ],
)
def test_traversal_rejected(path):
    with pytest.raises(HTTPException) as exc_info:
        _validated_repository_path("myrepo", path)
    assert exc_info.value.status_code == 400
