"""Catalog of fine-grained permissions.

Every entry is a stable ``"resource:action"`` string. Routers reference these
via :class:`app.dependencies.RequirePermission`. The seed migration inserts
all keys listed in :data:`PERMISSIONS` so the DB stays in sync with the code.
"""

from __future__ import annotations

# (key, description) — order matters only for readability in the UI.
PERMISSIONS: list[tuple[str, str]] = [
    # Storage analyzer (Feature A)
    ("storage:read", "View deep storage analysis results"),
    ("storage:analyze", "Run deep storage analysis scans"),
    # Retention & cleanup (Feature B)
    ("retention:read", "View cleanup rules and dry-run previews"),
    ("retention:execute", "Run cleanup and delete components/blobs"),
    # Blobstores (Feature C)
    ("blobstores:read", "View blobstores and their state"),
    ("blobstores:write", "Create or modify blobstores"),
    # System & scripts (Feature D)
    ("system:read", "View Nexus system status"),
    ("system:execute", "Trigger host maintenance scripts"),
    # Vulnerability scanning (Feature E)
    ("scan:read", "View CVE vulnerability reports"),
    ("scan:execute", "Trigger image scans"),
    # Repositories (Feature F)
    ("repositories:read", "View repositories"),
    ("repositories:write", "Create, configure, invalidate cache, rebuild index"),
    # Projects & integrations (DevSecOps intelligence platform)
    ("projects:read", "View projects and their connected integrations"),
    ("projects:write", "Create/delete projects and connect/disconnect integrations"),
    # Access & webhooks
    ("access:read", "View API tokens, webhooks and anonymous access"),
    ("access:write", "Issue and revoke API tokens, manage webhooks and anonymous access"),
    # Nexus scheduled tasks
    ("tasks:control", "View, start and stop Nexus background tasks"),
    # Monitoring (v3: metrics + jobs + alerts)
    ("metrics:read", "View real-time and historical metrics"),
    ("metrics:collect", "Trigger metric collection / background scans"),
    ("jobs:read", "View background job status"),
    ("jobs:manage", "Enqueue and cancel background jobs"),
    ("alerts:read", "View alert rules and history"),
    ("alerts:write", "Create, edit, delete alert rules"),
    # Administration (the wrapper itself)
    ("users:manage", "Create, edit, deactivate users and assign roles"),
    ("roles:manage", "Create, edit, delete roles and assign permissions"),
    ("profile:edit", "Edit own profile and change password"),
]

ALL_PERMISSION_KEYS: list[str] = [key for key, _ in PERMISSIONS]
READ_PERMISSION_KEYS: list[str] = [key for key, _ in PERMISSIONS if key.endswith(":read")]

# Role name -> permission keys for the three system roles seeded on startup.
# Every role can edit its own profile (profile:edit); admins can do everything.
SYSTEM_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": ALL_PERMISSION_KEYS,
    "operator": [
        "storage:read", "storage:analyze",
        "retention:read", "retention:execute",
        "repositories:read", "scan:read", "scan:execute",
        "metrics:read", "metrics:collect", "jobs:read", "jobs:manage",
        "tasks:control",
        "projects:read", "projects:write",
        "profile:edit",
    ],
    "viewer": READ_PERMISSION_KEYS + ["profile:edit"],
}


def permission_description(key: str) -> str:
    """Human description for a permission key, or the key itself if unknown."""
    for k, desc in PERMISSIONS:
        if k == key:
            return desc
    return key
