"""vulnerability scanning tables

Revision ID: 20260720_0100
Revises: 20260719_2100
Create Date: 2026-07-20 01:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0100"
down_revision: Union[str, None] = "20260719_2100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("auto_scan", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("scanners", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scan_targets_repo", "scan_targets", ["repo"], unique=True)

    op.create_table(
        "scan_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("target_repo", sa.String(length=255), nullable=False),
        sa.Column("image", sa.String(length=512), nullable=False),
        sa.Column("scanner", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("critical", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("high", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("medium", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("low", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unknown", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_scan_reports_target_repo", "scan_reports", ["target_repo"])

    op.create_table(
        "scan_vulnerabilities",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.BigInteger(), sa.ForeignKey("scan_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("scanner", sa.String(length=32), nullable=False),
        sa.Column("cve", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("package", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("installed_version", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("fixed_version", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("cvss", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_scan_vulnerabilities_report_id", "scan_vulnerabilities", ["report_id"])
    op.create_index("ix_scan_vulnerabilities_repo", "scan_vulnerabilities", ["repo"])
    op.create_index("ix_scan_vulnerabilities_cve", "scan_vulnerabilities", ["cve"])


def downgrade() -> None:
    op.drop_index("ix_scan_vulnerabilities_cve", table_name="scan_vulnerabilities")
    op.drop_index("ix_scan_vulnerabilities_repo", table_name="scan_vulnerabilities")
    op.drop_index("ix_scan_vulnerabilities_report_id", table_name="scan_vulnerabilities")
    op.drop_table("scan_vulnerabilities")
    op.drop_index("ix_scan_reports_target_repo", table_name="scan_reports")
    op.drop_table("scan_reports")
    op.drop_index("ix_scan_targets_repo", table_name="scan_targets")
    op.drop_table("scan_targets")
