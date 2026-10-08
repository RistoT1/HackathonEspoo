"""hoitajan tapaukset, halytyssanat ja katseluloki

Revision ID: b7d2e91c4f10
Revises: a146c4d6d17e
Create Date: 2026-10-07 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2e91c4f10"
down_revision: str | Sequence[str] | None = "a146c4d6d17e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- Alkusisalto. Muutokset tasta eteenpain SQL:lla (backend/sql/kysymyssisalto.sql). ---
# Saantopohjaiset halytykset (CLAUDE.md: rintakipu, hengenahdistus, halvausoireet -> heti).
# Ilmaus verrataan litterointiin pienilla kirjaimilla osamerkkijonona. EI laaketieteellisesti
# tarkistettu: tarkistetaan hoitajan kanssa ennen kayttoa.
# (ilmaus, kategoria, taso)
HALYTYSSANAT = [
    ("rintakipu", "rintakipu", "PUNAINEN"),
    ("rintaa puristaa", "rintakipu", "PUNAINEN"),
    ("puristaa rinnassa", "rintakipu", "PUNAINEN"),
    ("rinnassa kipu", "rintakipu", "PUNAINEN"),
    ("rintaan sattuu", "rintakipu", "PUNAINEN"),
    ("hengenahdistus", "hengenahdistus", "PUNAINEN"),
    ("henkeä ahdistaa", "hengenahdistus", "PUNAINEN"),
    ("en saa henkeä", "hengenahdistus", "PUNAINEN"),
    ("ei saa henkeä", "hengenahdistus", "PUNAINEN"),
    ("hengittäminen on vaikeaa", "hengenahdistus", "PUNAINEN"),
    ("halvaus", "halvausoire", "PUNAINEN"),
    ("suupieli roikkuu", "halvausoire", "PUNAINEN"),
    ("kasvot roikkuu", "halvausoire", "PUNAINEN"),
    ("puhe sammaltaa", "halvausoire", "PUNAINEN"),
    ("käsi ei toimi", "halvausoire", "PUNAINEN"),
    ("toinen puoli ei toimi", "halvausoire", "PUNAINEN"),
    ("pyörtyi", "tajunnan häiriö", "PUNAINEN"),
    ("pyörryin", "tajunnan häiriö", "PUNAINEN"),
    ("tajuton", "tajunnan häiriö", "PUNAINEN"),
    ("kaaduin", "kaatuminen", "KELTAINEN"),
    ("kaatunut", "kaatuminen", "KELTAINEN"),
    ("kaatui", "kaatuminen", "KELTAINEN"),
]


def upgrade() -> None:
    op.create_table(
        "halytyssana",
        sa.Column("ilmaus", sa.String(length=200), nullable=False),
        sa.Column("kategoria", sa.String(length=100), nullable=False),
        sa.Column("taso", sa.String(length=20), nullable=False),
        sa.Column("on_aktiivinen", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("ilmaus"),
    )
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
        sa.ForeignKeyConstraint(["tapaus_id"], ["hoitajan_tapaus.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_katseluloki_tapaus_id"), "katseluloki", ["tapaus_id"])

    halytyssana = sa.table(
        "halytyssana", sa.column("ilmaus"), sa.column("kategoria"), sa.column("taso")
    )
    op.bulk_insert(
        halytyssana,
        [{"ilmaus": i, "kategoria": k, "taso": t} for i, k, t in HALYTYSSANAT],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_katseluloki_tapaus_id"), table_name="katseluloki")
    op.drop_table("katseluloki")
    op.drop_index(op.f("ix_hoitajan_tapaus_vanhus_id"), table_name="hoitajan_tapaus")
    op.drop_index(op.f("ix_hoitajan_tapaus_tila"), table_name="hoitajan_tapaus")
    op.drop_index(op.f("ix_hoitajan_tapaus_luotu"), table_name="hoitajan_tapaus")
    op.drop_index(op.f("ix_hoitajan_tapaus_kiireellisyys"), table_name="hoitajan_tapaus")
    op.drop_table("hoitajan_tapaus")
    op.drop_table("halytyssana")
