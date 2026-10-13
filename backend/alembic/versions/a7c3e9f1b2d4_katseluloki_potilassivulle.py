"""Katseluloki myös potilassivulle: vanhus_id, tehtava_id valinnaiseksi.

Revision ID: a7c3e9f1b2d4
Revises: dc1e2a6b7f90
Create Date: 2026-10-13 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e9f1b2d4"
down_revision: str | Sequence[str] | None = "dc1e2a6b7f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch: SQLite (testit) ei osaa muuttaa sarakkeen NULL-sääntöä suoraan.
    with op.batch_alter_table("katseluloki") as taulu:
        taulu.alter_column("tehtava_id", existing_type=sa.String(length=36), nullable=True)
        taulu.add_column(sa.Column("vanhus_id", sa.String(length=64), nullable=True))
        taulu.create_index(op.f("ix_katseluloki_vanhus_id"), ["vanhus_id"], unique=False)


def downgrade() -> None:
    op.execute("DELETE FROM katseluloki WHERE tehtava_id IS NULL")
    with op.batch_alter_table("katseluloki") as taulu:
        taulu.drop_index(op.f("ix_katseluloki_vanhus_id"))
        taulu.drop_column("vanhus_id")
        taulu.alter_column("tehtava_id", existing_type=sa.String(length=36), nullable=False)
