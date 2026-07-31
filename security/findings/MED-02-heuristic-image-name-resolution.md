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

Implementation Status: Deferred (backlog)

Testing Result: Not applicable — no code change made this pass.
