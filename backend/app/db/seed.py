"""Idempotent bootstrap seed.

Runs after migrations on every startup. Safe to re-run: it inserts only what
is missing. Responsibilities:

  1. Ensure every permission key in :data:`core.permissions.PERMISSIONS`
     exists in the DB.
  2. Ensure the three system roles (admin/operator/viewer) exist with the
     permissions defined in :data:`SYSTEM_ROLE_PERMISSIONS`.
  3. Ensure the bootstrap admin user exists (created from env vars) and has
     the ``admin`` role.
  4. Ensure the default alert rules exist (see :func:`_seed_default_alert_rules`)
     — inserted only if missing **by name**, never overwritten/recreated, so a
     user who deletes or edits one keeps it that way across restarts (unlike
     the system roles above, which are intentionally re-synced every boot).

All relationship access uses eager loading (``selectinload``) so we never
trigger a lazy load inside the async context (which would raise
``MissingGreenlet``).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..core.permissions import (
    ALL_PERMISSION_KEYS,
    PERMISSIONS,
    SYSTEM_ROLE_PERMISSIONS,
)
from ..core.security import hash_password
from ..models import AlertRule, Permission, Role, User

logger = logging.getLogger(__name__)

# (name, metric, condition, threshold) — install-agnostic defaults only.
# No storage.total default is seeded: there's no sensible byte threshold that
# holds across installs, so a fabricated one would be worse than none.
_DEFAULT_ALERT_RULES: list[tuple[str, str, str, float]] = [
    ("Blobstore disk usage above 85%", "blobstore.used_pct", ">", 85.0),
    ("Blobstore disk usage above 95% (critical)", "blobstore.used_pct", ">", 95.0),
]


async def _seed_permissions(session: AsyncSession) -> dict[str, Permission]:
    """Insert any missing permissions; return the full key->Permission map."""
    result = await session.execute(select(Permission))
    by_key = {p.key: p for p in result.scalars().all()}

    for key, description in PERMISSIONS:
        if key not in by_key:
            perm = Permission(key=key, description=description)
            session.add(perm)
            await session.flush()
            by_key[key] = perm
        elif by_key[key].description != description:
            by_key[key].description = description

    await session.flush()
    return by_key


async def _seed_roles(session: AsyncSession, perms_by_key: dict[str, Permission]) -> dict[str, Role]:
    """Insert/repair system roles; return the name->Role map.

    Eager-loads ``permissions`` so reassigning the relationship does not need
    a lazy fetch (which is forbidden under async).
    """
    result = await session.execute(select(Role).options(selectinload(Role.permissions)))
    by_name: dict[str, Role] = {r.name: r for r in result.scalars().all()}

    for role_name, perm_keys in SYSTEM_ROLE_PERMISSIONS.items():
        if role_name not in by_name:
            role = Role(
                name=role_name,
                description=f"System role: {role_name}",
                is_system=True,
            )
            session.add(role)
            await session.flush()
            # Re-fetch with the relationship loaded so we can set it safely.
            role = await session.scalar(
                select(Role).options(selectinload(Role.permissions)).where(Role.id == role.id)
            )
            by_name[role_name] = role
        role = by_name[role_name]
        role.is_system = True
        role.permissions = [perms_by_key[k] for k in perm_keys if k in perms_by_key]

    await session.flush()
    return by_name


async def _seed_bootstrap_admin(
    session: AsyncSession,
    settings: Settings,
    roles_by_name: dict[str, Role],
) -> None:
    """Create the bootstrap admin user if they don't already exist."""
    admin_role = roles_by_name.get("admin")
    if admin_role is None:
        logger.error("admin role missing during seed; skipping bootstrap user.")
        return

    # Eager-load roles so we can mutate the relationship under async.
    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.username == settings.BOOTSTRAP_ADMIN_USERNAME)
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        if admin_role not in existing.roles:
            existing.roles.append(admin_role)
        return

    user = User(
        username=settings.BOOTSTRAP_ADMIN_USERNAME,
        email=settings.BOOTSTRAP_ADMIN_EMAIL,
        password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
        is_active=True,
        roles=[admin_role],
    )
    session.add(user)
    logger.info("Created bootstrap admin user '%s'.", settings.BOOTSTRAP_ADMIN_USERNAME)


async def _seed_default_alert_rules(session: AsyncSession) -> None:
    """Insert the default alert rules if missing by name — never re-created.

    Unlike system roles (always re-synced), these are meant to be user-tunable
    once seeded: deleting or editing one is a real decision that must survive
    a restart, so this only ever inserts, keyed on ``name``.
    """
    result = await session.execute(select(AlertRule.name).where(AlertRule.is_default.is_(True)))
    existing_names = {row[0] for row in result.all()}

    for name, metric, condition, threshold in _DEFAULT_ALERT_RULES:
        if name in existing_names:
            continue
        session.add(AlertRule(
            name=name, metric=metric, condition=condition, threshold=threshold,
            repo_filter=None, webhook_url=None, enabled=True, is_default=True,
        ))
    await session.flush()


async def run_seed(session: AsyncSession, settings: Settings) -> None:
    """Run the full idempotent seed. Called from the backend entrypoint."""
    perms_by_key = await _seed_permissions(session)
    roles_by_name = await _seed_roles(session, perms_by_key)
    await _seed_bootstrap_admin(session, settings, roles_by_name)
    await _seed_default_alert_rules(session)
    await session.commit()
    logger.info(
        "Seed complete: %d permissions, %d roles.",
        len(ALL_PERMISSION_KEYS),
        len(SYSTEM_ROLE_PERMISSIONS),
    )
