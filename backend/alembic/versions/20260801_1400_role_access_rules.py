"""role access rules: repository + image wildcards, actions, allow/deny

Revision ID: 20260801_1400
Revises: 20260801_1000
Create Date: 2026-08-01 14:00:00.000000

Replaces ``role_image_scopes`` with ``role_access_rules``, and
``roles.image_scope_unrestricted`` with ``roles.access_mode``.

The old model could only name one repository per row, matched images with a
shell glob, and gated viewing/scanning/deleting with a single rule. The new one
carries a repository *pattern*, an Ant-style image pattern, an action set
(read/scan/delete) and an allow/deny effect — see ``app.core.access_control``.

Existing grants are carried over exactly: each scope row becomes an ``allow``
rule pinned to that one repository (``repo_pattern`` = the literal name) for all
three actions, which is precisely what the old row authorised. The boolean maps
to the new two-state mode, so nobody's access changes across the upgrade.

Old patterns are shell globs, where ``*`` also matched ``/``; under Ant rules it
no longer does. In practice image scopes were written against flat image names
(``abrisham-frontend*``), which behave identically under both, so patterns are
copied verbatim rather than rewritten — a mechanical ``*`` -> ``**`` conversion
would widen every rule that never intended to cross a group boundary.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_1400"
down_revision: Union[str, None] = "20260801_1000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALL_ACTIONS = "read,scan,delete"


def upgrade() -> None:
    op.create_table(
        "role_access_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("effect", sa.String(length=8), server_default="allow", nullable=False),
        sa.Column("repo_pattern", sa.String(length=255), nullable=False),
        sa.Column("image_pattern", sa.String(length=255), nullable=False),
        sa.Column("actions", sa.String(length=64), server_default="read", nullable=False),
        sa.Column("description", sa.String(length=255), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "role_id", "effect", "repo_pattern", "image_pattern", name="uq_role_access_rule"
        ),
    )
    op.create_index("ix_role_access_rules_role_id", "role_access_rules", ["role_id"])
    op.create_index("ix_role_access_rules_repo_pattern", "role_access_rules", ["repo_pattern"])

    op.add_column(
        "roles",
        sa.Column("access_mode", sa.String(length=16), nullable=False, server_default="unrestricted"),
    )
    # server_default backfills existing rows; drop it afterward so future inserts
    # go through the ORM default like every other column.
    op.alter_column("roles", "access_mode", server_default=None)

    op.execute(
        """
        UPDATE roles
           SET access_mode = CASE WHEN image_scope_unrestricted
                                  THEN 'unrestricted' ELSE 'scoped' END
        """
    )
    # Distinct because the old unique key was (role_id, repo, pattern) while the
    # new one is (role_id, effect, repo_pattern, image_pattern) — identical rows
    # cannot collide, but being explicit costs nothing and documents the intent.
    op.execute(
        f"""
        INSERT INTO role_access_rules
              (role_id, effect, repo_pattern, image_pattern, actions, description)
        SELECT DISTINCT role_id, 'allow', repo, pattern, '{_ALL_ACTIONS}',
               'Migrated from image scope'
          FROM role_image_scopes
        """
    )

    op.drop_index("ix_role_image_scopes_repo", table_name="role_image_scopes")
    op.drop_index("ix_role_image_scopes_role_id", table_name="role_image_scopes")
    op.drop_table("role_image_scopes")
    op.drop_column("roles", "image_scope_unrestricted")


def downgrade() -> None:
    op.create_table(
        "role_image_scopes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("pattern", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("role_id", "repo", "pattern", name="uq_role_image_scope"),
    )
    op.create_index("ix_role_image_scopes_role_id", "role_image_scopes", ["role_id"])
    op.create_index("ix_role_image_scopes_repo", "role_image_scopes", ["repo"])

    op.add_column(
        "roles",
        sa.Column("image_scope_unrestricted", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("roles", "image_scope_unrestricted", server_default=None)
    op.execute("UPDATE roles SET image_scope_unrestricted = (access_mode = 'unrestricted')")

    # Only allow rules naming a single literal repository survive the round trip;
    # deny rules, repository wildcards and partial action sets have no
    # representation in the old model and are dropped rather than approximated
    # into something more permissive than what they replaced.
    op.execute(
        """
        INSERT INTO role_image_scopes (role_id, repo, pattern)
        SELECT DISTINCT role_id, repo_pattern, image_pattern
          FROM role_access_rules
         WHERE effect = 'allow'
           AND repo_pattern NOT LIKE '%*%'
           AND repo_pattern NOT LIKE '%?%'
        """
    )

    op.drop_index("ix_role_access_rules_repo_pattern", table_name="role_access_rules")
    op.drop_index("ix_role_access_rules_role_id", table_name="role_access_rules")
    op.drop_table("role_access_rules")
    op.drop_column("roles", "access_mode")
