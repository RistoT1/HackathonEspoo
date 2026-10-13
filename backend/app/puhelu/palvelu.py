"""Puhelun kulku: soiton kaynnistys, Twilion vuorot ja paattyminen.

Keskustelupuhelu: Twilio kutsuu /puhelu/vuoro/{istunto_id} vastattaessa ja jokaisen vastauksen
jalkeen; tilakone (keskustelu.py) paattaa seuraavan repliikin. Paattyessa tulos jaa muistiin
triagea varten ja soittojonon tehtava merkitaan valmiiksi.
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Callable
from dataclasses import replace

from sqlalchemy.orm import Session

from app.config import Settings, hae_asetukset
from app.mallit import (
    Kysymys,
    Puhelukonteksti,
    PuhelunAloitus,
    PuhelunLoppu,
    Puhelutehtava,
    PuhelutehtavanTila,
)
from app.muisti.muisti import konteksti_muistilla
from app.puhelu.agentti import Keskusteluagentti
from app.puhelu.istunnot import Istuntovarasto
from app.puhelu.keskustelu import (
    Puheluistunto,
    Rajat,
    Vuoro,
    kaiun_vuoro,
    kasittele,
    lopeta,
)
from app.puhelu.kielentarkistus import Tarkistin
from app.puhelu.loki import hae_puheluloki
from app.puhelu.puhe import (
    AVAUS,
    Kuuntelu,
    Relay,
    luo_twiml,
    relay_twiml,
    testipuhelun_repliikit,
)
from app.puhelu.soittaja import Soittaja
from app.puhelu.tietopohja import ikaluokka, puhelutyypit
from app.puhelu.vihjeet import puhelun_vihjeet
from app.puhelutehtavat import varasto as jonovarasto
from app.puhelutehtavat.palvelu import (
    TehtavaaEiLoydy,
    TilasiirtymaVirhe,
    hae_soittotiedot,
    hae_tehtava,
    vaihda_tila,
)
from app.tietokanta import tallenna_puhelun_tulos

logger = logging.getLogger(__name__)

# Testipuhelun arvonta tehdaan naista; jono on demossa pieni.
TESTIN_ARVONTARAJA = 1000

# Twilion lopulliset tilat, joissa puheluun ei vastattu.
_EI_VASTATTU = {"busy", "no-answer", "failed", "canceled"}

Valitsin = Callable[[list[Puhelutehtava]], Puhelutehtava]
# Mallin lämmitys järjestelmäohjeella (reitti ajaa sen taustatyönä soiton aikana).
# Argumentit: keskustelun alku (viestit) ja kutsu, kun lämmitys on valmis.
Lammitys = Callable[[list[dict[str, str]], Callable[[], None]], None]


def relay_asetuksista(asetukset: Settings) -> Relay | None:
    """ConversationRelay, jos PUHELU_TAPA=relay; muuten None (Gather-webhookit)."""
    if asetukset.puhelu_tapa != "relay":
        return None
    return Relay(
        tts=asetukset.relay_tts,
        aani=asetukset.relay_aani,
        stt=asetukset.relay_stt,
        stt_malli=asetukset.relay_stt_malli or None,
        tauko_ms=asetukset.relay_puheen_tauko_ms,
        vihjeet=asetukset.relay_vihjeet,
        osittaiset=asetukset.relay_osittaiset,
    )


def rajat_asetuksista(asetukset: Settings) -> Rajat:
    return Rajat(varmuusraja=asetukset.matala_varmuus)


def kuuntelu_asetuksista(asetukset: Settings, konteksti: Puhelukonteksti | None) -> Kuuntelu:
    """Puheentunnistuksen asetukset .env:sta + puhelun omat vihjesanat."""
    lisavihjeet = tuple(
        vihje.strip() for vihje in asetukset.stt_lisavihjeet.split(",") if vihje.strip()
    )
    return Kuuntelu(
        aani=asetukset.puhe_aani,
        tauko_s=asetukset.puheen_tauko_s,
        odotus_s=asetukset.kuuntelun_odotus_s,
        malli=asetukset.stt_malli or None,
        vihjeet=puhelun_vihjeet(konteksti, lisavihjeet),
    )


def _vuoro_url(perus_url: str, istunto_id: str) -> str:
    return f"{perus_url}/puhelu/vuoro/{istunto_id}"


def _tila_url(perus_url: str, istunto_id: str) -> str:
    return f"{perus_url}/puhelu/tila/{istunto_id}"


def kaiun_url(perus_url: str, kierros: int) -> str:
    return f"{perus_url}/dev/puhelu/kaiku/vuoro/{kierros}"


def _arvo_tehtava(sessio: Session, valitse: Valitsin) -> Puhelutehtava | None:
    tehtavat = jonovarasto.listaa(sessio, tila=None, raja=TESTIN_ARVONTARAJA)
    kysyttavat = [tehtava for tehtava in tehtavat if tehtava.kysymykset]
    if not kysyttavat:
        return None
    return valitse(kysyttavat)


def _relay_ikaluokalle(relay: Relay, konteksti: Puhelukonteksti) -> Relay:
    """Puheen loppumisen tauko ikäluokan mukaan (Jere: iäkkäällä 1,8 s): liian lyhyt tauko
    katkaisee hitaasti puhuvan vanhuksen kesken lauseen. Asetus on alaraja."""
    luokka = puhelutyypit()["ikaluokat"][ikaluokka(konteksti.ika)]
    tauko_ms = max(relay.tauko_ms, round(luokka["hiljaisuus_s"] * 1000))
    return replace(relay, tauko_ms=tauko_ms)


def _kaynnista_keskustelu(
    soittaja: Soittaja,
    istunnot: Istuntovarasto,
    numero: str,
    perus_url: str,
    tehtava_id: str,
    kysymykset: list[Kysymys],
    konteksti: Puhelukonteksti,
    paivita_jono: bool,
    rajat: Rajat,
    relay: Relay | None = None,
    lammitys: Lammitys | None = None,
) -> PuhelunAloitus:
    istunto = Puheluistunto(
        istunto_id=str(uuid.uuid4()),
        tehtava_id=tehtava_id,
        kysymykset=kysymykset,
        konteksti=konteksti,
        paivita_jono=paivita_jono,
        rajat=rajat,
    )
    # Istunto ensin: Twilio voi kutsua vuoroa heti, kun puheluun vastataan.
    istunnot.lisaa(istunto)
    if lammitys is not None:
        # Järjestelmäohje mallin välimuistiin soiton ajaksi (Gemma 12B M2:lla: ohjeen
        # käsittely jopa 43 s, avauksen aikana ei ehdi; mitattu 11.10.).
        istunto.lammitetty = True
        lammitys(istunto.lammitysviestit(), istunto.merkitse_malli_lammin)
    tila_url = _tila_url(perus_url, istunto.istunto_id)
    try:
        if relay is not None:
            ws_url = perus_url.replace("https://", "wss://", 1)
            twiml = relay_twiml(
                f"{ws_url}/puhelu/relay/{istunto.istunto_id}",
                f"{perus_url}/puhelu/relay-loppu/{istunto.istunto_id}",
                _relay_ikaluokalle(relay, konteksti),
                puhelun_vihjeet(konteksti, ()),
            )
            puhelu_sid = soittaja.soita_twimlilla(numero, twiml, tila_url)
        else:
            vuoro_url = _vuoro_url(perus_url, istunto.istunto_id)
            puhelu_sid = soittaja.soita_webhookilla(numero, vuoro_url, tila_url)
    except Exception:
        istunnot.poista(istunto.istunto_id)
        raise
    istunto.puhelu_sid = puhelu_sid

    suunnitelma = [AVAUS, *(kysymys.teksti for kysymys in kysymykset)]
    return PuhelunAloitus(puhelu_sid=puhelu_sid, tehtava_id=tehtava_id, repliikit=suunnitelma)


def aloita_puhelu(
    sessio: Session,
    soittaja: Soittaja,
    istunnot: Istuntovarasto,
    tehtava_id: str,
    oletusnumero: str | None,
    perus_url: str,
    rajat: Rajat,
    relay: Relay | None = None,
    lammitys: Lammitys | None = None,
) -> PuhelunAloitus:
    """Keskustelupuhelu jonon tehtavalle. Tehtavan pitaa olla tilassa SOITETAAN."""
    tiedot = hae_soittotiedot(sessio, tehtava_id, oletusnumero)
    return _kaynnista_keskustelu(
        soittaja,
        istunnot,
        tiedot.puhelinnumero,
        perus_url,
        tehtava_id,
        tiedot.kysymykset,
        tiedot.konteksti,
        paivita_jono=True,
        rajat=rajat,
        relay=relay,
        lammitys=lammitys,
    )


def aloita_testikeskustelu(
    sessio: Session,
    soittaja: Soittaja,
    istunnot: Istuntovarasto,
    numero: str,
    perus_url: str,
    rajat: Rajat,
    valitse: Valitsin = random.choice,
    relay: Relay | None = None,
    lammitys: Lammitys | None = None,
    tehtava_id: str | None = None,
) -> PuhelunAloitus | None:
    """VAIN DEV: keskustelupuhelu satunnaisen (tai annetun) tehtavan kysymyksilla. Ei muuta
    jonoa. Sama tehtava_id kahdesti -> toinen puhelu muistaa ensimmaisen (demo)."""
    tehtava = hae_tehtava(sessio, tehtava_id) if tehtava_id else _arvo_tehtava(sessio, valitse)
    if tehtava is None:
        return None
    return _kaynnista_keskustelu(
        soittaja,
        istunnot,
        numero,
        perus_url,
        tehtava.id,
        tehtava.kysymykset,
        konteksti_muistilla(sessio, tehtava),
        paivita_jono=False,
        rajat=rajat,
        relay=relay,
        lammitys=lammitys,
    )


def soita_testipuhelu(
    sessio: Session,
    soittaja: Soittaja,
    numero: str,
    aani: str,
    valitse: Valitsin = random.choice,
) -> PuhelunAloitus | None:
    """VAIN DEV: lukee satunnaisen tehtavan kontekstin ja kysymykset (ei kuuntele, ei ngrokia)."""
    tehtava = _arvo_tehtava(sessio, valitse)
    if tehtava is None:
        return None
    repliikit = testipuhelun_repliikit(tehtava.kysymykset, tehtava.konteksti)
    twiml = luo_twiml(repliikit, aani)
    puhelu_sid = soittaja.soita(numero, twiml)
    return PuhelunAloitus(puhelu_sid=puhelu_sid, tehtava_id=tehtava.id, repliikit=repliikit)


def soita_kaikutesti(soittaja: Soittaja, numero: str, perus_url: str) -> str:
    """VAIN DEV: puheentunnistuksen testi. Palauttaa CallSidin."""
    return soittaja.soita_webhookilla(numero, kaiun_url(perus_url, 0), tila_url=None)


def _varmuus(kentat: dict[str, str]) -> float | None:
    try:
        return float(kentat["Confidence"])
    except (KeyError, ValueError):
        return None


def _paata(istunto: Puheluistunto, istunnot: Istuntovarasto, sessio: Session) -> None:
    """Tulos kantaan ja muistiin, minkä jälkeen jonon tila päivitetään."""
    if not istunnot.paata(istunto, lambda tulos: tallenna_puhelun_tulos(sessio, tulos)):
        return
    logger.info(
        "Puhelu paattyi: istunto=%s loppu=%s hatamerkki=%s vastauksia=%d",
        istunto.istunto_id,
        istunto.loppu,
        istunto.hatamerkki,
        len(istunto.vastaukset),
    )
    loki = hae_puheluloki(hae_asetukset())
    if loki is not None:
        otsikko = (
            f"Puhelu {istunto.istunto_id}, tehtävä {istunto.tehtava_id}, "
            f"loppu {istunto.loppu}, hätämerkki {istunto.hatamerkki}"
        )
        loki.kirjaa_keskustelu(istunto.istunto_id, otsikko, istunto.litterointi())
    if not istunto.paivita_jono:
        return

    ei_vastattu = istunto.loppu == PuhelunLoppu.EI_VASTANNUT
    uusi_tila = PuhelutehtavanTila.EI_VASTANNUT if ei_vastattu else PuhelutehtavanTila.VALMIS
    try:
        vaihda_tila(sessio, istunto.tehtava_id, uusi_tila)
    except (TehtavaaEiLoydy, TilasiirtymaVirhe) as virhe:
        logger.warning("Jonon tilaa ei paivitetty (%s): %s", istunto.tehtava_id, virhe)


def aloita_vuoro(
    istunto: Puheluistunto,
    kentat: dict[str, str],
    agentti: Keskusteluagentti,
    tarkistin: Tarkistin,
) -> "asyncio.Task[Vuoro]":
    """Vuoron käsittely taustatehtävänä: webhook voi vastata välivastauksella ja hakea
    tuloksen myöhemmin. Ei kantaa: se viimeistellään toimittavan pyynnön sessiolla."""
    puhe = kentat.get("SpeechResult")
    return asyncio.create_task(kasittele(istunto, puhe, _varmuus(kentat), agentti, tarkistin))


def viimeistele_vuoro(
    istunto: Puheluistunto, vuoro: Vuoro, istunnot: Istuntovarasto, sessio: Session
) -> None:
    """Puhelun päättyessä tulos muistiin ja jonon tila kantaan."""
    if not vuoro.kuuntele:
        _paata(istunto, istunnot, sessio)


async def kasittele_vuoro(
    istunto_id: str,
    kentat: dict[str, str],
    istunnot: Istuntovarasto,
    agentti: Keskusteluagentti,
    sessio: Session,
    tarkistin: Tarkistin,
) -> Vuoro | None:
    """Vuoro alusta loppuun ilman välivastausta. None = istuntoa ei ole."""
    istunto = istunnot.hae(istunto_id)
    if istunto is None:
        return None
    vuoro = await aloita_vuoro(istunto, kentat, agentti, tarkistin)
    viimeistele_vuoro(istunto, vuoro, istunnot, sessio)
    return vuoro


def kasittele_tila(
    istunto_id: str, puhelun_tila: str, istunnot: Istuntovarasto, sessio: Session
) -> None:
    """Twilion lopullinen tila. Paattaa puhelun, jos keskustelu ei ehtinyt sita tehda."""
    istunto = istunnot.hae(istunto_id)
    if istunto is None:
        return  # keskustelu paatti jo itse
    loppu = PuhelunLoppu.EI_VASTANNUT if puhelun_tila in _EI_VASTATTU else PuhelunLoppu.KESKEYTYI
    lopeta(istunto, loppu)
    _paata(istunto, istunnot, sessio)


def kasittele_kaiku(
    kierros: int, kentat: dict[str, str], istunnot: Istuntovarasto, stt_malli: str | None
) -> Vuoro:
    puhe = kentat.get("SpeechResult")
    vuoro, tulos = kaiun_vuoro(kierros, puhe, _varmuus(kentat), stt_malli)
    if tulos is not None:
        istunnot.lisaa_kaiku(tulos)
    return vuoro
