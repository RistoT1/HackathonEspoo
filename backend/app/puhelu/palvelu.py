"""Puhelun kulku: soiton kaynnistys, Twilion vuorot ja paattyminen.

Keskustelupuhelu: Twilio kutsuu /puhelu/vuoro/{istunto_id} vastattaessa ja jokaisen vastauksen
jalkeen; tilakone (keskustelu.py) paattaa seuraavan repliikin. Paattyessa tulos jaa muistiin
triagea varten ja soittojonon tehtava merkitaan valmiiksi.
"""

import logging
import random
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config import Settings
from app.mallit import (
    Kysymys,
    Puhelukonteksti,
    PuhelunAloitus,
    PuhelunLoppu,
    Puhelutehtava,
    PuhelutehtavanTila,
)
from app.puhelu.agentti import Keskusteluagentti
from app.puhelu.istunnot import Istuntovarasto
from app.puhelu.keskustelu import (
    LUPAKYSYMYS,
    Puheluistunto,
    Vuoro,
    kaiun_vuoro,
    kasittele,
    lopeta,
)
from app.puhelu.puhe import AVAUS, Kuuntelu, luo_twiml, testipuhelun_repliikit
from app.puhelu.soittaja import Soittaja
from app.puhelu.vihjeet import puhelun_vihjeet
from app.puhelutehtavat import varasto as jonovarasto
from app.puhelutehtavat.palvelu import (
    TehtavaaEiLoydy,
    TilasiirtymaVirhe,
    hae_soittotiedot,
    vaihda_tila,
)

logger = logging.getLogger(__name__)

# Testipuhelun arvonta tehdaan naista; jono on demossa pieni.
TESTIN_ARVONTARAJA = 1000

# Twilion lopulliset tilat, joissa puheluun ei vastattu.
_EI_VASTATTU = {"busy", "no-answer", "failed", "canceled"}

Valitsin = Callable[[list[Puhelutehtava]], Puhelutehtava]


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


def _kaynnista_keskustelu(
    soittaja: Soittaja,
    istunnot: Istuntovarasto,
    numero: str,
    perus_url: str,
    tehtava_id: str,
    kysymykset: list[Kysymys],
    konteksti: Puhelukonteksti,
    paivita_jono: bool,
    varmuusraja: float,
) -> PuhelunAloitus:
    istunto = Puheluistunto(
        istunto_id=str(uuid.uuid4()),
        tehtava_id=tehtava_id,
        kysymykset=kysymykset,
        konteksti=konteksti,
        paivita_jono=paivita_jono,
        varmuusraja=varmuusraja,
    )
    # Istunto ensin: Twilio voi kutsua vuoroa heti, kun puheluun vastataan.
    istunnot.lisaa(istunto)
    try:
        puhelu_sid = soittaja.soita_webhookilla(
            numero,
            _vuoro_url(perus_url, istunto.istunto_id),
            _tila_url(perus_url, istunto.istunto_id),
        )
    except Exception:
        istunnot.poista(istunto.istunto_id)
        raise

    suunnitelma = [AVAUS, LUPAKYSYMYS, *(kysymys.teksti for kysymys in kysymykset)]
    return PuhelunAloitus(puhelu_sid=puhelu_sid, tehtava_id=tehtava_id, repliikit=suunnitelma)


def aloita_puhelu(
    sessio: Session,
    soittaja: Soittaja,
    istunnot: Istuntovarasto,
    tehtava_id: str,
    oletusnumero: str | None,
    perus_url: str,
    varmuusraja: float,
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
        varmuusraja=varmuusraja,
    )


def aloita_testikeskustelu(
    sessio: Session,
    soittaja: Soittaja,
    istunnot: Istuntovarasto,
    numero: str,
    perus_url: str,
    varmuusraja: float,
    valitse: Valitsin = random.choice,
) -> PuhelunAloitus | None:
    """VAIN DEV: keskustelupuhelu satunnaisen tehtavan kysymyksilla. Ei muuta jonoa."""
    tehtava = _arvo_tehtava(sessio, valitse)
    if tehtava is None:
        return None
    return _kaynnista_keskustelu(
        soittaja,
        istunnot,
        numero,
        perus_url,
        tehtava.id,
        tehtava.kysymykset,
        tehtava.konteksti,
        paivita_jono=False,
        varmuusraja=varmuusraja,
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
    """Tulos muistiin ja jonon tila paivitetaan. Kerran per puhelu."""
    if not istunnot.paata(istunto):
        return
    logger.info(
        "Puhelu paattyi: istunto=%s loppu=%s hatamerkki=%s vastauksia=%d",
        istunto.istunto_id,
        istunto.loppu,
        istunto.hatamerkki,
        len(istunto.vastaukset),
    )
    if not istunto.paivita_jono:
        return

    ei_vastattu = istunto.loppu == PuhelunLoppu.EI_VASTANNUT
    uusi_tila = PuhelutehtavanTila.EI_VASTANNUT if ei_vastattu else PuhelutehtavanTila.VALMIS
    try:
        vaihda_tila(sessio, istunto.tehtava_id, uusi_tila)
    except (TehtavaaEiLoydy, TilasiirtymaVirhe) as virhe:
        logger.warning("Jonon tilaa ei paivitetty (%s): %s", istunto.tehtava_id, virhe)


async def kasittele_vuoro(
    istunto_id: str,
    kentat: dict[str, str],
    istunnot: Istuntovarasto,
    agentti: Keskusteluagentti,
    sessio: Session,
) -> Vuoro | None:
    """Twilion vuoro. None = istuntoa ei ole (esim. backend kaynnistyi uudelleen)."""
    istunto = istunnot.hae(istunto_id)
    if istunto is None:
        return None

    vuoro = await kasittele(istunto, kentat.get("SpeechResult"), _varmuus(kentat), agentti)
    if not vuoro.kuuntele:
        _paata(istunto, istunnot, sessio)
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
