"""kysymyssisalto ja laakeryhmat

Revision ID: a146c4d6d17e
Revises: 81be0d605b28
Create Date: 2026-10-07 17:54:18.600562

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a146c4d6d17e"
down_revision: str | Sequence[str] | None = "81be0d605b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- Alkusisalto. Muutokset tasta eteenpain SQL:lla (backend/sql/kysymyssisalto.sql). ---
# Kysymyksissa ei laakkeen nimea, sairautta eika epailtya haittavaikutusta (puhelu ei ole
# salattu, ei johdattelua). Laakeryhmia EI ole tarkistettu laaketieteellisesti.

MUUTOSKYSYMYKSET = [
    (
        "UUSI_LAAKE",
        "Tietojemme mukaan sinulle on merkitty uusi lääke. Oletko aloittanut sen "
        "käytön, ja miten se on sujunut?",
    ),
    (
        "ANNOSMUUTOS",
        "Tietojemme mukaan yhden lääkkeesi annosta on muutettu. Oletko ottanut "
        "lääkettä uuden annoksen mukaan, ja miten olet voinut sen jälkeen?",
    ),
    (
        "LOPETETTU_LAAKE",
        "Tietojemme mukaan yksi lääkkeesi on merkitty lopetetuksi. Oletko "
        "lopettanut sen käytön, ja onko vanhoja oireita palannut?",
    ),
    (
        "LYHYT_KUURI",
        "Tietojemme mukaan sinulla on ollut lyhyt lääkekuuri. Saitko sen käytyä "
        "loppuun, ja miten olet voinut sen jälkeen?",
    ),
]

TARKENNUSKYSYMYKSET = [
    ("huimaus", "Onko sinua huimannut, esimerkiksi kun nouset ylös?"),
    ("kaatuminen", "Oletko kaatunut tai ollut kaatumaisillasi viime aikoina?"),
    ("vasymys", "Oletko ollut tavallista väsyneempi tai uneliaampi päivällä?"),
    (
        "sekavuus",
        "Onko sinusta tai läheisistäsi tuntunut, että ajatus kulkee eri tavalla kuin ennen?",
    ),
    ("vatsa", "Onko vatsasi toiminut tavalliseen tapaan?"),
    ("ruokahalu", "Onko ruoka maistunut ja oletko jaksanut juoda tavalliseen tapaan?"),
    ("turvotus", "Oletko huomannut turvotusta esimerkiksi nilkoissa?"),
    ("verenvuoto", "Onko sinulle tullut mustelmia tai verenvuotoa tavallista helpommin?"),
    ("lihaskivut", "Onko lihaksissa ollut uutta kipua tai heikkoutta?"),
    ("iho", "Onko iholle tullut ihottumaa tai kutinaa?"),
    ("uni", "Miten olet nukkunut viime öinä?"),
]

YLEISKYSYMYKSET = [
    ("vointi", "Miten voit tänään?", 10),
]

# (nimi, korkea_riski, jarjestys, avainsanat, tarkennusaiheet jarjestyksessa)
LAAKERYHMAT = [
    (
        "verenohennus",
        True,
        10,
        ["warfarin", "clopidogrel", "apixaban", "rivaroxaban", "dabigatran", "heparin"],
        ["verenvuoto", "kaatuminen"],
    ),
    (
        "opioidit",
        True,
        20,
        [
            "oxycodone",
            "hydrocodone",
            "meperidine",
            "morphine",
            "fentanyl",
            "tramadol",
            "codeine",
            "buprenorphine",
        ],
        ["vasymys", "sekavuus", "kaatuminen"],
    ),
    ("insuliini", True, 30, ["insulin"], ["huimaus", "ruokahalu"]),
    (
        "rauhoittavat ja unilääkkeet",
        True,
        40,
        ["alprazolam", "diazepam", "lorazepam", "temazepam", "zolpidem", "oxazepam"],
        ["vasymys", "kaatuminen"],
    ),
    (
        "verenpaine ja sydän",
        False,
        50,
        [
            "amlodipine",
            "hydrochlorothiazide",
            "atenolol",
            "verapamil",
            "metoprolol",
            "furosemide",
            "digoxin",
            "nitroglycerin",
            "captopril",
            "lisinopril",
            "losartan",
            "enalapril",
            "ramipril",
            "bisoprolol",
            "diltiazem",
            "spironolactone",
        ],
        ["huimaus", "kaatuminen"],
    ),
    (
        "kolesterolilääkkeet",
        False,
        60,
        ["simvastatin", "atorvastatin", "rosuvastatin", "pravastatin", "lovastatin"],
        ["lihaskivut"],
    ),
    (
        "diabeteksen tablettilääkkeet",
        False,
        70,
        ["metformin", "glipizide", "glyburide", "sitagliptin"],
        ["vatsa", "ruokahalu"],
    ),
    (
        "muistilääkkeet",
        False,
        80,
        ["donepezil", "galantamine", "rivastigmine", "memantine"],
        ["vatsa", "uni"],
    ),
    (
        "tulehduskipulääkkeet",
        False,
        90,
        ["ibuprofen", "naproxen", "diclofenac", "celecoxib"],
        ["vatsa", "turvotus"],
    ),
    (
        "antibiootit",
        False,
        100,
        [
            "amoxicillin",
            "penicillin",
            "cefaclor",
            "cefuroxime",
            "ciprofloxacin",
            "nitrofurantoin",
            "doxycycline",
            "azithromycin",
        ],
        ["vatsa", "iho"],
    ),
]


def _lisaa_alkusisalto() -> None:
    muutoskysymys = sa.table("muutoskysymys", sa.column("muutos_tyyppi"), sa.column("teksti"))
    tarkennuskysymys = sa.table("tarkennuskysymys", sa.column("aihe"), sa.column("teksti"))
    yleiskysymys = sa.table(
        "yleiskysymys", sa.column("tunniste"), sa.column("teksti"), sa.column("jarjestys")
    )
    laakeryhma = sa.table(
        "laakeryhma",
        sa.column("nimi"),
        sa.column("korkea_riski", sa.Boolean),
        sa.column("jarjestys"),
    )
    avainsana = sa.table("laakeryhman_avainsana", sa.column("avainsana"), sa.column("laakeryhma"))
    tarkennus = sa.table(
        "laakeryhman_tarkennus",
        sa.column("laakeryhma"),
        sa.column("aihe"),
        sa.column("jarjestys"),
    )

    op.bulk_insert(muutoskysymys, [{"muutos_tyyppi": t, "teksti": x} for t, x in MUUTOSKYSYMYKSET])
    op.bulk_insert(tarkennuskysymys, [{"aihe": a, "teksti": x} for a, x in TARKENNUSKYSYMYKSET])
    op.bulk_insert(
        yleiskysymys,
        [{"tunniste": t, "teksti": x, "jarjestys": j} for t, x, j in YLEISKYSYMYKSET],
    )
    op.bulk_insert(
        laakeryhma,
        [{"nimi": n, "korkea_riski": r, "jarjestys": j} for n, r, j, _, _ in LAAKERYHMAT],
    )
    op.bulk_insert(
        avainsana,
        [{"avainsana": a, "laakeryhma": n} for n, _, _, sanat, _ in LAAKERYHMAT for a in sanat],
    )
    op.bulk_insert(
        tarkennus,
        [
            {"laakeryhma": n, "aihe": aihe, "jarjestys": (i + 1) * 10}
            for n, _, _, _, aiheet in LAAKERYHMAT
            for i, aihe in enumerate(aiheet)
        ],
    )


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "laakeryhma",
        sa.Column("nimi", sa.String(length=100), nullable=False),
        sa.Column("korkea_riski", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("jarjestys", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("on_aktiivinen", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("nimi"),
    )
    op.create_table(
        "muutoskysymys",
        sa.Column("muutos_tyyppi", sa.String(length=32), nullable=False),
        sa.Column("teksti", sa.Text(), nullable=False),
        sa.Column("on_aktiivinen", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("muutos_tyyppi"),
    )
    op.create_table(
        "tarkennuskysymys",
        sa.Column("aihe", sa.String(length=64), nullable=False),
        sa.Column("teksti", sa.Text(), nullable=False),
        sa.Column("on_aktiivinen", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("aihe"),
    )
    op.create_table(
        "yleiskysymys",
        sa.Column("tunniste", sa.String(length=64), nullable=False),
        sa.Column("teksti", sa.Text(), nullable=False),
        sa.Column("jarjestys", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("on_aktiivinen", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("tunniste"),
    )
    op.create_table(
        "laakeryhman_avainsana",
        sa.Column("avainsana", sa.String(length=100), nullable=False),
        sa.Column("laakeryhma", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["laakeryhma"], ["laakeryhma.nimi"], onupdate="CASCADE", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("avainsana"),
    )
    op.create_index(
        op.f("ix_laakeryhman_avainsana_laakeryhma"),
        "laakeryhman_avainsana",
        ["laakeryhma"],
        unique=False,
    )
    op.create_table(
        "laakeryhman_tarkennus",
        sa.Column("laakeryhma", sa.String(length=100), nullable=False),
        sa.Column("aihe", sa.String(length=64), nullable=False),
        sa.Column("jarjestys", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["aihe"], ["tarkennuskysymys.aihe"], onupdate="CASCADE", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["laakeryhma"], ["laakeryhma.nimi"], onupdate="CASCADE", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("laakeryhma", "aihe"),
    )
    # ### end Alembic commands ###
    _lisaa_alkusisalto()


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("laakeryhman_tarkennus")
    op.drop_index(op.f("ix_laakeryhman_avainsana_laakeryhma"), table_name="laakeryhman_avainsana")
    op.drop_table("laakeryhman_avainsana")
    op.drop_table("yleiskysymys")
    op.drop_table("tarkennuskysymys")
    op.drop_table("muutoskysymys")
    op.drop_table("laakeryhma")
    # ### end Alembic commands ###
