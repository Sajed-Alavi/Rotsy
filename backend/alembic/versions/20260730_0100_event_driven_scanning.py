"""event-driven scanning: image ledger, repo baseline, report diagnostics

Revision ID: 20260730_0100
Revises: 20260724_0300
Create Date: 2026-07-30 01:00:00.000000

Adds the durable state that makes scanning event-driven instead of periodic:

  * ``scan_image_ledger`` — every image the system has observed, and what
    happened to it. Replaces 24-hour Redis dedupe keys, which caused every
    image to be re-scanned daily and again after any Redis restart.
  * ``scan_targets.baseline_at`` — when a repository's pre-existing images were
    adopted as history. Images present at that moment are never auto-scanned.
  * ``scan_reports.error`` / ``registry_ref`` / ``duration_ms`` — the failure
    reason, the reference actually scanned, and how long it took.

Existing repositories are baselined at migration time: whatever is already in
them is treated as history, so upgrading does not trigger a mass scan. Images
that already have a report are seeded into the ledger as ``scanned`` so they are
not picked up as "new" on the next observation.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0100"
down_revision: Union[str, None] = "20260724_0300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_image_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("image", sa.String(length=512), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="baseline"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="baseline"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_job_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("scan_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("repo", "image", name="uq_scan_ledger_repo_image"),
    )
    op.create_index("ix_scan_image_ledger_repo", "scan_image_ledger", ["repo"])
    op.create_index("ix_scan_image_ledger_state", "scan_image_ledger", ["state"])

    op.add_column("scan_targets", sa.Column("baseline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("scan_reports", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("scan_reports", sa.Column("registry_ref", sa.String(length=512),
                                            nullable=False, server_default=""))
    op.add_column("scan_reports", sa.Column("duration_ms", sa.Integer(),
                                            nullable=False, server_default="0"))

    # Seed the ledger from existing reports so already-scanned images are not
    # mistaken for new pushes, then baseline every enabled target.
    op.execute(
        """
        INSERT INTO scan_image_ledger (repo, image, state, source, first_seen_at, last_scan_at, scan_count)
        SELECT target_repo,
               image,
               CASE WHEN bool_or(status = 'success') THEN 'scanned' ELSE 'failed' END,
               'baseline',
               min(started_at),
               max(finished_at),
               1
        FROM scan_reports
        GROUP BY target_repo, image
        """
    )
    op.execute("UPDATE scan_targets SET baseline_at = now() WHERE baseline_at IS NULL")


def downgrade() -> None:
    op.drop_column("scan_reports", "duration_ms")
    op.drop_column("scan_reports", "registry_ref")
    op.drop_column("scan_reports", "error")
    op.drop_column("scan_targets", "baseline_at")
    op.drop_index("ix_scan_image_ledger_state", table_name="scan_image_ledger")
    op.drop_index("ix_scan_image_ledger_repo", table_name="scan_image_ledger")
    op.drop_table("scan_image_ledger")
