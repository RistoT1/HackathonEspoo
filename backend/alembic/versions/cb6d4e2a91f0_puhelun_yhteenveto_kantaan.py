"""Tallenna puhelun triage-yhteenveto kantaan ilman raakavastauksia.

Revision ID: cb6d4e2a91f0
Revises: a3c7e1d9f204
Create Date: 2026-10-12 15:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cb6d4e2a91f0"
down_revision: str | Sequence[str] | None = "a3c7e1d9f204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "puhelun_tulos",
        sa.Column("istunto_id", sa.String(length=36), nullable=False),
        sa.Column("tehtava_id", sa.String(length=36), nullable=False),
        sa.Column("loppu", sa.String(length=32), nullable=True),
        sa.Column("paattyi", sa.DateTime(timezone=True), nullable=True),
        sa.Column("yhteenveto", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["tehtava_id"], ["puhelutehtava.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("istunto_id"),
    )
    op.create_index(
        op.f("ix_puhelun_tulos_tehtava_id"), "puhelun_tulos", ["tehtava_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_puhelun_tulos_tehtava_id"), table_name="puhelun_tulos")
    op.drop_table("puhelun_tulos")
