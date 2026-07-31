"""Repository router regression tests.

HIGH-01 — endpoint-level coverage for the ``list_assets`` scope filter. The
original fix was only covered indirectly (through ``allowed_image_patterns``),
which left the actual filtering in the handler untested.

MED-02 — the scope decision is now made from Nexus's own component→asset
mapping, not by parsing the caller-supplied path, so a path crafted to *look*
like an in-scope image can no longer reach an out-of-scope one.

MED-03 — ``RepoCreate.extra`` can add new Nexus fields but can no longer
overwrite fields ``_build_repo_payload`` already validated.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import Role, RoleImageScope, User
from app.routers.repositories import RepoCreate, _build_repo_payload, download_asset, list_assets


# --- fakes -------------------------------------------------------------------
class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeHTTP:
    def __init__(self, assets_payload):
        self._assets_payload = assets_payload

    async def get(self, url, params=None):
        return _Response(self._assets_payload)


class _FakeNexus:
    """Minimal stand-in exposing only what the handlers touch.

    ``components`` drives ``images.asset_image_map``: the authoritative
    "this asset belongs to this image" answer.
    """

    def __init__(self, assets_payload, components):
        self.client = _FakeHTTP(assets_payload)
        self._components = components

    async def paginate(self, path, params=None):
        for component in self._components:
            yield component


def _request_with(nexus):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(nexus=nexus, cache=None)))


async def _scoped_user(session, pattern="frontend-*", repo="my-repo"):
    role = Role(name="frontend-only")
    session.add(role)
    await session.flush()
    session.add(RoleImageScope(role_id=role.id, repo=repo, pattern=pattern))
    user = User(username="scoped", email="s@example.com", password_hash="x", roles=[role])
    session.add(user)
    await session.commit()
    return user


# Two images in one repo; the caller is scoped to "frontend-*" only.
_COMPONENTS = [
    {"name": "frontend-app", "group": "", "assets": [
        {"path": "v2/frontend-app/manifests/1.0"},
        {"path": "v2/frontend-app/blobs/sha256:aaa"},
    ]},
    {"name": "billing-secrets", "group": "", "assets": [
        {"path": "v2/billing-secrets/manifests/1.0"},
        {"path": "v2/billing-secrets/blobs/sha256:bbb"},
    ]},
]


# --- HIGH-01 / MED-02: list_assets ------------------------------------------
async def test_list_assets_filters_out_of_scope_images(db_session):
    user = await _scoped_user(db_session)
    assets = {"items": [
        {"path": "v2/frontend-app/manifests/1.0"},
        {"path": "v2/billing-secrets/manifests/1.0"},
    ], "continuationToken": None}
    nexus = _FakeNexus(assets, _COMPONENTS)

    result = await list_assets(_request_with(nexus), "my-repo", user, db_session)

    paths = [i["path"] for i in result["items"]]
    assert paths == ["v2/frontend-app/manifests/1.0"]


async def test_list_assets_drops_assets_no_component_claims(db_session):
    """Fail closed: an asset Nexus does not attribute to any image cannot be
    shown to be in scope, so it is not listed."""
    user = await _scoped_user(db_session)
    assets = {"items": [{"path": "v2/orphaned/blobs/sha256:ccc"}]}
    nexus = _FakeNexus(assets, _COMPONENTS)

    result = await list_assets(_request_with(nexus), "my-repo", user, db_session)

    assert result["items"] == []


async def test_list_assets_unscoped_user_sees_everything(db_session):
    """An unrestricted role keeps the pre-existing behaviour untouched."""
    role = Role(name="viewer")  # image_scope_unrestricted defaults True
    user = User(username="open", email="o@example.com", password_hash="x", roles=[role])
    db_session.add(user)
    await db_session.commit()

    assets = {"items": [
        {"path": "v2/frontend-app/manifests/1.0"},
        {"path": "v2/billing-secrets/manifests/1.0"},
    ]}
    nexus = _FakeNexus(assets, _COMPONENTS)

    result = await list_assets(_request_with(nexus), "my-repo", user, db_session)

    assert len(result["items"]) == 2


# --- MED-02: download_asset --------------------------------------------------
async def test_download_asset_denies_out_of_scope_path(db_session):
    user = await _scoped_user(db_session)
    nexus = _FakeNexus({"items": []}, _COMPONENTS)

    with pytest.raises(HTTPException) as exc:
        await download_asset(
            _request_with(nexus), "my-repo", user, db_session,
            path="/v2/billing-secrets/manifests/1.0",
        )
    assert exc.value.status_code == 403


async def test_download_asset_denies_path_nexus_does_not_attribute(db_session):
    """MED-02 proper: the old heuristic parsed the owning image out of the
    path, so a path shaped like an allowed image was accepted on its face.
    The decision now needs Nexus to confirm the ownership."""
    user = await _scoped_user(db_session)
    nexus = _FakeNexus({"items": []}, _COMPONENTS)

    with pytest.raises(HTTPException) as exc:
        await download_asset(
            _request_with(nexus), "my-repo", user, db_session,
            path="/v2/frontend-app/../billing-secrets/manifests/1.0",
        )
    assert exc.value.status_code in (400, 403)


# --- MED-03: extra mass assignment ------------------------------------------
def _repo_create(**overrides) -> RepoCreate:
    base = dict(name="demo", format="docker", type="hosted", blob_store="default")
    return RepoCreate(**{**base, **overrides})


@pytest.mark.parametrize("key", ["name", "online", "storage", "docker"])
def test_extra_cannot_override_validated_fields(key):
    body = _repo_create(extra={key: {"tampered": True}})
    with pytest.raises(HTTPException) as exc:
        _build_repo_payload(body)
    assert exc.value.status_code == 400
    assert key in exc.value.detail


def test_extra_blobstore_override_rejected():
    """The concrete exploit from the finding: repointing storage at another
    team's blob store while the validated blob_store field says 'default'."""
    body = _repo_create(extra={"storage": {"blobStoreName": "other-teams-store", "writePolicy": "ALLOW"}})
    with pytest.raises(HTTPException):
        _build_repo_payload(body)


def test_extra_can_still_add_new_fields():
    """extra remains a usable escape hatch for genuinely new Nexus fields."""
    body = _repo_create(extra={"cleanup": {"policyNames": ["weekly"]}})
    payload = _build_repo_payload(body)
    assert payload["cleanup"] == {"policyNames": ["weekly"]}
    assert payload["storage"]["blobStoreName"] == "default"
