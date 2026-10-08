"""halytyssanat pois kannasta: repon datatiedostoon app/triage/halytyssanat.json

Turvallisuuskriittiset saannot muutoshallintaan (versionhallinta, katselmointi, testit)
vapaasti muokattavien kantarivien sijaan.

Revision ID: c41f8a2d9e73
Revises: b7d2e91c4f10
Create Date: 2026-10-07 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c41f8a2d9e73"
down_revision: str | Sequence[str] | None = "b7d2e91c4f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("halytyssana")


def downgrade() -> None:
    # Palauttaa vain rakenteen; sisalto on tiedostossa app/triage/halytyssanat.json.
    op.create_table(
        "halytyssana",
        sa.Column("ilmaus", sa.String(length=200), nullable=False),
        sa.Column("kategoria", sa.String(length=100), nullable=False),
        sa.Column("taso", sa.String(length=20), nullable=False),
        sa.Column("on_aktiivinen", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("ilmaus"),
    )
