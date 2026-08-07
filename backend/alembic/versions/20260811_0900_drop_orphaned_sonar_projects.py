"""sonar: drop orphaned pre-migration sonar_projects rows

Revision ID: 20260811_0900
Revises: 20260810_0900
Create Date: 2026-08-11 09:00:00.000000

The previous migration (20260810_0900) added github_repository_id /
gitlab_repository_id to sonar_projects but explicitly did not backfill them
— any row created before that migration has both columns NULL. Under the
old (pre-migration) one-Sonar-project-per-Rotsy-Project model that was fine;
under the current one-per-repository model such a row is meaningless (it
belongs to no repository) and several endpoints
(``run_analysis``/``_resolve_repo``, health scoring, the repository list)
assumed every ``SonarProject`` under a Project has one, and crashed with a
raw 500 the first time a project with an orphaned row was touched.

Deletes rows where both columns are still NULL — cascades to their
``analysis_runs``/``quality_gate_results``, which are equally orphaned (no
repository to have been analyzed). If you had real analysis history under
the old model you want to keep, back up ``sonar_projects``,
``analysis_runs``, and ``quality_gate_results`` before running this, then
manually reconnect the affected repositories afterward — Rotsy has no way
to infer which repository an orphaned row used to belong to.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0900"
down_revision: Union[str, None] = "20260810_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "DELETE FROM sonar_projects WHERE github_repository_id IS NULL AND gitlab_repository_id IS NULL"
        )
    )
    if result.rowcount:
        print(f"[20260811_0900] Deleted {result.rowcount} orphaned sonar_projects row(s) "
              f"(and their cascaded analysis_runs/quality_gate_results) with no linked repository.")


def downgrade() -> None:
    # Deleted rows cannot be un-deleted — this migration is not reversible
    # (there is nothing to restore them from).
    pass
