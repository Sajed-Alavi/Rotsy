"""sonar: one SonarProject per repository, not per Rotsy Project

Revision ID: 20260810_0900
Revises: 20260809_0900
Create Date: 2026-08-10 09:00:00.000000

A Rotsy Project is a grouping that can hold many repositories, each
independently analyzed (different languages, different histories). Adds
``sonar_projects.github_repository_id`` / ``gitlab_repository_id`` (exactly
one populated per row, each individually unique) so a SonarProject is
identified by *which repository it is*, not just which Project it's grouped
under — the previous one-Sonar-project-per-Project assumption made it
impossible to connect more than one repository to a Project at all.

No backfill: any ``sonar_projects`` rows that exist from before this
migration have no known repository to backfill against (the old schema
never recorded one), so they're left with NULL repository columns. They
keep working exactly as before for the one repository they were created
for; new connections from this point on populate the repository columns.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0900"
down_revision: Union[str, None] = "20260809_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sonar_projects",
        sa.Column("github_repository_id", sa.Integer(),
                   sa.ForeignKey("github_repositories.id", ondelete="CASCADE"), nullable=True),
    )
    op.add_column(
        "sonar_projects",
        sa.Column("gitlab_repository_id", sa.Integer(),
                   sa.ForeignKey("gitlab_repositories.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_unique_constraint(
        "uq_sonar_projects_github_repository_id", "sonar_projects", ["github_repository_id"]
    )
    op.create_unique_constraint(
        "uq_sonar_projects_gitlab_repository_id", "sonar_projects", ["gitlab_repository_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_sonar_projects_gitlab_repository_id", "sonar_projects", type_="unique")
    op.drop_constraint("uq_sonar_projects_github_repository_id", "sonar_projects", type_="unique")
    op.drop_column("sonar_projects", "gitlab_repository_id")
    op.drop_column("sonar_projects", "github_repository_id")
