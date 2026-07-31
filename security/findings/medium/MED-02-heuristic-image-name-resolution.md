Vulnerability Name: Heuristic (not authoritative) image-name resolution for scope checks
Severity: Medium
Affected Component: `backend/app/routers/repositories.py` — `_image_name_from_asset_path`

Description:
`download_asset` and (after HIGH-01) `list_assets` resolve "which image does this asset path belong to" via a best-effort string-splitting heuristic (`v2/<image>/manifests/<tag>` / `v2/<image>/blobs/sha256:…`) rather than the authoritative components/images API used by `list_repository_images`. The function's own docstring already flags this as best-effort.

Root Cause:
Raw Nexus asset paths don't cleanly expose "which image" the way the structured components/images API does, and re-deriving that structure from a path string is inherently approximate for edge cases (unusual repository layouts, non-standard tag/digest formats).

Security Impact:
A scoped user could submit a path whose heuristically-parsed "owning image" happens to match an allowed pattern while the path actually resolves (server-side, at Nexus) to a different, restricted image — an edge-case scope bypass narrower than HIGH-01/CRIT-01 but in the same family.

Recommended Fix:
Where feasible, resolve the asset's owning image via the same components/images API `list_repository_images` uses (trading a request for correctness) rather than parsing the path, or at minimum add test coverage for the heuristic's known edge cases so regressions are caught.

Implementation Status: Fixed

Scope decisions no longer parse the asset path. New `images.asset_image_map()` (`backend/app/services/images.py`) paginates the components API — the same authoritative source `list_repository_images` uses — and returns a path→owning-image map; `_owning_image()` in `backend/app/routers/repositories.py` looks the caller's path up in it. Both `list_assets` and `download_asset` now **fail closed**: an asset no component claims is dropped from the listing and refused for download, rather than falling through to a guessed name.

One lookup covers a whole `list_assets` page, so the correctness trade costs one extra Nexus request per scoped request, not one per asset. The old `_image_name_from_asset_path` heuristic was deleted rather than kept as a fallback — leaving it reachable was the finding.

Testing Result:
`backend/tests/test_repositories.py` — out-of-scope image filtered from `list_assets`, unattributed asset dropped, unscoped user unaffected, and `download_asset` 403s for both an out-of-scope path and a traversal-shaped path Nexus does not attribute.
