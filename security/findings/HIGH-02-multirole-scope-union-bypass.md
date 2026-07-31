Vulnerability Name: Image-scope restriction silently defeated by holding any second unscoped role
Severity: High
Affected Component: `backend/app/core/image_scope.py` (`allowed_image_patterns`), `Role` model (`backend/app/models/user.py`)

Description:
A role with zero `RoleImageScope` rows for a repo was unconditionally treated as "unrestricted" there, and a user's effective access was the union across all held roles. A user holding any second role without scope rows for that repo — most commonly a baseline role like `viewer` that everyone holds — got full access regardless of an explicitly scoped role they also held, with no way for an admin to prevent it.

Root Cause:
The union computation had no concept of "this role should never grant blanket access on its own" — absence of scope rows was the only signal, and it always meant "open."

Security Impact:
An admin scopes a role "frontend-only" to `abrisham-frontend*` and assigns it plus the baseline `viewer` role to a contractor, intending to restrict them to frontend images. Because `viewer` has no scope rows, the union resolves to unrestricted and the contractor gets full access to every image in the repo — the RBAC image-scope feature is silently non-functional for any user holding more than one role, which is the common case.

Recommended Fix:
Add an explicit per-role flag controlling whether "no scope rows" means "open" for that role, defaulting to the current (open) behavior so nothing existing breaks, but letting an admin turn it off for roles where it matters.

Implementation Status: Fixed — `Role.image_scope_unrestricted: bool` (default `True`, migration `20260731_1700_role_image_scope_unrestricted.py` backfills existing rows to `True`). `allowed_image_patterns` now only lets a role contribute blanket access when both "no scope rows for this repo" and `image_scope_unrestricted is True` hold. Exposed via `RoleCreate`/`RoleUpdate`/`RoleOut` (`backend/app/schemas/role.py`), `backend/app/routers/roles.py`, and a checkbox in `frontend/src/features/roles/RolesPage.jsx` ("Unrestricted where this role has no scopes for a repo").

Testing Result: `backend/tests/test_image_scope.py` — covers a single scoped role (restricts), a single unrestricted role (opens access, unchanged default), a scoped role paired with a default (`True`) baseline role (still bypasses — documents the unchanged default), and a scoped role paired with an opted-out (`False`) baseline role (bypass closed — the actual fix).
