"""Repository- and image-level access control (Artifactory-style access rules).

The base RBAC system (:mod:`app.dependencies`) only ever answers "does this user
hold permission key X *anywhere*" — it has no notion of which repository or image
a request targets. This module supplies that dimension.

Model
-----
A role owns any number of :class:`~app.models.RoleAccessRule` rows. Each rule is
a five-part statement::

    <effect> <actions> on <repo_pattern> / <image_pattern>

  * ``effect``        — ``allow`` or ``deny``. A deny beats an allow **within the
                        same role**, which is how "everything except …" is
                        expressed (Artifactory's exclude patterns).
  * ``actions``       — any subset of :data:`ACTIONS` (``read``/``scan``/``delete``),
                        so a role can be allowed to see an image without being
                        allowed to delete it.
  * ``repo_pattern``  — wildcard over repository names (``*``, ``prod-*``).
  * ``image_pattern`` — wildcard over image display names (``abrisham*``,
                        ``team/**``), matched against ``group/name`` as produced
                        by :func:`app.services.images.component_display_name`.

Evaluation
----------
Rules are evaluated **per role**, and the results are unioned across the roles a
user holds — mirroring how effective *permissions* are already a union
(``_load_user_permissions``). One role granting an action is enough.

Denies are deliberately role-local: a deny in role A cannot silently strip
access that role B grants outright. That keeps every role's rules readable in
isolation, and matches Artifactory, where exclude patterns only narrow the
permission target that declares them.

A role that has *no rule matching a repository at all* falls back to its
:attr:`~app.models.Role.access_mode`:

  * ``unrestricted`` — full access to that repository. This is the default and
    what the seeded admin/operator/viewer roles carry, so an install that never
    writes a rule behaves exactly as it did before access rules existed.
  * ``scoped`` — nothing. The role grants only what its own rules allow, so it
    can never widen a user's access by accident. This is what closes the
    multi-role union bypass documented in
    ``security/findings/high/HIGH-02-multirole-scope-union-bypass.md``: give the
    baseline role everyone holds ``scoped`` and it stops overriding the
    restrictions on a deliberately narrow role.

Wildcards
---------
Ant/Artifactory-style, **not** :mod:`fnmatch`:

  ===========  ==========================================================
  ``*``        any run of characters except ``/``
  ``**``       any run of characters, including ``/``
  ``?``        exactly one character, not ``/``
  ===========  ==========================================================

So ``team/*`` matches ``team/api`` but not ``team/api/edge``; ``team/**``
matches both. Patterns are anchored — they must match the whole name.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import RoleAccessRule, User

# --- vocabulary -------------------------------------------------------------
READ = "read"
SCAN = "scan"
DELETE = "delete"
#: Every action an access rule can grant, in escalating order of danger.
ACTIONS: tuple[str, ...] = (READ, SCAN, DELETE)

ALLOW = "allow"
DENY = "deny"
EFFECTS: tuple[str, ...] = (ALLOW, DENY)

MODE_UNRESTRICTED = "unrestricted"
MODE_SCOPED = "scoped"
ACCESS_MODES: tuple[str, ...] = (MODE_UNRESTRICTED, MODE_SCOPED)


# --- pattern compilation ----------------------------------------------------
# `**` must be tried before `*`, hence the alternation order.
_WILDCARD = re.compile(r"\*\*|[*?]")
_TRANSLATION = {"**": ".*", "*": "[^/]*", "?": "[^/]"}


@lru_cache(maxsize=1024)
def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile an Ant-style glob into an anchored regex.

    Cached because the same handful of patterns are re-matched on every request
    of every listing endpoint.
    """
    parts: list[str] = []
    pos = 0
    for match in _WILDCARD.finditer(pattern):
        parts.append(re.escape(pattern[pos:match.start()]))
        parts.append(_TRANSLATION[match.group()])
        pos = match.end()
    parts.append(re.escape(pattern[pos:]))
    return re.compile(f"(?s:{''.join(parts)})\\Z")


def matches(pattern: str, value: str) -> bool:
    """Whether ``value`` matches the Ant-style ``pattern`` in full."""
    return compile_pattern(pattern).match(value) is not None


def is_universal(pattern: str) -> bool:
    """Whether ``pattern`` matches every possible name.

    Only ``**`` qualifies: a bare ``*`` stops at ``/``, so it misses ``team/app``.
    """
    return bool(pattern) and set(pattern) == {"*"} and "**" in pattern


def parse_actions(raw: str | None) -> frozenset[str]:
    """Read the stored comma-separated action set, dropping anything unknown."""
    if not raw:
        return frozenset()
    return frozenset(a for a in (part.strip() for part in raw.split(",")) if a in ACTIONS)


def format_actions(actions: Iterable[str]) -> str:
    """Canonical storage form: unique, known, sorted by :data:`ACTIONS` order."""
    known = {a for a in actions if a in ACTIONS}
    return ",".join(a for a in ACTIONS if a in known)


# --- compiled rule sets -----------------------------------------------------
@dataclass(frozen=True, slots=True)
class _CompiledRule:
    image: re.Pattern[str]
    actions: frozenset[str]
    universal: bool

    def covers(self, image_name: str, action: str) -> bool:
        return action in self.actions and self.image.match(image_name) is not None


@dataclass(frozen=True, slots=True)
class _RoleRules:
    """One role's rules that matched a given repository, pre-compiled."""

    allow: tuple[_CompiledRule, ...]
    deny: tuple[_CompiledRule, ...]

    def grants(self, image_name: str, action: str) -> bool:
        if any(rule.covers(image_name, action) for rule in self.deny):
            return False
        return any(rule.covers(image_name, action) for rule in self.allow)

    def grants_everything(self, action: str) -> bool:
        """Whether this role grants ``action`` on every image in the repository."""
        if any(action in rule.actions for rule in self.deny):
            return False
        return any(rule.universal and action in rule.actions for rule in self.allow)


@dataclass(frozen=True, slots=True)
class RepoAccess:
    """A user's decided access to one repository.

    Obtained from :meth:`AccessResolver.repo`. Callers ask two questions of it:
    the cheap :attr:`unrestricted` / :attr:`blocks_everything` fast paths (which
    let a handler skip an expensive Nexus round-trip entirely), and the per-image
    :meth:`allows`.
    """

    repo: str
    unrestricted: bool
    roles: tuple[_RoleRules, ...]

    def allows(self, image_name: str, action: str = READ) -> bool:
        """Whether ``action`` is permitted on ``image_name`` in this repository."""
        if self.unrestricted:
            return True
        return any(role.grants(image_name, action) for role in self.roles)

    @property
    def blocks_everything(self) -> bool:
        """True when no image in this repository can be reached, whatever its name.

        Lets a handler return an empty result without calling Nexus at all.
        """
        return not self.unrestricted and not any(role.allow for role in self.roles)

    @property
    def visible(self) -> bool:
        """Whether the repository itself should appear in listings."""
        return not self.blocks_everything

    def covers_all(self, action: str = READ) -> bool:
        """Whether ``action`` is permitted on *every* image in this repository.

        Repository-wide operations — a retention policy that deletes across the
        whole repo, a scan target that enables it wholesale — need repository-wide
        authority. Someone scoped to ``abrisham*`` must not be able to configure a
        policy whose blast radius is the entire repository.
        """
        if self.unrestricted:
            return True
        return any(role.grants_everything(action) for role in self.roles)

    def filter(
        self,
        items: Iterable[dict],
        *,
        key: str | Callable[[dict], str | None] = "name",
        action: str = READ,
    ) -> list[dict]:
        """Keep the items whose image name this access permits.

        ``key`` is either a dict key holding the image name, or a callable
        deriving it. An item whose name cannot be determined is **dropped** —
        unattributed data fails closed rather than leaking.
        """
        if self.unrestricted:
            return list(items)
        read_name = key if callable(key) else (lambda item: item.get(key))
        kept = []
        for item in items:
            name = read_name(item)
            if name and self.allows(name, action):
                kept.append(item)
        return kept


# --- resolution -------------------------------------------------------------
class AccessResolver:
    """Per-request access oracle for one user.

    Every rule belonging to the user's roles is loaded once by
    :func:`load_access`; each repository is then decided in memory and memoised,
    so a listing endpoint that touches many repositories costs a single query.
    """

    __slots__ = ("_modes", "_rules_by_role", "_cache")

    def __init__(self, user: User, rules_by_role: dict[int, list[RoleAccessRule]]) -> None:
        self._modes = {role.id: role.access_mode for role in user.roles}
        self._rules_by_role = rules_by_role
        self._cache: dict[str, RepoAccess] = {}

    @property
    def unrestricted_everywhere(self) -> bool:
        """True when no rule can narrow this user in any repository.

        Holds when they carry no access rules at all *and* at least one of their
        roles falls back to unrestricted — the stock admin/operator/viewer case.
        Bulk operations that cannot be partially applied (delete every report,
        run every retention policy) require this rather than a per-repo check.
        """
        return not self._rules_by_role and any(
            mode == MODE_UNRESTRICTED for mode in self._modes.values()
        )

    def repo(self, name: str) -> RepoAccess:
        """Decide (and cache) this user's access to repository ``name``."""
        decided = self._cache.get(name)
        if decided is None:
            decided = self._decide(name)
            self._cache[name] = decided
        return decided

    def _decide(self, repo: str) -> RepoAccess:
        role_rules: list[_RoleRules] = []
        for role_id, mode in self._modes.items():
            allow: list[_CompiledRule] = []
            deny: list[_CompiledRule] = []
            for rule in self._rules_by_role.get(role_id, ()):
                if not matches(rule.repo_pattern, repo):
                    continue
                compiled = _CompiledRule(
                    image=compile_pattern(rule.image_pattern),
                    actions=parse_actions(rule.actions),
                    universal=is_universal(rule.image_pattern),
                )
                (deny if rule.effect == DENY else allow).append(compiled)

            if not allow and not deny:
                # No rule speaks to this repository — the role's mode decides.
                if mode == MODE_UNRESTRICTED:
                    return RepoAccess(repo=repo, unrestricted=True, roles=())
                continue

            role_rules.append(_RoleRules(allow=tuple(allow), deny=tuple(deny)))

        return RepoAccess(repo=repo, unrestricted=False, roles=tuple(role_rules))

    def allows(self, repo: str, image_name: str, action: str = READ) -> bool:
        """Shorthand for ``resolver.repo(repo).allows(image_name, action)``."""
        return self.repo(repo).allows(image_name, action)

    def visible_repos(self, names: Iterable[str]) -> list[str]:
        """The subset of ``names`` this user may see at all."""
        return [name for name in names if self.repo(name).visible]

    def filter_by_repo(
        self,
        items: Iterable[dict],
        *,
        repo_key: str = "repo",
        image_key: str | None = None,
        action: str = READ,
    ) -> list[dict]:
        """Filter rows that each carry their own repository (and optionally image).

        With ``image_key`` omitted this is a repository-visibility filter; with it
        set, each row is additionally checked against its image name.
        """
        kept = []
        for item in items:
            repo = item.get(repo_key)
            if not repo:
                continue
            access = self.repo(repo)
            if image_key is None:
                if access.visible:
                    kept.append(item)
            else:
                name = item.get(image_key)
                if name and access.allows(name, action):
                    kept.append(item)
        return kept


async def load_access(session: AsyncSession, user: User) -> AccessResolver:
    """Load every access rule for ``user``'s roles and return a resolver."""
    role_ids = [role.id for role in user.roles]
    rules_by_role: dict[int, list[RoleAccessRule]] = {}
    if role_ids:
        rows = (
            await session.execute(
                select(RoleAccessRule).where(RoleAccessRule.role_id.in_(role_ids))
            )
        ).scalars().all()
        for rule in rows:
            rules_by_role.setdefault(rule.role_id, []).append(rule)
    return AccessResolver(user, rules_by_role)


# --- introspection ----------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RuleMatch:
    """One rule that applied during an :func:`explain` call."""

    rule_id: int
    role_id: int
    effect: str
    repo_pattern: str
    image_pattern: str
    actions: tuple[str, ...]
    matched_image: bool


def explain(
    rules: Sequence[RoleAccessRule], repo: str, image_name: str
) -> tuple[list[RuleMatch], set[str]]:
    """Explain how ``rules`` (all from one role) decide ``repo``/``image_name``.

    Returns the rules whose repository pattern matched, and the actions that
    survive after applying denies. Powers the "test this rule" affordance in the
    admin UI — the reason wildcard rules are safe to write is that you can see
    what they do before saving.
    """
    applied: list[RuleMatch] = []
    allowed: set[str] = set()
    denied: set[str] = set()

    for rule in rules:
        if not matches(rule.repo_pattern, repo):
            continue
        actions = parse_actions(rule.actions)
        hit = matches(rule.image_pattern, image_name)
        applied.append(
            RuleMatch(
                rule_id=rule.id,
                role_id=rule.role_id,
                effect=rule.effect,
                repo_pattern=rule.repo_pattern,
                image_pattern=rule.image_pattern,
                actions=tuple(a for a in ACTIONS if a in actions),
                matched_image=hit,
            )
        )
        if hit:
            (denied if rule.effect == DENY else allowed).update(actions)

    return applied, allowed - denied
