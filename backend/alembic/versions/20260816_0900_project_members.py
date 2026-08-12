"""core: project members

Revision ID: 20260816_0900
Revises: 20260815_0900
Create Date: 2026-08-16 09:00:00.000000

Per-project access grants. Until now every ``projects:*`` permission was
global — anyone holding ``projects:read`` could see and act on every
Project. ``project_members`` is the join table that scopes that down to
"which projects", independent of the global RBAC that decides "what
actions". See ``app/core/project_access.py``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0900"
down_revision: Union[str, None] = "20260815_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_role", sa.String(length=16), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])
    op.create_unique_constraint(
        "uq_project_members_project_user", "project_members", ["project_id", "user_id"]
    )

    # Backfill: every existing project gets every current holder of the
    # global `admin` role as a project-admin member, so upgrading doesn't
    # instantly lock existing admins out of projects they already use.
    conn = op.get_bind()
    admin_user_ids = [
        row[0] for row in conn.execute(
            sa.text(
                "SELECT DISTINCT u.id FROM users u "
                "JOIN user_roles ur ON ur.user_id = u.id "
                "JOIN roles r ON r.id = ur.role_id "
                "WHERE r.name = 'admin'"
            )
        )
    ]
    project_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM projects"))]
    if admin_user_ids and project_ids:
        conn.execute(
            sa.text(
                "INSERT INTO project_members (project_id, user_id, project_role, created_at) "
                "VALUES (:project_id, :user_id, 'admin', now())"
            ),
            [
                {"project_id": pid, "user_id": uid}
                for pid in project_ids
                for uid in admin_user_ids
            ],
        )


def downgrade() -> None:
    op.drop_constraint("uq_project_members_project_user", "project_members", type_="unique")
    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_index("ix_project_members_project_id", table_name="project_members")
    op.drop_table("project_members")
