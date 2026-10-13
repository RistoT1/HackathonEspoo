"""vanhuksen henkilotieto

Revision ID: e9b1c2d3a4f5
Revises: d5f0f6e9a2c1
Create Date: 2026-10-13 11:40:00.000000

Erillinen tunnistetietotaulu (nimi, hetu), linkki vain vanhus_id:lla. Pseudonymisointi:
tunnistetiedot pidetaan erillaan terveystiedoista, kuten vanhuksen_yhteystieto.

Siirretaan tunnistetiedot pois puhelutehtava-taulusta (edellinen migraatio d5f0f6e9a2c1
lisasi ne sinne) omaan tauluunsa. Nain terveystiedot (puhelutehtava) ja tunnistetiedot
pysyvat erillaan ja linkki on vain vanhus_id.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9b1c2d3a4f5"
down_revision: str | Sequence[str] | None = "d5f0f6e9a2c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "vanhuksen_henkilotieto",
        sa.Column("vanhus_id", sa.String(length=64), nullable=False),
        sa.Column("etunimi", sa.String(length=64), nullable=True),
        sa.Column("sukunimi", sa.String(length=64), nullable=True),
        sa.Column("hetu", sa.String(length=20), nullable=True),
        sa.Column("paivitetty", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("vanhus_id"),
    )
    # Poistetaan d5f0f6e9a2c1:n lisaamat tunnistesarakkeet puhelutehtava-taulusta:
    # tunnistetiedot kuuluvat erilliseen tauluun (pseudonymisointi).
    op.drop_column("puhelutehtava", "etunimi")
    op.drop_column("puhelutehtava", "sukunimi")
    op.drop_column("puhelutehtava", "hetu")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("puhelutehtava", sa.Column("hetu", sa.String(length=20), nullable=True))
    op.add_column("puhelutehtava", sa.Column("sukunimi", sa.String(length=64), nullable=True))
    op.add_column("puhelutehtava", sa.Column("etunimi", sa.String(length=64), nullable=True))
    op.drop_table("vanhuksen_henkilotieto")

