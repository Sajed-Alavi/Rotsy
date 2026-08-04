"""Access-rule engine tests (``app.core.access_control``).

Two halves. The first pins the wildcard grammar, which is the part an operator
types by hand and the part that changed meaning in the move off ``fnmatch`` —
notably that ``*`` no longer crosses ``/``.

The second pins evaluation: per-action grants, deny-beats-allow within a role,
union across roles, repository wildcards, and the ``access_mode`` fallback that
decides what a role does with a repository none of its rules mention. Between
them they cover the multi-role union bypass from
``security/findings/high/HIGH-02-multirole-scope-union-bypass.md``.
"""

from __future__ import annotations

import pytest

from app.core.access_control import (
    DELETE,
    READ,
    SCAN,
    format_actions,
    is_universal,
    load_access,
    matches,
    parse_actions,
)
from app.models import Role, RoleAccessRule, User

# --- wildcard grammar --------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "value", "expected"),
    [
        # The request's own examples.
        ("abrisham*", "abrisham", True),
        ("abrisham*", "abrisham-frontend", True),
        ("abrisham*", "abrisham-frontend:1.4", True),
        ("abrisham*", "not-abrisham", False),
        ("prod-*", "prod-api", True),
        ("prod-*", "prod", False),
        ("prod-*", "staging-api", False),
        # A single star stops at a path separator; a double star crosses it.
        ("team/*", "team/api", True),
        ("team/*", "team/api/edge", False),
        ("team/**", "team/api", True),
        ("team/**", "team/api/edge", True),
        ("team/**", "team", False),
        ("*", "app", True),
        ("*", "team/app", False),
        ("**", "team/sub/app", True),
        # '?' is exactly one character, and not a separator.
        ("v?", "v1", True),
        ("v?", "v10", False),
        ("a?b", "a/b", False),
        # Patterns are anchored at both ends.
        ("abrisham", "abrisham-frontend", False),
        ("frontend", "my-frontend", False),
        # Matching is case-sensitive, as the shell-glob matcher was.
        ("Abrisham*", "abrisham-app", False),
        # A trailing newline must not sneak past the end anchor.
        ("abrisham", "abrisham\n", False),
    ],
)
def test_pattern_matching(pattern: str, value: str, expected: bool):
    assert matches(pattern, value) is expected


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [("**", True), ("****", True), ("*", False), ("a**", False), ("*/**", False)],
)
def test_is_universal(pattern: str, expected: bool):
    """Only ``**`` reaches every name — ``*`` cannot cross a separator."""
    assert is_universal(pattern) is expected


def test_action_round_trip():
    assert format_actions(["scan", "read", "read"]) == "read,scan"
    assert format_actions(["delete", "read", "scan"]) == "read,scan,delete"
    assert parse_actions("read,scan") == frozenset({READ, SCAN})
    assert parse_actions("read,bogus") == frozenset({READ})
    assert parse_actions("") == frozenset()


# --- fixtures ----------------------------------------------------------------
async def _role(session, name: str, mode: str = "scoped", **rules) -> Role:
    role = Role(name=name, access_mode=mode)
    session.add(role)
    await session.flush()
    return role


def _rule(role: Role, repo="*", image="**", actions="read", effect="allow") -> RoleAccessRule:
    return RoleAccessRule(
        role_id=role.id, effect=effect, repo_pattern=repo, image_pattern=image, actions=actions
    )


async def _user(session, *roles: Role) -> User:
    user = User(username="u", email="u@example.com", password_hash="x", roles=list(roles))
    session.add(user)
    await session.commit()
    return user


# --- evaluation --------------------------------------------------------------
async def test_no_roles_at_all_grants_nothing(db_session):
    user = await _user(db_session)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.allows("anything") is False
    assert access.visible is False


async def test_unrestricted_role_with_no_rules_is_open(db_session):
    """The default. An install that never writes a rule behaves as it always did."""
    role = await _role(db_session, "viewer", mode="unrestricted")
    user = await _user(db_session, role)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.unrestricted is True
    assert access.allows("anything", DELETE) is True


async def test_scoped_role_with_no_rules_grants_nothing(db_session):
    role = await _role(db_session, "locked-down")
    user = await _user(db_session, role)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.unrestricted is False
    assert access.blocks_everything is True


async def test_rule_restricts_to_its_image_pattern(db_session):
    role = await _role(db_session, "abrisham-team")
    db_session.add(_rule(role, repo="my-repo", image="abrisham*"))
    user = await _user(db_session, role)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.allows("abrisham-frontend") is True
    assert access.allows("billing-secrets") is False


async def test_repo_pattern_spans_many_repositories(db_session):
    """The capability the old exact-repo model had no way to express."""
    role = await _role(db_session, "prod-readers")
    db_session.add(_rule(role, repo="prod-*", image="**"))
    user = await _user(db_session, role)
    resolver = await load_access(db_session, user)

    assert resolver.repo("prod-eu").allows("anything") is True
    assert resolver.repo("prod-us").allows("anything") is True
    assert resolver.repo("staging-eu").allows("anything") is False


async def test_actions_are_independent(db_session):
    """Reading an image and deleting it are separate grants."""
    role = await _role(db_session, "read-only")
    db_session.add(_rule(role, repo="my-repo", image="abrisham*", actions="read"))
    user = await _user(db_session, role)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.allows("abrisham-app", READ) is True
    assert access.allows("abrisham-app", SCAN) is False
    assert access.allows("abrisham-app", DELETE) is False


async def test_deny_beats_allow_within_one_role(db_session):
    role = await _role(db_session, "abrisham-team")
    db_session.add(_rule(role, repo="my-repo", image="abrisham*", actions="read,scan"))
    db_session.add(_rule(role, repo="my-repo", image="abrisham-secrets*", effect="deny",
                         actions="read,scan"))
    user = await _user(db_session, role)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.allows("abrisham-frontend") is True
    assert access.allows("abrisham-secrets-store") is False


async def test_deny_removes_only_the_actions_it_names(db_session):
    role = await _role(db_session, "team")
    db_session.add(_rule(role, repo="my-repo", image="**", actions="read,scan,delete"))
    db_session.add(_rule(role, repo="my-repo", image="**", effect="deny", actions="delete"))
    user = await _user(db_session, role)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.allows("app", READ) is True
    assert access.allows("app", SCAN) is True
    assert access.allows("app", DELETE) is False


async def test_deny_in_one_role_does_not_veto_another_roles_allow(db_session):
    """Denies are deliberately role-local.

    Effective access is a union across roles, so a deny that reached across
    roles would make every role's rules unreadable in isolation. This mirrors
    Artifactory, where exclude patterns only narrow the permission target that
    declares them. It is the behaviour most likely to be reported as a bug, so
    it is pinned here on purpose.
    """
    denier = await _role(db_session, "denier")
    db_session.add(_rule(denier, repo="my-repo", image="**", effect="deny", actions="read"))
    granter = await _role(db_session, "granter")
    db_session.add(_rule(granter, repo="my-repo", image="abrisham*", actions="read"))
    user = await _user(db_session, denier, granter)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.allows("abrisham-app") is True


async def test_unrestricted_baseline_role_still_opens_access(db_session):
    """Pins the unchanged default: a plain second role widens access."""
    baseline = await _role(db_session, "viewer", mode="unrestricted")
    scoped = await _role(db_session, "abrisham-team")
    db_session.add(_rule(scoped, repo="my-repo", image="abrisham*"))
    user = await _user(db_session, baseline, scoped)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.unrestricted is True
    assert access.allows("billing-secrets") is True


async def test_scoped_baseline_role_no_longer_defeats_a_scoped_role(db_session):
    """HIGH-02: switching the baseline role to 'scoped' closes the union bypass."""
    baseline = await _role(db_session, "viewer", mode="scoped")
    scoped = await _role(db_session, "abrisham-team")
    db_session.add(_rule(scoped, repo="my-repo", image="abrisham*"))
    user = await _user(db_session, baseline, scoped)
    access = (await load_access(db_session, user)).repo("my-repo")

    assert access.unrestricted is False
    assert access.allows("abrisham-frontend") is True
    assert access.allows("billing-secrets") is False


async def test_a_matching_rule_takes_an_unrestricted_role_off_its_fallback(db_session):
    """Backwards-compatible with image scopes: a repo with rules is scoped by
    them, a repo without any stays open."""
    role = await _role(db_session, "mixed", mode="unrestricted")
    db_session.add(_rule(role, repo="scoped-repo", image="abrisham*"))
    user = await _user(db_session, role)
    resolver = await load_access(db_session, user)

    assert resolver.repo("scoped-repo").allows("billing") is False
    assert resolver.repo("scoped-repo").allows("abrisham-app") is True
    assert resolver.repo("other-repo").unrestricted is True


# --- repository-level visibility ---------------------------------------------
async def test_visible_repos_filters_the_listing(db_session):
    role = await _role(db_session, "prod-only")
    db_session.add(_rule(role, repo="prod-*", image="**"))
    user = await _user(db_session, role)
    resolver = await load_access(db_session, user)

    assert resolver.visible_repos(["prod-eu", "staging-eu", "prod-us"]) == ["prod-eu", "prod-us"]


async def test_deny_only_role_hides_the_repository(db_session):
    role = await _role(db_session, "blocked", mode="unrestricted")
    db_session.add(_rule(role, repo="my-repo", image="**", effect="deny", actions="read"))
    user = await _user(db_session, role)

    assert (await load_access(db_session, user)).repo("my-repo").visible is False


async def test_covers_all_needs_a_universal_image_pattern(db_session):
    """Repository-wide config (retention, scan targets) needs repository-wide reach."""
    partial = await _role(db_session, "partial")
    db_session.add(_rule(partial, repo="my-repo", image="abrisham*", actions="delete"))
    whole = await _role(db_session, "whole")
    db_session.add(_rule(whole, repo="my-repo", image="**", actions="delete"))

    partial_user = await _user(db_session, partial)
    assert (await load_access(db_session, partial_user)).repo("my-repo").covers_all(DELETE) is False

    whole_user = User(username="w", email="w@example.com", password_hash="x", roles=[whole])
    db_session.add(whole_user)
    await db_session.commit()
    assert (await load_access(db_session, whole_user)).repo("my-repo").covers_all(DELETE) is True


async def test_unrestricted_everywhere_only_without_rules(db_session):
    """The gate on bulk operations that cannot be partially applied."""
    plain = await _role(db_session, "viewer", mode="unrestricted")
    plain_user = await _user(db_session, plain)
    assert (await load_access(db_session, plain_user)).unrestricted_everywhere is True

    ruled = await _role(db_session, "ruled", mode="unrestricted")
    db_session.add(_rule(ruled, repo="one-repo", image="abrisham*"))
    ruled_user = User(username="r", email="r@example.com", password_hash="x", roles=[ruled])
    db_session.add(ruled_user)
    await db_session.commit()
    assert (await load_access(db_session, ruled_user)).unrestricted_everywhere is False


async def test_filter_drops_items_it_cannot_attribute(db_session):
    """Fail closed: an item with no resolvable image name is never shown."""
    role = await _role(db_session, "team")
    db_session.add(_rule(role, repo="my-repo", image="abrisham*"))
    user = await _user(db_session, role)
    access = (await load_access(db_session, user)).repo("my-repo")

    items = [{"name": "abrisham-app"}, {"name": "billing"}, {"name": None}, {}]
    assert access.filter(items) == [{"name": "abrisham-app"}]
