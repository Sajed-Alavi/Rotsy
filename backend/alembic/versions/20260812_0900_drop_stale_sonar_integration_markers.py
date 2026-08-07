"""sonar: drop stale 'sonar' integration markers with no sonar_projects row

Revision ID: 20260812_0900
Revises: 20260811_0900
Create Date: 2026-08-12 09:00:00.000000

The previous migration (20260811_0900) deleted orphaned pre-per-repository
``sonar_projects`` rows, but a project's ``integrations`` row for
module_key='sonar' has no foreign key to (and is never cascade-deleted by)
``sonar_projects`` — it is a standalone marker, created once via
``create_sonar_project_row``/``connect_integration`` the first time a
repository under a Project gets a Sonar project.

That migration therefore left every affected Project with a "sonar"
Integration row but zero SonarProject rows. Two endpoints assumed those could
never diverge:

  * ``create_sonar_project_row`` used to skip re-creating the Integration
    marker only when a SonarProject already existed for the Project — with
    none left, it called ``connect_integration`` again, which 409s on the
    Integration row still sitting there. Every future "Connect Sonar" attempt
    for any repository under the Project failed permanently (see
    ``app/modules/sonar/provisioning.py``, now fixed to check the Integration
    row directly instead).
  * ``run_analysis``/``run_repository_analysis`` in ``routers/sonar.py`` look
    up analysis targets via ``sonar_projects``, which had none — so
    ``POST /modules/sonar/projects/{id}/run-analysis`` returned 400 ("Project
    has no Sonar project configured") even though the Settings/Project UI
    showed a connected Sonar integration.

Deletes ``integrations`` rows where module_key='sonar' and no ``sonar_projects``
row references that project_id — the marker for a Sonar connection that, per
the previous migration, no longer has anything backing it. A Project in that
state re-provisions cleanly the next time a repository is connected to Sonar.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0900"
down_revision: Union[str, None] = "20260811_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            """
            DELETE FROM integrations
            WHERE module_key = 'sonar'
              AND project_id NOT IN (SELECT project_id FROM sonar_projects)
            """
        )
    )
    if result.rowcount:
        print(f"[20260812_0900] Deleted {result.rowcount} stale 'sonar' integration marker(s) "
              f"with no backing sonar_projects row.")


def downgrade() -> None:
    # Deleted rows cannot be un-deleted — nothing to restore them from, same
    # as 20260811_0900.
    pass
