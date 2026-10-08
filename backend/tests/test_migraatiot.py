"""Alembic-migraatiot vastaavat malleja: unohtunut migraatio kaataa taman testin."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.tietokanta import Base

BACKEND = Path(__file__).resolve().parents[1]


def test_migraatiot_vastaavat_malleja(tmp_path):
    url = f"sqlite:///{tmp_path / 'migraatiot.db'}"
    asetukset = Config(str(BACKEND / "alembic.ini"))
    asetukset.set_main_option("script_location", str(BACKEND / "alembic"))
    asetukset.set_main_option("sqlalchemy.url", url)
    asetukset.attributes["konfiguroi_lokitus"] = False

    command.upgrade(asetukset, "head")
    command.upgrade(asetukset, "head")  # idempotentti

    with create_engine(url).connect() as yhteys:
        erot = compare_metadata(MigrationContext.configure(yhteys), Base.metadata)
    assert erot == []
