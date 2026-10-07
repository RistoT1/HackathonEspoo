"""katselulokiin toiminto (katselu / yhteystieto)

Revision ID: d8a3b5c71e20
Revises: c41f8a2d9e73
Create Date: 2026-10-07 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8a3b5c71e20"
down_revision: str | Sequence[str] | None = "c41f8a2d9e73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "katseluloki",
        sa.Column(
            "toiminto", sa.String(length=30), server_default=sa.text("'katselu'"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("katseluloki", "toiminto")
