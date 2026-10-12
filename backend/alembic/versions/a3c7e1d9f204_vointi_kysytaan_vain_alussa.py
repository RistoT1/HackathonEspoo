"""vointi kysytaan vain alussa: yleiskysymys pois, muutoskysymyksista "miten olet voinut"

Avaus kysyy jo kuulumiset ("Mitä kuuluu?"). Puhelussa kysyttiin vointia vielä keskellä
("...ja miten olet voinut sen jälkeen?") ja lopussa ("Miten voit tänään?"), mikä toisti
saman kysymyksen (12.10. testisoitot). Tilalle lääkekohtainen "onko se sopinut sinulle".
Vaikuttaa uusiin soittotehtäviin; jonossa olevat pitävät kysymyksensä (nollaa jono devissä).

Revision ID: a3c7e1d9f204
Revises: f2a9c4e6d1b8
Create Date: 2026-10-12 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c7e1d9f204"
down_revision: str | Sequence[str] | None = "f2a9c4e6d1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUDET = {
    "ANNOSMUUTOS": "Tietojemme mukaan yhden lääkkeesi annosta on muutettu. Oletko ottanut "
    "lääkettä uuden annoksen mukaan, ja onko se sopinut sinulle?",
    "LYHYT_KUURI": "Tietojemme mukaan sinulla on ollut lyhyt lääkekuuri. Saitko sen käytyä "
    "loppuun, ja sopiko se sinulle?",
}
VANHAT = {
    "ANNOSMUUTOS": "Tietojemme mukaan yhden lääkkeesi annosta on muutettu. Oletko ottanut "
    "lääkettä uuden annoksen mukaan, ja miten olet voinut sen jälkeen?",
    "LYHYT_KUURI": "Tietojemme mukaan sinulla on ollut lyhyt lääkekuuri. Saitko sen käytyä "
    "loppuun, ja miten olet voinut sen jälkeen?",
}

_muutoskysymys = sa.table(
    "muutoskysymys", sa.column("muutos_tyyppi", sa.String), sa.column("teksti", sa.Text)
)
_yleiskysymys = sa.table(
    "yleiskysymys", sa.column("tunniste", sa.String), sa.column("on_aktiivinen", sa.Boolean)
)


def _aseta_tekstit(tekstit: dict[str, str]) -> None:
    for tyyppi, teksti in tekstit.items():
        op.execute(
            _muutoskysymys.update()
            .where(_muutoskysymys.c.muutos_tyyppi == tyyppi)
            .values(teksti=teksti)
        )


def upgrade() -> None:
    _aseta_tekstit(UUDET)
    op.execute(
        _yleiskysymys.update()
        .where(_yleiskysymys.c.tunniste == "vointi")
        .values(on_aktiivinen=False)
    )


def downgrade() -> None:
    _aseta_tekstit(VANHAT)
    op.execute(
        _yleiskysymys.update()
        .where(_yleiskysymys.c.tunniste == "vointi")
        .values(on_aktiivinen=True)
    )
