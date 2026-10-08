from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.kysymykset.sisalto import Kysymyssisalto, Laakeryhma
from app.mallit import MuutosTyyppi

BACKEND = Path(__file__).resolve().parents[1]


def migroi(url: str) -> None:
    """Sama skeema ja alkusisalto kuin tuotannossa: Alembic-migraatiot."""
    asetukset = Config(str(BACKEND / "alembic.ini"))
    asetukset.set_main_option("script_location", str(BACKEND / "alembic"))
    asetukset.set_main_option("sqlalchemy.url", url)
    asetukset.attributes["konfiguroi_lokitus"] = False
    command.upgrade(asetukset, "head")


@pytest.fixture
def sessiotehdas(tmp_path) -> sessionmaker[Session]:
    """SQLite-tiedosto migraatioilla: nopea, ei Postgresia."""
    url = f"sqlite:///{tmp_path / 'testi.db'}"
    migroi(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def sessio(sessiotehdas: sessionmaker[Session]):
    with sessiotehdas() as sessio:
        yield sessio


@pytest.fixture
def sisalto() -> Kysymyssisalto:
    """Testien oma sisalto puhtaille funktioille: riippumaton kannan tekstien muokkauksista."""
    return Kysymyssisalto(
        muutoskysymykset={
            tyyppi: f"Kysymys muutoksesta {tyyppi.value}?" for tyyppi in MuutosTyyppi
        },
        tarkennuskysymykset={
            "verenvuoto": "Mustelmia?",
            "kaatuminen": "Kaatunut?",
            "vasymys": "Vasynyt?",
            "lihaskivut": "Lihaskipuja?",
            "vatsa": "Vatsa?",
        },
        yleiskysymykset=(("vointi", "Miten voit?"),),
        laakeryhmat=(
            Laakeryhma(
                "verenohennus", ("warfarin",), ("verenvuoto", "kaatuminen"), True, ("B01A",)
            ),
            Laakeryhma("opioidit", ("oxycodone",), ("vasymys", "kaatuminen"), True, ("N02A",)),
            Laakeryhma("kolesterolilääkkeet", ("simvastatin",), ("lihaskivut",), False, ("C10",)),
            Laakeryhma("antibiootit", ("amoxicillin",), ("vatsa",), False, ("J01",)),
            Laakeryhma("sydänglykosidit", (), ("vatsa",), True, ("C01AA",)),
            Laakeryhma("sydänlääkkeet", (), ("kaatuminen",), False, ("C01",)),
        ),
    )
