"""puhelutehtavan potilastiedot

Revision ID: d5f0f6e9a2c1
Revises: f2a9c4e6d1b8
Create Date: 2026-10-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f0f6e9a2c1"
down_revision: str | Sequence[str] | None = "dc1e2a6b7f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("puhelutehtava", sa.Column("etunimi", sa.String(length=64), nullable=True))
    op.add_column("puhelutehtava", sa.Column("sukunimi", sa.String(length=64), nullable=True))
    op.add_column("puhelutehtava", sa.Column("hetu", sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("puhelutehtava", "hetu")
    op.drop_column("puhelutehtava", "sukunimi")
    op.drop_column("puhelutehtava", "etunimi")
