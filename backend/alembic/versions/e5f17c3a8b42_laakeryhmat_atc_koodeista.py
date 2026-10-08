"""laakeryhmat ATC-koodeista: laakeryhman_atc, puuttuvat ryhmat ja tarkennukset

Laakeryhma tunnistetaan ensisijaisesti WHO ATC -etuliitteesta (pisin osuma voittaa); nimen
avainsanat jaavat varakeinoksi, kun ATC puuttuu. Sisaltoa EI ole tarkistettu
laaketieteellisesti: tuotannossa riskiluokka Fimean Laake 75+ -luokituksesta ja kliininen
hyvaksynta. Muutokset tasta eteenpain SQL:lla (backend/sql/kysymyssisalto.sql).

Revision ID: e5f17c3a8b42
Revises: d8a3b5c71e20
Create Date: 2026-10-07 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f17c3a8b42"
down_revision: str | Sequence[str] | None = "d8a3b5c71e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUDET_TARKENNUKSET = [
    ("kuume", "Onko sinulla ollut kuumetta tai vilunväristyksiä?"),
    ("vapina", "Onko käsissä ollut vapinaa?"),
    ("sydamen_tykytys", "Onko sydän tuntunut hakkaavan tai muljahtelevan?"),
]

# (nimi, korkea_riski, jarjestys, tarkennusaiheet jarjestyksessa)
UUDET_RYHMAT = [
    ("solunsalpaajat", True, 15, ["kuume", "vatsa"]),
    ("immuunivastetta heikentävät", True, 16, ["kuume"]),
    ("psykoosilääkkeet", True, 35, ["vasymys", "kaatuminen"]),
    ("sydänglykosidit", True, 45, ["ruokahalu", "sekavuus"]),
    ("väsyttävät allergialääkkeet", True, 46, ["vasymys", "sekavuus"]),
    ("nesteenpoistolääkkeet", False, 55, ["huimaus", "ruokahalu"]),
    ("hormonaaliset syöpälääkkeet", False, 110, ["vasymys"]),
    ("kortisonit", False, 120, ["turvotus", "uni"]),
    ("kilpirauhaslääkkeet", False, 130, ["sydamen_tykytys", "uni"]),
    ("hengitystielääkkeet", False, 140, ["vapina", "sydamen_tykytys"]),
    ("allergialääkkeet", False, 150, ["vasymys"]),
    ("osteoporoosilääkkeet", False, 160, ["vatsa"]),
    ("kihtilääkkeet", False, 170, ["vatsa"]),
    ("masennuslääkkeet", False, 180, ["huimaus", "kaatuminen"]),
    ("epilepsia- ja hermokipulääkkeet", False, 190, ["vasymys", "huimaus"]),
    ("keskushermostoa stimuloivat", False, 200, ["uni", "sydamen_tykytys"]),
]

# ATC-etuliite -> ryhma. Pisin osuva etuliite voittaa (C01AA ennen C01, R06AA ennen R06).
ATC_ETULIITTEET = [
    ("B01A", "verenohennus"),
    ("N02A", "opioidit"),
    ("R05DA", "opioidit"),
    ("A10A", "insuliini"),
    ("N05B", "rauhoittavat ja unilääkkeet"),
    ("N05C", "rauhoittavat ja unilääkkeet"),
    ("N03AE", "rauhoittavat ja unilääkkeet"),
    ("N05A", "psykoosilääkkeet"),
    ("C01", "verenpaine ja sydän"),
    ("C02", "verenpaine ja sydän"),
    ("C07", "verenpaine ja sydän"),
    ("C08", "verenpaine ja sydän"),
    ("C09", "verenpaine ja sydän"),
    ("C01AA", "sydänglykosidit"),
    ("C03", "nesteenpoistolääkkeet"),
    ("C10", "kolesterolilääkkeet"),
    ("A10B", "diabeteksen tablettilääkkeet"),
    ("N06D", "muistilääkkeet"),
    ("M01A", "tulehduskipulääkkeet"),
    ("J01", "antibiootit"),
    ("L01", "solunsalpaajat"),
    ("L02", "hormonaaliset syöpälääkkeet"),
    ("L04", "immuunivastetta heikentävät"),
    ("H02", "kortisonit"),
    ("H03", "kilpirauhaslääkkeet"),
    ("R03", "hengitystielääkkeet"),
    ("R06AA", "väsyttävät allergialääkkeet"),
    ("R06AB", "väsyttävät allergialääkkeet"),
    ("R06", "allergialääkkeet"),
    ("M05B", "osteoporoosilääkkeet"),
    ("M04", "kihtilääkkeet"),
    ("N06A", "masennuslääkkeet"),
    ("N03A", "epilepsia- ja hermokipulääkkeet"),
    ("N02BF", "epilepsia- ja hermokipulääkkeet"),
    ("N06B", "keskushermostoa stimuloivat"),
]


def upgrade() -> None:
    op.create_table(
        "laakeryhman_atc",
        sa.Column("atc_etuliite", sa.String(length=7), nullable=False),
        sa.Column("laakeryhma", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["laakeryhma"], ["laakeryhma.nimi"], onupdate="CASCADE", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("atc_etuliite"),
    )
    op.create_index(
        op.f("ix_laakeryhman_atc_laakeryhma"), "laakeryhman_atc", ["laakeryhma"], unique=False
    )

    tarkennuskysymys = sa.table("tarkennuskysymys", sa.column("aihe"), sa.column("teksti"))
    laakeryhma = sa.table(
        "laakeryhma",
        sa.column("nimi"),
        sa.column("korkea_riski", sa.Boolean),
        sa.column("jarjestys"),
    )
    tarkennus = sa.table(
        "laakeryhman_tarkennus",
        sa.column("laakeryhma"),
        sa.column("aihe"),
        sa.column("jarjestys"),
    )
    atc = sa.table("laakeryhman_atc", sa.column("atc_etuliite"), sa.column("laakeryhma"))

    op.bulk_insert(tarkennuskysymys, [{"aihe": a, "teksti": t} for a, t in UUDET_TARKENNUKSET])
    op.bulk_insert(
        laakeryhma,
        [{"nimi": n, "korkea_riski": r, "jarjestys": j} for n, r, j, _ in UUDET_RYHMAT],
    )
    op.bulk_insert(
        tarkennus,
        [
            {"laakeryhma": n, "aihe": aihe, "jarjestys": (i + 1) * 10}
            for n, _, _, aiheet in UUDET_RYHMAT
            for i, aihe in enumerate(aiheet)
        ],
    )
    op.bulk_insert(atc, [{"atc_etuliite": e, "laakeryhma": n} for e, n in ATC_ETULIITTEET])


def downgrade() -> None:
    op.drop_index(op.f("ix_laakeryhman_atc_laakeryhma"), table_name="laakeryhman_atc")
    op.drop_table("laakeryhman_atc")
    uudet_ryhmat = ", ".join(f"'{n}'" for n, _, _, _ in UUDET_RYHMAT)
    op.execute(f"DELETE FROM laakeryhman_tarkennus WHERE laakeryhma IN ({uudet_ryhmat})")
    op.execute(f"DELETE FROM laakeryhma WHERE nimi IN ({uudet_ryhmat})")
    uudet_aiheet = ", ".join(f"'{a}'" for a, _ in UUDET_TARKENNUKSET)
    op.execute(f"DELETE FROM laakeryhman_tarkennus WHERE aihe IN ({uudet_aiheet})")
    op.execute(f"DELETE FROM tarkennuskysymys WHERE aihe IN ({uudet_aiheet})")
