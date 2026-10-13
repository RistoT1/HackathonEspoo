"""Yhdistää kaksi rinnakkaista migraatiohaaraa: potilassivun katseluloki (a7c3e9f1b2d4) ja
vanhuksen henkilötiedot (kanta-haara, e9b1c2d3a4f5). Ei skeemamuutoksia.

Kumpikin kehityskanta päivittyy ilman nollausta: kumman tahansa haaran ajanut kanta saa
puuttuvan haaran migraatiot ja tämän yhdistyksen.

Revision ID: 2cc698f59ef4
Revises: a7c3e9f1b2d4, e9b1c2d3a4f5
Create Date: 2026-10-13 15:51:32.535791

"""

from collections.abc import Sequence

revision: str = "2cc698f59ef4"
down_revision: str | Sequence[str] | None = ("a7c3e9f1b2d4", "e9b1c2d3a4f5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
