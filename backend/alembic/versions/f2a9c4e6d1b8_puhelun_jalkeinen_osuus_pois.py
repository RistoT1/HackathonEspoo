"""puhelun jalkeinen osuus pois backendista: hoitajan_tapaus ja katseluloki

Backend kattaa toistaiseksi vain soittoa edeltavan osuuden (tunnistus -> soittojono).

Revision ID: f2a9c4e6d1b8
Revises: e5f17c3a8b42
Create Date: 2026-10-07 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a9c4e6d1b8"
down_revision: str | Sequence[str] | None = "e5f17c3a8b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_katseluloki_tapaus_id"), table_name="katseluloki")
    op.drop_table("katseluloki")
    op.drop_index(op.f("ix_hoitajan_tapaus_vanhus_id"), table_name="hoitajan_tapaus")
    op.drop_index(op.f("ix_hoitajan_tapaus_tila"), table_name="hoitajan_tapaus")
    op.drop_index(op.f("ix_hoitajan_tapaus_luotu"), table_name="hoitajan_tapaus")
    op.drop_index(op.f("ix_hoitajan_tapaus_kiireellisyys"), table_name="hoitajan_tapaus")
    op.drop_table("hoitajan_tapaus")


def downgrade() -> None:
    # Palauttaa vain rakenteen; data poistui upgradessa.
    op.create_table(
        "hoitajan_tapaus",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tehtava_id", sa.String(length=36), nullable=False),
        sa.Column("vanhus_id", sa.String(length=64), nullable=False),
        sa.Column("kiireellisyys", sa.String(length=20), nullable=False),
        sa.Column("llm_ehdotus", sa.String(length=20), nullable=False),
        sa.Column("perusteet", sa.JSON(), nullable=False),
        sa.Column("yhteenveto", sa.Text(), nullable=False),
        sa.Column("laukaisimet", sa.JSON(), nullable=False),
        sa.Column("tila", sa.String(length=30), nullable=False),
        sa.Column("muistiinpano", sa.Text(), nullable=False),
        sa.Column("luokitus_oikein", sa.Boolean(), nullable=True),
        sa.Column("luotu", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paivitetty", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tehtava_id"], ["puhelutehtava.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tehtava_id"),
    )
    op.create_index(op.f("ix_hoitajan_tapaus_kiireellisyys"), "hoitajan_tapaus", ["kiireellisyys"])
    op.create_index(op.f("ix_hoitajan_tapaus_luotu"), "hoitajan_tapaus", ["luotu"])
    op.create_index(op.f("ix_hoitajan_tapaus_tila"), "hoitajan_tapaus", ["tila"])
    op.create_index(op.f("ix_hoitajan_tapaus_vanhus_id"), "hoitajan_tapaus", ["vanhus_id"])
    op.create_table(
        "katseluloki",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tapaus_id", sa.String(length=36), nullable=False),
        sa.Column("katsoja", sa.String(length=100), nullable=False),
        sa.Column("aika", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "toiminto", sa.String(length=30), server_default=sa.text("'katselu'"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tapaus_id"], ["hoitajan_tapaus.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_katseluloki_tapaus_id"), "katseluloki", ["tapaus_id"])
