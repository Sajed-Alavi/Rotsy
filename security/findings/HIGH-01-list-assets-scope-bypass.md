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

Testing Result: Covered indirectly by `backend/tests/test_image_scope.py`'s coverage of `allowed_image_patterns`, the function this endpoint now calls. No endpoint-level HTTP test was added (would require a live/mocked Nexus client), so manual verification is listed in the plan's verification section.
