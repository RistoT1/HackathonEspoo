"""Puhelutehtavien tietokantakasittely. Ainoa paikka, joka koskee puhelutehtava-tauluihin."""

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session, selectinload

from app.mallit import Kysymys, Laakemuutos, Puhelukonteksti, Puhelutehtava, PuhelutehtavanTila
from app.tietokanta import (
    PuhelutehtavanMuutosRivi,
    PuhelutehtavaRivi,
    VanhuksenYhteystietoRivi,
)

# Postgresin parametriraja on 65535; haetaan avaimet erissa.
_AVAINERA = 1000


def _utc(aika: datetime) -> datetime:
    """SQLite palauttaa aikaleimat ilman aikavyohyketta; tallennus on aina UTC."""
    if aika.tzinfo is None:
        return aika.replace(tzinfo=UTC)
    return aika


def rivin_muutokset(rivi: PuhelutehtavaRivi) -> list[Laakemuutos]:
    """Tehtavan muutokset uusin ensin."""
    muutokset = [Laakemuutos.model_validate(m.tiedot) for m in rivi.muutokset]
    muutokset.sort(key=lambda muutos: muutos.tapahtui, reverse=True)
    return muutokset


def dto(rivi: PuhelutehtavaRivi) -> Puhelutehtava:
    muutokset = rivin_muutokset(rivi)
    return Puhelutehtava(
        id=rivi.id,
        vanhus_id=rivi.vanhus_id,
        tila=PuhelutehtavanTila(rivi.tila),
        prioriteetti=rivi.prioriteetti,
        perusteet=list(rivi.perusteet),
        ika=rivi.ika,
        aktiivisia_laakkeita=rivi.aktiivisia_laakkeita,
        viitepaiva=_utc(rivi.viitepaiva),
        yritykset=rivi.yritykset,
        muutokset=muutokset,
        kysymykset=[Kysymys.model_validate(k) for k in rivi.kysymykset],
        konteksti=Puhelukonteksti.model_validate(rivi.konteksti),
        luotu=_utc(rivi.luotu),
        paivitetty=_utc(rivi.paivitetty),
    )


def olemassa_olevat_avaimet(sessio: Session, avaimet: Iterable[str]) -> set[str]:
    kaikki = list(avaimet)
    loytyneet: set[str] = set()
    for alku in range(0, len(kaikki), _AVAINERA):
        era = kaikki[alku : alku + _AVAINERA]
        kysely = select(PuhelutehtavanMuutosRivi.muutosavain).where(
            PuhelutehtavanMuutosRivi.muutosavain.in_(era)
        )
        loytyneet.update(sessio.scalars(kysely))
    return loytyneet


def hae_rivi(sessio: Session, tehtava_id: str, lukitse: bool = False) -> PuhelutehtavaRivi | None:
    kysely = select(PuhelutehtavaRivi).where(PuhelutehtavaRivi.id == tehtava_id)
    if lukitse:
        kysely = kysely.with_for_update()
    return sessio.scalars(kysely).first()


def hae_liitettava_rivi(
    sessio: Session, vanhus_id: str, max_yritykset: int
) -> PuhelutehtavaRivi | None:
    """Odottava tehtava, tai vastaamatta jaanyt jolla yrityksia on jaljella."""
    odottaa = PuhelutehtavaRivi.tila == PuhelutehtavanTila.ODOTTAA.value
    voi_yrittaa_uudelleen = and_(
        PuhelutehtavaRivi.tila == PuhelutehtavanTila.EI_VASTANNUT.value,
        PuhelutehtavaRivi.yritykset < max_yritykset,
    )
    kysely = (
        select(PuhelutehtavaRivi)
        .where(PuhelutehtavaRivi.vanhus_id == vanhus_id)
        .where(or_(odottaa, voi_yrittaa_uudelleen))
        .order_by(PuhelutehtavaRivi.luotu)
        .limit(1)
        .with_for_update()
    )
    return sessio.scalars(kysely).first()


def luo_rivi(sessio: Session, vanhus_id: str, viitepaiva: datetime) -> PuhelutehtavaRivi:
    rivi = PuhelutehtavaRivi(
        vanhus_id=vanhus_id,
        tila=PuhelutehtavanTila.ODOTTAA.value,
        viitepaiva=viitepaiva,
        yritykset=0,
        kysymykset=[],
        konteksti={},
        perusteet=[],
    )
    sessio.add(rivi)
    return rivi


def lisaa_muutos(rivi: PuhelutehtavaRivi, avain: str, muutos: Laakemuutos) -> None:
    rivi.muutokset.append(
        PuhelutehtavanMuutosRivi(
            muutosavain=avain,
            tyyppi=muutos.tyyppi.value,
            tapahtui=muutos.tapahtui,
            tiedot=muutos.model_dump(mode="json"),
        )
    )


def hae_seuraava_odottava_rivi(sessio: Session) -> PuhelutehtavaRivi | None:
    """Korkein prioriteetti, sitten vanhin.

    SKIP LOCKED: rinnakkaiset soittajat eivat saa samaa tehtavaa.
    """
    kysely = (
        select(PuhelutehtavaRivi)
        .where(PuhelutehtavaRivi.tila == PuhelutehtavanTila.ODOTTAA.value)
        .order_by(PuhelutehtavaRivi.prioriteetti.desc(), PuhelutehtavaRivi.luotu)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return sessio.scalars(kysely).first()


def listaa(sessio: Session, tila: PuhelutehtavanTila | None, raja: int) -> list[Puhelutehtava]:
    kysely = (
        select(PuhelutehtavaRivi)
        .options(selectinload(PuhelutehtavaRivi.muutokset))
        .order_by(PuhelutehtavaRivi.prioriteetti.desc(), PuhelutehtavaRivi.luotu)
        .limit(raja)
    )
    if tila is not None:
        kysely = kysely.where(PuhelutehtavaRivi.tila == tila.value)
    return [dto(rivi) for rivi in sessio.scalars(kysely)]


def hae_puhelinnumero(sessio: Session, vanhus_id: str) -> str | None:
    rivi = sessio.get(VanhuksenYhteystietoRivi, vanhus_id)
    if rivi is None:
        return None
    return rivi.puhelinnumero


def tallenna_puhelinnumero(sessio: Session, vanhus_id: str, puhelinnumero: str) -> None:
    rivi = sessio.get(VanhuksenYhteystietoRivi, vanhus_id)
    if rivi is None:
        sessio.add(VanhuksenYhteystietoRivi(vanhus_id=vanhus_id, puhelinnumero=puhelinnumero))
    else:
        rivi.puhelinnumero = puhelinnumero
    sessio.commit()


def poista_kaikki(sessio: Session) -> int:
    """VAIN DEV-nollaukseen. Tuotannossa tehtavia ei poisteta."""
    sessio.execute(delete(PuhelutehtavanMuutosRivi))
    poistettuja = sessio.execute(delete(PuhelutehtavaRivi)).rowcount
    sessio.commit()
    return poistettuja
