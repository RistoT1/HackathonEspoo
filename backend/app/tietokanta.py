"""Sovelluksen omat taulut (SQLAlchemy 2.0) ja sessiot. Demodata on erikseen demo-skeemassa."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from app.config import hae_asetukset
from app.mallit import PuhelunTulos


@lru_cache
def hae_engine() -> Engine:
    # Luodaan laiskasti, jotta testit eivat tarvitse kantaa eivatka .env:ia.
    return create_engine(hae_asetukset().database_url, pool_pre_ping=True)


@lru_cache
def hae_sessiotehdas() -> sessionmaker[Session]:
    return sessionmaker(bind=hae_engine(), expire_on_commit=False)


def hae_sessio() -> Iterator[Session]:
    """FastAPI-dependency. Testit korvaavat taman SQLite-sessiolla."""
    with hae_sessiotehdas()() as sessio:
        yield sessio


_BACKEND_HAKEMISTO = Path(__file__).resolve().parents[1]


def aja_migraatiot() -> None:
    """Paivittaa skeeman uusimpaan (alembic upgrade head). Ajetaan sovelluksen kaynnistyksessa.

    Uusi migraatio mallimuutoksen jalkeen: uv run alembic revision --autogenerate -m "..."
    """
    from alembic import command
    from alembic.config import Config

    asetukset = Config(str(_BACKEND_HAKEMISTO / "alembic.ini"))
    asetukset.set_main_option("script_location", str(_BACKEND_HAKEMISTO / "alembic"))
    asetukset.set_main_option("sqlalchemy.url", hae_asetukset().database_url.replace("%", "%%"))
    asetukset.attributes["konfiguroi_lokitus"] = False
    command.upgrade(asetukset, "head")


def _nyt() -> datetime:
    return datetime.now(UTC)


def _uusi_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class PuhelutehtavaRivi(Base):
    """Yksi soitto vanhukselle. Ei nimea eika puhelinnumeroa: vain pseudonyymi vanhus_id."""

    __tablename__ = "puhelutehtava"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uusi_id)
    vanhus_id: Mapped[str] = mapped_column(String(64), index=True)
    tila: Mapped[str] = mapped_column(String(20), index=True)
    prioriteetti: Mapped[int] = mapped_column(Integer, default=0, index=True)
    perusteet: Mapped[list] = mapped_column(JSON, default=list)
    ika: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aktiivisia_laakkeita: Mapped[int] = mapped_column(Integer, default=0)
    viitepaiva: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    yritykset: Mapped[int] = mapped_column(Integer, default=0)
    kysymykset: Mapped[list] = mapped_column(JSON, default=list)  # LLM:lle
    konteksti: Mapped[dict] = mapped_column(JSON, default=dict)  # LLM:lle
    luotu: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_nyt, index=True)
    paivitetty: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_nyt, onupdate=_nyt
    )

    muutokset: Mapped[list["PuhelutehtavanMuutosRivi"]] = relationship(
        back_populates="tehtava", cascade="all, delete-orphan"
    )


class PuhelutehtavanMuutosRivi(Base):
    """Muutos, joka laukaisi tehtavan. muutosavain on uniikki: sama muutos ei jonoon kahdesti."""

    __tablename__ = "puhelutehtavan_muutos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tehtava_id: Mapped[str] = mapped_column(
        ForeignKey("puhelutehtava.id", ondelete="CASCADE"), index=True
    )
    muutosavain: Mapped[str] = mapped_column(String(255), unique=True)
    tyyppi: Mapped[str] = mapped_column(String(32))
    tapahtui: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tiedot: Mapped[dict] = mapped_column(JSON)  # Laakemuutos JSON-muodossa

    tehtava: Mapped[PuhelutehtavaRivi] = relationship(back_populates="muutokset")


class PuhelunTulosRivi(Base):
    """Puhelun pysyvä hoitajayhteenveto. Raakavastauksia tai litterointia ei tallenneta."""

    __tablename__ = "puhelun_tulos"

    istunto_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tehtava_id: Mapped[str] = mapped_column(
        ForeignKey("puhelutehtava.id", ondelete="CASCADE"), index=True
    )
    loppu: Mapped[str | None] = mapped_column(String(32), nullable=True)
    paattyi: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    yhteenveto: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class KatseluLokiRivi(Base):
    """Tehtävänäkymän katselutapahtuma; hackathonissa katsojan tunnistautumista ei ole."""

    __tablename__ = "katseluloki"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tehtava_id: Mapped[str] = mapped_column(
        ForeignKey("puhelutehtava.id", ondelete="CASCADE"), index=True
    )
    katsoja: Mapped[str] = mapped_column(String(100))
    aika: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_nyt)
    toiminto: Mapped[str] = mapped_column(
        String(30), server_default=text("'katselu'"), default="katselu"
    )


class VanhuksenYhteystietoRivi(Base):
    """Pseudonymisointi: puhelinnumero erillaan terveystiedoista, linkki vain vanhus_id:lla."""

    __tablename__ = "vanhuksen_yhteystieto"

    vanhus_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    puhelinnumero: Mapped[str] = mapped_column(String(32))
    paivitetty: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_nyt, onupdate=_nyt
    )


# --- Kysymyssisalto: tekstit ja laakeryhmat kannassa, muokataan SQL:lla (ks. backend/sql/) ---
# Luonnolliset avaimet (tyyppi, aihe, nimi), jotta rivit on helppo kirjoittaa kasin.
# on_aktiivinen = false poistaa rivin kaytosta ilman poistoa.


class MuutoskysymysRivi(Base):
    """Kysymys per muutostyyppi (UUSI_LAAKE, ANNOSMUUTOS, LOPETETTU_LAAKE, LYHYT_KUURI)."""

    __tablename__ = "muutoskysymys"

    muutos_tyyppi: Mapped[str] = mapped_column(String(32), primary_key=True)
    teksti: Mapped[str] = mapped_column(Text)
    on_aktiivinen: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class TarkennuskysymysRivi(Base):
    """Voinnin tarkennus aiheittain (esim. huimaus). Laakeryhmat viittaavat aiheeseen."""

    __tablename__ = "tarkennuskysymys"

    aihe: Mapped[str] = mapped_column(String(64), primary_key=True)
    teksti: Mapped[str] = mapped_column(Text)
    on_aktiivinen: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class YleiskysymysRivi(Base):
    """Kysytaan jokaisessa puhelussa muutosten jalkeen (esim. yleinen vointi)."""

    __tablename__ = "yleiskysymys"

    tunniste: Mapped[str] = mapped_column(String(64), primary_key=True)
    teksti: Mapped[str] = mapped_column(Text)
    jarjestys: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    on_aktiivinen: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class LaakeryhmaRivi(Base):
    """Laakeryhma. jarjestys: pienin ensin; ensimmainen avainsanaosuma ratkaisee ryhman."""

    __tablename__ = "laakeryhma"

    nimi: Mapped[str] = mapped_column(String(100), primary_key=True)
    korkea_riski: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    jarjestys: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    on_aktiivinen: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class LaakeryhmanAvainsanaRivi(Base):
    """Osuma laakkeen ainesosaan pienilla kirjaimilla, esim. 'warfarin'."""

    __tablename__ = "laakeryhman_avainsana"

    avainsana: Mapped[str] = mapped_column(String(100), primary_key=True)
    laakeryhma: Mapped[str] = mapped_column(
        ForeignKey("laakeryhma.nimi", ondelete="CASCADE", onupdate="CASCADE"), index=True
    )


class LaakeryhmanAtcRivi(Base):
    """ATC-etuliite -> laakeryhma (ensisijainen tunnistus). Pisin osuva etuliite voittaa."""

    __tablename__ = "laakeryhman_atc"

    atc_etuliite: Mapped[str] = mapped_column(String(7), primary_key=True)
    laakeryhma: Mapped[str] = mapped_column(
        ForeignKey("laakeryhma.nimi", ondelete="CASCADE", onupdate="CASCADE"), index=True
    )


class LaakeryhmanTarkennusRivi(Base):
    """Mitka tarkennukset ryhman laakkeesta kysytaan, jarjestyksessa."""

    __tablename__ = "laakeryhman_tarkennus"

    laakeryhma: Mapped[str] = mapped_column(
        ForeignKey("laakeryhma.nimi", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    aihe: Mapped[str] = mapped_column(
        ForeignKey("tarkennuskysymys.aihe", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    )
    jarjestys: Mapped[int] = mapped_column(Integer, server_default=text("0"))


def tallenna_puhelun_tulos(sessio: Session, tulos: PuhelunTulos) -> None:
    """Tallentaa tai päivittää vain triage-yhteenvedon, ei puhelun raakavastauksia."""
    rivi = sessio.get(PuhelunTulosRivi, tulos.istunto_id)
    if rivi is None:
        rivi = PuhelunTulosRivi(istunto_id=tulos.istunto_id)
        sessio.add(rivi)
    rivi.tehtava_id = tulos.tehtava_id
    rivi.loppu = tulos.loppu.value if tulos.loppu is not None else None
    rivi.paattyi = tulos.paattyi
    yhteenveto = tulos.yhteenveto
    rivi.yhteenveto = yhteenveto.model_dump(mode="json") if yhteenveto is not None else None
    sessio.commit()
