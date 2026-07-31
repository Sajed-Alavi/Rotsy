"""HIGH-02 regression tests: multi-role union no longer silently reopens a
role's image-scope restriction, once the baseline role opts out via
``image_scope_unrestricted=False``. Also pins the pre-existing, unchanged
default behavior so the backward-compat guarantee doesn't quietly regress.
"""

from __future__ import annotations

from app.core.image_scope import allowed_image_patterns
from app.models import Role, RoleImageScope, User


async def test_single_scoped_role_restricts(db_session):
    session = db_session
    role = Role(name="frontend-only")
    session.add(role)
    await session.flush()
    session.add(RoleImageScope(role_id=role.id, repo="my-repo", pattern="frontend-*"))
    user = User(username="u1", email="u1@example.com", password_hash="x", roles=[role])
    session.add(user)
    await session.commit()

    patterns = await allowed_image_patterns(session, user, "my-repo")
    assert patterns == ["frontend-*"]


async def test_unrestricted_role_with_no_rows_opens_access(db_session):
    session = db_session
    role = Role(name="viewer", image_scope_unrestricted=True)
    user = User(username="u2", email="u2@example.com", password_hash="x", roles=[role])
    session.add(user)
    await session.commit()

    patterns = await allowed_image_patterns(session, user, "my-repo")
    assert patterns is None


async def test_default_unrestricted_baseline_role_still_bypasses_scoped_role(db_session):
    """Pins today's default (unchanged) behavior: a baseline role that hasn't
    been opted out still grants full access even alongside a scoped role,
    since image_scope_unrestricted defaults to True."""
    session = db_session
    scoped_role = Role(name="frontend-only")
    baseline_role = Role(name="viewer")  # image_scope_unrestricted defaults True
    session.add_all([scoped_role, baseline_role])
    await session.flush()
    session.add(RoleImageScope(role_id=scoped_role.id, repo="my-repo", pattern="frontend-*"))
    user = User(username="u3", email="u3@example.com", password_hash="x", roles=[scoped_role, baseline_role])
    session.add(user)
    await session.commit()

    patterns = await allowed_image_patterns(session, user, "my-repo")
    assert patterns is None


async def test_opted_out_baseline_role_no_longer_defeats_scoped_role(db_session):
    """The actual bug fix: flipping the baseline role's
    image_scope_unrestricted to False stops it from silently overriding the
    explicitly scoped role the same user holds."""
    session = db_session
    scoped_role = Role(name="frontend-only")
    baseline_role = Role(name="viewer", image_scope_unrestricted=False)
    session.add_all([scoped_role, baseline_role])
    await session.flush()
    session.add(RoleImageScope(role_id=scoped_role.id, repo="my-repo", pattern="frontend-*"))
    user = User(username="u4", email="u4@example.com", password_hash="x", roles=[scoped_role, baseline_role])
    session.add(user)
    await session.commit()

    patterns = await allowed_image_patterns(session, user, "my-repo")
    assert patterns == ["frontend-*"]


async def test_no_roles_at_all_grants_nothing(db_session):
    session = db_session
    user = User(username="u5", email="u5@example.com", password_hash="x", roles=[])
    session.add(user)
    await session.commit()

    patterns = await allowed_image_patterns(session, user, "my-repo")
    assert patterns == []
