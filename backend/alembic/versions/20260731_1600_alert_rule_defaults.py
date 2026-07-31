"""alert rules: optional webhook + default-rule provenance

Revision ID: 20260731_1600
Revises: 20260731_1500
Create Date: 2026-07-31 16:00:00.000000

Relaxes ``alert_rules.webhook_url`` to nullable (a rule can now exist and
evaluate without a configured delivery destination) and adds
``is_default`` so the startup seed can ship sensible default alert rules
without recreating them if a user deletes one.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_1600"
down_revision: Union[str, None] = "20260731_1500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("alert_rules", "webhook_url", existing_type=sa.String(length=512), nullable=True)
    op.add_column("alert_rules", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("alert_rules", "is_default")
    op.execute("UPDATE alert_rules SET webhook_url = '' WHERE webhook_url IS NULL")
    op.alter_column("alert_rules", "webhook_url", existing_type=sa.String(length=512), nullable=False)
