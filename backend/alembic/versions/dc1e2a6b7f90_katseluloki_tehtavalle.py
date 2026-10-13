"""Kirjaa hoitajan tehtävänäkymän katselut.

Revision ID: dc1e2a6b7f90
Revises: cb6d4e2a91f0
Create Date: 2026-10-12 15:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "dc1e2a6b7f90"
down_revision: str | Sequence[str] | None = "cb6d4e2a91f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "katseluloki",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tehtava_id", sa.String(length=36), nullable=False),
        sa.Column("katsoja", sa.String(length=100), nullable=False),
        sa.Column("aika", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "toiminto",
            sa.String(length=30),
            server_default=sa.text("'katselu'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tehtava_id"], ["puhelutehtava.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_katseluloki_tehtava_id"), "katseluloki", ["tehtava_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_katseluloki_tehtava_id"), table_name="katseluloki")
    op.drop_table("katseluloki")
