Vulnerability Name: Image-scope RBAC bypass — `list_assets` never filters by scope
Severity: High
Affected Component: `backend/app/routers/repositories.py` — `list_assets` (`GET /api/repositories/{name}/assets`)

Description:
`list_repository_images`, `delete_repository_images`, and `download_asset` all resolve `allowed_image_patterns` and filter their results through `image_visible` before returning. `list_assets` — the paginated raw-asset endpoint — was the one sibling that returned Nexus's asset list (paths, sizes, checksums, `downloadUrl`, uploader) completely unfiltered.

Root Cause:
The image-scope feature was added to the "images/tags" view and the "download" path but not backfilled onto the raw asset-listing endpoint, which exposes the same underlying data through a different shape.

Security Impact:
A role scoped (via `RolesPage.jsx` → image scopes) to only `abrisham-frontend*` images in a repo could still call `GET /api/repositories/{repo}/assets` and enumerate every asset outside that scope — including images the RBAC feature was explicitly configured to hide.

Recommended Fix:
Apply the same `allowed_image_patterns` / `image_visible` filtering used by the sibling endpoints, resolving each item's owning image from its path the same way `download_asset` already does.

Implementation Status: Fixed — `backend/app/routers/repositories.py`'s `list_assets` now takes `user`/`session` dependencies, resolves the repo's format once, and filters `items` through `image_visible(patterns, _image_name_from_asset_path(...))` before returning.

Testing Result:
`backend/tests/test_image_scope.py` covers `allowed_image_patterns`, the function this endpoint calls, and `backend/tests/test_repositories.py` now adds the endpoint-level coverage this entry previously lacked — `list_assets` driven against a mocked Nexus client, asserting an out-of-scope image is filtered out of the response and that an unscoped user still sees everything.

Superseded detail: the filter no longer resolves the owning image with the `_image_name_from_asset_path` heuristic named above. That heuristic was the subject of [MED-02](../medium/MED-02-heuristic-image-name-resolution.md) and has been removed; `list_assets` now filters on `images.asset_image_map()`, Nexus's own component→asset mapping, and drops assets no component claims.
