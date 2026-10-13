"""Soittojonon logiikka: lääkemuutokset -> puhelutehtävät, tilasiirtymät, seuraavan soiton otto.

Säännöt ovat puhtaita funktioita (muutosavain, siirtyma_sallittu); kanta on varasto.py:ssä.
"""

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.adapteri import Datalahde
from app.kysymykset.kysymykset import luo_konteksti, luo_kysymykset
from app.kysymykset.sisalto import Kysymyssisalto
from app.kysymykset.varasto import lataa_sisalto
from app.mallit import (
    Laakemuutos,
    PotilaanLaakemuutokset,
    Puhelutehtava,
    PuhelutehtavanTila,
    Soittotiedot,
    TunnistuksenTulos,
)
from app.muutokset.palvelu import hae_kaikki_laakemuutokset
from app.puhelutehtavat import varasto
from app.puhelutehtavat.priorisointi import laske_prioriteetti
from app.tietokanta import PuhelutehtavaRivi

T = PuhelutehtavanTila

# Soittoyritykset per tehtävä, ennen kuin hoitajan on otettava asia hoitaakseen.
MAX_YRITYKSET = 3

# Uusi muutos liitetään vanhuksen olemassa olevaan tehtävään, jos se odottaa soittoa tai jäi
# vastaamatta ja yrityksiä on jäljellä (silloin se palaa jonoon). Näin vanhukselle soitetaan
# kerran, ei jokaisesta muutoksesta erikseen. Muuten luodaan uusi tehtävä: muutos ei saa kadota.

_SALLITUT_SIIRTYMAT: dict[PuhelutehtavanTila, set[PuhelutehtavanTila]] = {
    T.ODOTTAA: {T.SOITETAAN, T.PERUTTU},
    T.SOITETAAN: {T.VALMIS, T.EI_VASTANNUT},
    T.EI_VASTANNUT: {T.ODOTTAA, T.PERUTTU},
    T.VALMIS: set(),
    T.PERUTTU: set(),
}


class TehtavaaEiLoydy(LookupError):
    pass


class TilasiirtymaVirhe(ValueError):
    pass


class SoittotiedotEivatSaatavilla(RuntimeError):
    """Numero annetaan vain kesken olevalle soitolle (tila SOITETAAN)."""


class PuhelinnumeroPuuttuu(RuntimeError):
    pass


class TunnistusKaynnissa(RuntimeError):
    """Rinnakkainen tunnistusajo ehti lisätä saman muutoksen ensin."""


def muutosavain(vanhus_id: str, muutos: Laakemuutos) -> str:
    """Muutoksen pysyvä tunniste: sama muutos näkyy ikkunassa monta päivää, jonoon vain kerran."""
    tapahtui = muutos.tapahtui.isoformat()
    return f"{vanhus_id}|{muutos.tyyppi.value}|{muutos.laake.koodi}|{tapahtui}"


def siirtyma_sallittu(
    nykyinen: PuhelutehtavanTila, uusi: PuhelutehtavanTila, yritykset: int
) -> bool:
    if uusi not in _SALLITUT_SIIRTYMAT[nykyinen]:
        return False
    uusi_yritys = nykyinen == T.EI_VASTANNUT and uusi == T.ODOTTAA
    if uusi_yritys and yritykset >= MAX_YRITYKSET:
        return False
    return True


def tunnista_ja_jonouta(
    datalahde: Datalahde,
    sessio: Session,
    viitepaiva: datetime,
    ikkuna_paivia: int,
    min_ika: int,
) -> TunnistuksenTulos:
    """Ajaa lääkemuutosten tunnistuksen ja vie uudet muutokset soittojonoon yhtenä transaktiona."""
    sisalto = lataa_sisalto(sessio)
    potilaat = hae_kaikki_laakemuutokset(datalahde, viitepaiva, ikkuna_paivia, min_ika)
    kaikki_avaimet = {
        muutosavain(potilas.vanhus_id, muutos)
        for potilas in potilaat
        for muutos in potilas.muutokset
    }
    jo_jonossa = varasto.olemassa_olevat_avaimet(sessio, kaikki_avaimet)

    luotuja = 0
    paivitettyja = 0
    uusia_muutoksia = 0
    try:
        # no_autoflush: rivit kirjoitetaan vasta commitissa, jolloin rinnakkaisen ajon
        # uniikkiavainvirhe tulee aina alla olevaan except-haaraan (409), ei kesken silmukan.
        with sessio.no_autoflush:
            for potilas in potilaat:
                # Datassa voi olla tuplarivejä (sama potilas, koodi, alku) -> sama avain kahdesti.
                uudet: dict[str, Laakemuutos] = {}
                for muutos in potilas.muutokset:
                    avain = muutosavain(potilas.vanhus_id, muutos)
                    if avain not in jo_jonossa:
                        uudet.setdefault(avain, muutos)
                if not uudet:
                    continue

                rivi = varasto.hae_liitettava_rivi(sessio, potilas.vanhus_id, MAX_YRITYKSET)
                if rivi is None:
                    rivi = varasto.luo_rivi(sessio, potilas.vanhus_id, viitepaiva)
                    luotuja += 1
                else:
                    rivi.tila = T.ODOTTAA.value  # EI_VASTANNUT palaa jonoon uuden syyn myötä
                    paivitettyja += 1

                for avain, muutos in uudet.items():
                    varasto.lisaa_muutos(rivi, avain, muutos)
                uusia_muutoksia += len(uudet)
                _paivita_rivin_tiedot(rivi, potilas, viitepaiva, sisalto)
        sessio.commit()
    except IntegrityError as virhe:
        sessio.rollback()
        raise TunnistusKaynnissa("Tunnistus ajettiin rinnakkain, yritä uudelleen") from virhe

    return TunnistuksenTulos(
        viitepaiva=viitepaiva,
        luotuja=luotuja,
        paivitettyja=paivitettyja,
        uusia_muutoksia=uusia_muutoksia,
    )


def _paivita_rivin_tiedot(
    rivi: PuhelutehtavaRivi,
    potilas: PotilaanLaakemuutokset,
    viitepaiva: datetime,
    sisalto: Kysymyssisalto,
) -> None:
    """Rivin tiedot lasketaan aina uudelleen kaikista sen muutoksista ja tuoreesta tilasta."""
    tehtavan_muutokset = varasto.rivin_muutokset(rivi)
    aktiivisia = len(potilas.aktiiviset_laakkeet)
    prioriteetti = laske_prioriteetti(tehtavan_muutokset, potilas.ika, aktiivisia, sisalto)
    kysymykset = luo_kysymykset(tehtavan_muutokset, sisalto)
    konteksti = luo_konteksti(tehtavan_muutokset, potilas.ika, aktiivisia, sisalto)
    rivi.kysymykset = [kysymys.model_dump(mode="json") for kysymys in kysymykset]
    rivi.konteksti = konteksti.model_dump(mode="json")
    rivi.prioriteetti = prioriteetti.pisteet
    rivi.perusteet = prioriteetti.perusteet
    rivi.ika = potilas.ika
    rivi.aktiivisia_laakkeita = aktiivisia
    rivi.viitepaiva = viitepaiva


def hae_tehtava(sessio: Session, tehtava_id: str) -> Puhelutehtava:
    rivi = varasto.hae_rivi(sessio, tehtava_id)
    if rivi is None:
        raise TehtavaaEiLoydy(tehtava_id)
    henkilotieto = varasto.hae_henkilotieto(sessio, rivi.vanhus_id)
    return varasto.dto(rivi, henkilotieto)


def ota_seuraava(sessio: Session) -> Puhelutehtava | None:
    """Varaa korkeimman prioriteetin odottavan tehtävän soitettavaksi (ODOTTAA -> SOITETAAN)."""
    rivi = varasto.hae_seuraava_odottava_rivi(sessio)
    if rivi is None:
        sessio.rollback()
        return None
    rivi.tila = T.SOITETAAN.value
    rivi.yritykset += 1
    sessio.commit()
    return varasto.dto(rivi)


def vaihda_tila(sessio: Session, tehtava_id: str, uusi: PuhelutehtavanTila) -> Puhelutehtava:
    rivi = varasto.hae_rivi(sessio, tehtava_id, lukitse=True)
    if rivi is None:
        sessio.rollback()
        raise TehtavaaEiLoydy(tehtava_id)

    nykyinen = PuhelutehtavanTila(rivi.tila)
    if not siirtyma_sallittu(nykyinen, uusi, rivi.yritykset):
        sessio.rollback()
        raise TilasiirtymaVirhe(f"Tilasiirtymä {nykyinen} -> {uusi} ei ole sallittu")

    rivi.tila = uusi.value
    if uusi == T.SOITETAAN:
        rivi.yritykset += 1
    sessio.commit()
    return varasto.dto(rivi)


def hae_soittotiedot(sessio: Session, tehtava_id: str, oletusnumero: str | None) -> Soittotiedot:
    """Numero, kysymykset ja konteksti puheluosalle. Vain kesken olevalle soitolle."""
    rivi = varasto.hae_rivi(sessio, tehtava_id)
    if rivi is None:
        raise TehtavaaEiLoydy(tehtava_id)
    if rivi.tila != T.SOITETAAN.value:
        raise SoittotiedotEivatSaatavilla("Soittotiedot vain tehtävälle tilassa SOITETAAN")

    puhelinnumero = varasto.hae_puhelinnumero(sessio, rivi.vanhus_id) or oletusnumero
    if not puhelinnumero:
        raise PuhelinnumeroPuuttuu("Vanhukselle ei ole puhelinnumeroa eikä DEMO_PUHELINNUMEROA")

    tehtava = varasto.dto(rivi)
    return Soittotiedot(
        tehtava_id=tehtava.id,
        puhelinnumero=puhelinnumero,
        kysymykset=tehtava.kysymykset,
        konteksti=tehtava.konteksti,
    )


def aseta_yhteystieto(sessio: Session, vanhus_id: str, puhelinnumero: str) -> None:
    varasto.tallenna_puhelinnumero(sessio, vanhus_id, puhelinnumero)
