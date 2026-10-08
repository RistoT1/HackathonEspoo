"""Kysymyssisallon luku kannasta. Vain aktiiviset rivit (on_aktiivinen = true)."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kysymykset.sisalto import Kysymyssisalto, Laakeryhma
from app.mallit import MuutosTyyppi
from app.tietokanta import (
    LaakeryhmanAtcRivi,
    LaakeryhmanAvainsanaRivi,
    LaakeryhmanTarkennusRivi,
    LaakeryhmaRivi,
    MuutoskysymysRivi,
    TarkennuskysymysRivi,
    YleiskysymysRivi,
)

logger = logging.getLogger(__name__)

_TUNNETUT_TYYPIT = {tyyppi.value for tyyppi in MuutosTyyppi}


def _muutoskysymykset(sessio: Session) -> dict[MuutosTyyppi, str]:
    kysely = select(MuutoskysymysRivi).where(MuutoskysymysRivi.on_aktiivinen.is_(True))
    kysymykset: dict[MuutosTyyppi, str] = {}
    for rivi in sessio.scalars(kysely):
        if rivi.muutos_tyyppi not in _TUNNETUT_TYYPIT:
            logger.warning("Tuntematon muutos_tyyppi taulussa muutoskysymys ohitettiin")
            continue
        kysymykset[MuutosTyyppi(rivi.muutos_tyyppi)] = rivi.teksti
    return kysymykset


def _tarkennuskysymykset(sessio: Session) -> dict[str, str]:
    kysely = select(TarkennuskysymysRivi).where(TarkennuskysymysRivi.on_aktiivinen.is_(True))
    return {rivi.aihe: rivi.teksti for rivi in sessio.scalars(kysely)}


def _yleiskysymykset(sessio: Session) -> tuple[tuple[str, str], ...]:
    kysely = (
        select(YleiskysymysRivi)
        .where(YleiskysymysRivi.on_aktiivinen.is_(True))
        .order_by(YleiskysymysRivi.jarjestys, YleiskysymysRivi.tunniste)
    )
    return tuple((rivi.tunniste, rivi.teksti) for rivi in sessio.scalars(kysely))


def _laakeryhmat(sessio: Session, aktiiviset_aiheet: set[str]) -> tuple[Laakeryhma, ...]:
    ryhmat = sessio.scalars(
        select(LaakeryhmaRivi)
        .where(LaakeryhmaRivi.on_aktiivinen.is_(True))
        .order_by(LaakeryhmaRivi.jarjestys, LaakeryhmaRivi.nimi)
    ).all()

    avainsanat: dict[str, list[str]] = {ryhma.nimi: [] for ryhma in ryhmat}
    for rivi in sessio.scalars(select(LaakeryhmanAvainsanaRivi)):
        if rivi.laakeryhma in avainsanat:
            avainsanat[rivi.laakeryhma].append(rivi.avainsana.strip().lower())

    atc_etuliitteet: dict[str, list[str]] = {ryhma.nimi: [] for ryhma in ryhmat}
    for rivi in sessio.scalars(select(LaakeryhmanAtcRivi)):
        if rivi.laakeryhma in atc_etuliitteet:
            atc_etuliitteet[rivi.laakeryhma].append(rivi.atc_etuliite.strip().upper())

    aiheet: dict[str, list[str]] = {ryhma.nimi: [] for ryhma in ryhmat}
    tarkennukset = sessio.scalars(
        select(LaakeryhmanTarkennusRivi).order_by(
            LaakeryhmanTarkennusRivi.jarjestys, LaakeryhmanTarkennusRivi.aihe
        )
    )
    for rivi in tarkennukset:
        kaytossa = rivi.laakeryhma in aiheet and rivi.aihe in aktiiviset_aiheet
        if kaytossa:
            aiheet[rivi.laakeryhma].append(rivi.aihe)

    return tuple(
        Laakeryhma(
            nimi=ryhma.nimi,
            avainsanat=tuple(avainsanat[ryhma.nimi]),
            tarkennusaiheet=tuple(aiheet[ryhma.nimi]),
            korkea_riski=ryhma.korkea_riski,
            atc_etuliitteet=tuple(atc_etuliitteet[ryhma.nimi]),
        )
        for ryhma in ryhmat
    )


def lataa_sisalto(sessio: Session) -> Kysymyssisalto:
    tarkennuskysymykset = _tarkennuskysymykset(sessio)
    return Kysymyssisalto(
        muutoskysymykset=_muutoskysymykset(sessio),
        tarkennuskysymykset=tarkennuskysymykset,
        yleiskysymykset=_yleiskysymykset(sessio),
        laakeryhmat=_laakeryhmat(sessio, set(tarkennuskysymykset)),
    )
