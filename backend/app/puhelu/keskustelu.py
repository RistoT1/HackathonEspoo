"""Puhelun tilakone: avaus -> lupa -> kysymykset (+ enintaan yksi tarkennus) -> lopetus.

Deterministinen runko; agentti (LLM) vain muotoilee kysymykset ja reagoi vastauksiin.
Jokainen vanhuksen lause tarkistetaan hatasanoista ENNEN agenttia. Ei Twiliota eika kantaa:
testattavissa suoraan.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from app.mallit import (
    KaikuTulos,
    KysymyksenVastaus,
    Kysymys,
    Puhelukonteksti,
    PuhelunLoppu,
    PuhelunTulos,
)
from app.puhelu.agentti import Keskusteluagentti, KiinteaAgentti, Reaktio, Repliikki
from app.puhelu.hatasanat import normalisoi, tunnista_hatamerkki
from app.puhelu.puhe import AVAUS, LOPETUS

logger = logging.getLogger(__name__)

LUPAKYSYMYS = (
    "Onko teillä hetki aikaa vastata muutamaan kysymykseen? Voitte vastata rauhassa, odotan."
)
SELVENNYSPYYNTO = "Anteeksi, en saanut ihan selvää. Voisitteko sanoa sen uudelleen, rauhassa?"
HATAOHJE = (
    "Tämä voi olla vakavaa. Lopettakaa tämä puhelu ja soittakaa heti hätänumeroon "
    "yksi yksi kaksi. Välitän tiedon myös hoitajalle."
)
HOITAJALLE = "Selvä. Välitän pyyntönne hoitajalle, ja hoitaja soittaa teille. Kiitos."
EI_NYT = "Selvä, ei haittaa. Hoitaja voi soittaa teille myöhemmin. Hyvää päivänjatkoa."
EI_KUULUNUT = "En kuullut vastausta."
EI_VASTAUSTA_LOPETUS = "En kuule teitä. Soitamme myöhemmin uudelleen. Näkemiin."

# Peräkkäiset hiljaiset vuorot, joiden jälkeen kysymys ohitetaan (lupavaiheessa lopetetaan).
MAX_HILJAISET = 2

# Pyyntö puhua ihmiselle: ohjataan aina hoitajalle. Lauseen osia, ei yksittäisiä sanoja,
# jotta "kävin lääkärissä" ei katkaise puhelua.
_HOITAJAPYYNNOT = (
    "puhua hoitaja",
    "puhua ihmise",
    "puhua oikea",
    "puhua lääkäri",
    "haluan hoitaja",
    "haluan ihmise",
    "oikean ihmisen",
    "hoitaja soittaa",
    "hoitaja soittais",
)
_KIELTAVAT_ALUT = ("ei", "en", "eipä", "eikä")
_KIELTAVAT_ILMAUKSET = ("myöhemmin", "ei nyt", "ei ole aikaa", "en ehdi", "ei sovi")


class Vaihe(StrEnum):
    ALKU = "ALKU"
    LUPA = "LUPA"
    KYSYMYS = "KYSYMYS"
    SELVENNYS = "SELVENNYS"  # matalan varmuuden vastaus pyydetty toistamaan
    JATKO = "JATKO"
    LOPPU = "LOPPU"


@dataclass(frozen=True)
class Vuoro:
    """Mita sanotaan ja jatketaanko kuuntelua. kuuntele=False = puhelu lopetetaan."""

    sanottavat: list[str]
    kuuntele: bool


@dataclass
class Puheluistunto:
    istunto_id: str
    tehtava_id: str
    kysymykset: list[Kysymys]
    konteksti: Puhelukonteksti
    paivita_jono: bool  # False = dev-testi, jonon tilaa ei muuteta
    varmuusraja: float = 0.6  # alle taman vastaus pyydetaan toistamaan (ks. config)
    vaihe: Vaihe = Vaihe.ALKU
    indeksi: int = 0
    hiljaisia: int = 0
    viimeisin_kysymys: str = ""
    historia: list[Repliikki] = field(default_factory=list)
    vastaukset: list[KysymyksenVastaus] = field(default_factory=list)
    loppu: PuhelunLoppu | None = None
    hatamerkki: str | None = None
    paattyi: datetime | None = None

    def tulos(self) -> PuhelunTulos:
        return PuhelunTulos(
            istunto_id=self.istunto_id,
            tehtava_id=self.tehtava_id,
            loppu=self.loppu,
            hatamerkki=self.hatamerkki,
            vastaukset=list(self.vastaukset),
            paattyi=self.paattyi,
        )


def haluaa_hoitajan(teksti: str) -> bool:
    normalisoitu = normalisoi(teksti)
    return any(f" {pyynto}" in normalisoitu for pyynto in _HOITAJAPYYNNOT)


def kieltaytyy(teksti: str) -> bool:
    normalisoitu = normalisoi(teksti)
    sanat = normalisoitu.split()
    alkaa_kiellolla = bool(sanat) and sanat[0] in _KIELTAVAT_ALUT
    kieltava_ilmaus = any(f" {ilmaus}" in normalisoitu for ilmaus in _KIELTAVAT_ILMAUKSET)
    return alkaa_kiellolla or kieltava_ilmaus


def lopeta(istunto: Puheluistunto, loppu: PuhelunLoppu) -> None:
    """Merkitsee puhelun paattyneeksi. Ensimmainen syy jaa voimaan."""
    if istunto.loppu is not None:
        return
    istunto.loppu = loppu
    istunto.vaihe = Vaihe.LOPPU
    istunto.paattyi = datetime.now(UTC)


def _sano(istunto: Puheluistunto, sanottavat: list[str], kuuntele: bool) -> Vuoro:
    ei_tyhjat = [teksti for teksti in sanottavat if teksti.strip()]
    for teksti in ei_tyhjat:
        istunto.historia.append(Repliikki("agentti", teksti))
    return Vuoro(sanottavat=ei_tyhjat, kuuntele=kuuntele)


def _lopetusvuoro(istunto: Puheluistunto, loppu: PuhelunLoppu, sanottavat: list[str]) -> Vuoro:
    lopeta(istunto, loppu)
    return _sano(istunto, sanottavat, kuuntele=False)


async def _muotoile(istunto: Puheluistunto, agentti: Keskusteluagentti, kysymys: Kysymys) -> str:
    try:
        teksti = await agentti.muotoile_kysymys(kysymys, istunto.konteksti, istunto.historia)
    except Exception:
        logger.exception("Agentti kaatui muotoilussa, kaytetaan kiinteaa tekstia")
        return kysymys.teksti
    return teksti.strip() or kysymys.teksti


async def _reagoi(
    istunto: Puheluistunto,
    agentti: Keskusteluagentti,
    kysymys: Kysymys,
    vastaus: str,
    jatkokysymys_sallittu: bool,
) -> Reaktio:
    try:
        return await agentti.reagoi(
            kysymys, vastaus, istunto.konteksti, istunto.historia, jatkokysymys_sallittu
        )
    except Exception:
        logger.exception("Agentti kaatui reaktiossa, kaytetaan kiinteaa kuittausta")
        return await KiinteaAgentti().reagoi(
            kysymys, vastaus, istunto.konteksti, istunto.historia, jatkokysymys_sallittu
        )


async def _kysy_seuraava(
    istunto: Puheluistunto, agentti: Keskusteluagentti, alku: list[str]
) -> Vuoro:
    kysymykset_loppuivat = istunto.indeksi >= len(istunto.kysymykset)
    if kysymykset_loppuivat:
        return _lopetusvuoro(istunto, PuhelunLoppu.VALMIS, [*alku, LOPETUS])

    kysymys = istunto.kysymykset[istunto.indeksi]
    teksti = await _muotoile(istunto, agentti, kysymys)
    istunto.vaihe = Vaihe.KYSYMYS
    istunto.viimeisin_kysymys = teksti
    istunto.hiljaisia = 0
    return _sano(istunto, [*alku, teksti], kuuntele=True)


def aloita(istunto: Puheluistunto) -> Vuoro:
    """Ensimmainen vuoro, kun puheluun vastataan: AI-ilmoitus ja lupa."""
    istunto.vaihe = Vaihe.LUPA
    istunto.viimeisin_kysymys = LUPAKYSYMYS
    return _sano(istunto, [AVAUS, LUPAKYSYMYS], kuuntele=True)


def _hiljainen_vuoro(istunto: Puheluistunto) -> Vuoro | None:
    """Toistaa kysymyksen. None = hiljaisuuksia liikaa, kysymys ohitetaan."""
    istunto.hiljaisia += 1
    if istunto.hiljaisia < MAX_HILJAISET:
        return _sano(istunto, [EI_KUULUNUT, istunto.viimeisin_kysymys], kuuntele=True)
    return None


async def kasittele(
    istunto: Puheluistunto,
    puhe: str | None,
    varmuus: float | None,
    agentti: Keskusteluagentti,
) -> Vuoro:
    """Yksi vuoro: vanhuksen puhe sisaan, seuraava repliikki ulos."""
    if istunto.vaihe == Vaihe.ALKU:
        return aloita(istunto)
    if istunto.vaihe == Vaihe.LOPPU:
        return Vuoro(sanottavat=[], kuuntele=False)

    teksti = (puhe or "").strip()
    if not teksti:
        toisto = _hiljainen_vuoro(istunto)
        if toisto is not None:
            return toisto
        if istunto.vaihe == Vaihe.LUPA:
            return _lopetusvuoro(istunto, PuhelunLoppu.EI_VASTAUSTA, [EI_VASTAUSTA_LOPETUS])
        return await _tallenna_ja_jatka(istunto, agentti, "", varmuus, kuittaa=False)

    istunto.hiljaisia = 0
    istunto.historia.append(Repliikki("vanhus", teksti))

    # Turvallisuus ensin: saanto, ei agenttia.
    hatamerkki = tunnista_hatamerkki(teksti)
    if hatamerkki is not None:
        istunto.hatamerkki = hatamerkki
        _kirjaa_vastaus(istunto, teksti, varmuus)
        return _lopetusvuoro(istunto, PuhelunLoppu.HATAMERKKI, [HATAOHJE])

    if haluaa_hoitajan(teksti):
        _kirjaa_vastaus(istunto, teksti, varmuus)
        return _lopetusvuoro(istunto, PuhelunLoppu.HALUAA_HOITAJAN, [HOITAJALLE])

    if istunto.vaihe == Vaihe.LUPA:
        if kieltaytyy(teksti):
            return _lopetusvuoro(istunto, PuhelunLoppu.EI_LUPAA, [EI_NYT])
        istunto.indeksi = 0
        return await _kysy_seuraava(istunto, agentti, alku=["Kiitos."])

    return await _tallenna_ja_jatka(istunto, agentti, teksti, varmuus, kuittaa=True)


def _kirjaa_vastaus(istunto: Puheluistunto, teksti: str, varmuus: float | None) -> None:
    """Vastaus talteen nykyiseen vaiheeseen: kysymys, tarkennus tai (hatatilanteessa) lupa."""
    if istunto.vaihe == Vaihe.KYSYMYS:
        kysymys = istunto.kysymykset[istunto.indeksi]
        istunto.vastaukset.append(
            KysymyksenVastaus(
                tunniste=kysymys.tunniste,
                kysymys=istunto.viimeisin_kysymys,
                vastaus=teksti,
                varmuus=varmuus,
            )
        )
    elif istunto.vaihe == Vaihe.SELVENNYS and istunto.vastaukset:
        istunto.vastaukset[-1].selvennys = teksti
    elif istunto.vaihe == Vaihe.JATKO and istunto.vastaukset:
        istunto.vastaukset[-1].jatkovastaus = teksti
    elif istunto.vaihe == Vaihe.LUPA:
        # Vain hata- tai hoitajapyynto kirjataan lupavaiheesta (hoitajan pitaa nahda se).
        istunto.vastaukset.append(
            KysymyksenVastaus(tunniste="lupa", kysymys=LUPAKYSYMYS, vastaus=teksti, varmuus=varmuus)
        )


def _on_epavarma(istunto: Puheluistunto, teksti: str, varmuus: float | None) -> bool:
    """Tyhja vastaus tai varmuus alle rajan. Puuttuva varmuus ei yksin tee epavarmaksi."""
    if not teksti:
        return True
    return varmuus is not None and varmuus < istunto.varmuusraja


async def _tallenna_ja_jatka(
    istunto: Puheluistunto,
    agentti: Keskusteluagentti,
    teksti: str,
    varmuus: float | None,
    kuittaa: bool,
) -> Vuoro:
    kysymys = istunto.kysymykset[istunto.indeksi]
    vaihe = istunto.vaihe
    _kirjaa_vastaus(istunto, teksti, varmuus)

    epavarma = _on_epavarma(istunto, teksti, varmuus)
    vastaus = istunto.vastaukset[-1] if istunto.vastaukset else None
    if vastaus is not None:
        if vaihe == Vaihe.JATKO:
            vastaus.epavarma = vastaus.epavarma or epavarma
        else:
            # KYSYMYS tai SELVENNYS: viimeisin yritys ratkaisee.
            vastaus.epavarma = epavarma

    # Kerran per kysymys: epaselva vastaus pyydetaan toistamaan ennen agenttia.
    pyyda_toistoa = vaihe == Vaihe.KYSYMYS and bool(teksti) and epavarma
    if pyyda_toistoa:
        istunto.vaihe = Vaihe.SELVENNYS
        return _sano(istunto, [SELVENNYSPYYNTO], kuuntele=True)

    oli_tarkennus = vaihe == Vaihe.JATKO
    reaktio = Reaktio(kuittaus="")
    if kuittaa:
        reaktio = await _reagoi(
            istunto, agentti, kysymys, teksti, jatkokysymys_sallittu=not oli_tarkennus
        )

    tarkennetaan = reaktio.jatkokysymys is not None and not oli_tarkennus
    if tarkennetaan:
        istunto.vaihe = Vaihe.JATKO
        istunto.viimeisin_kysymys = reaktio.jatkokysymys
        istunto.vastaukset[-1].jatkokysymys = reaktio.jatkokysymys
        return _sano(istunto, [reaktio.kuittaus, reaktio.jatkokysymys], kuuntele=True)

    istunto.indeksi += 1
    return await _kysy_seuraava(istunto, agentti, alku=[reaktio.kuittaus])


# --- VAIN DEV: puheentunnistuksen kaikutesti ---

KAIUN_KIERROKSET = 3
KAIUN_AVAUS = (
    "Tämä on puheentunnistuksen testi. Olen tekoäly. Sano jotain, niin kerron mitä kuulin."
)


def kaiun_vuoro(
    kierros: int, puhe: str | None, varmuus: float | None, stt_malli: str | None = None
) -> tuple[Vuoro, KaikuTulos | None]:
    """Kierros 0 = avaus. Sen jalkeen toistaa kuullun, varmuuden ja mahdollisen hatamerkin."""
    if kierros == 0:
        return Vuoro(sanottavat=[KAIUN_AVAUS], kuuntele=True), None

    kuultu = (puhe or "").strip()
    hatamerkki = tunnista_hatamerkki(kuultu) if kuultu else None
    tulos = KaikuTulos(
        kierros=kierros, kuultu=kuultu, varmuus=varmuus, hatamerkki=hatamerkki, stt_malli=stt_malli
    )

    sanottavat = [f"Kuulin: {kuultu}." if kuultu else "En kuullut mitään."]
    if varmuus is not None and kuultu:
        sanottavat.append(f"Varmuus {round(varmuus * 100)} prosenttia.")
    if hatamerkki is not None:
        sanottavat.append(f"Tunnistin hätämerkin: {hatamerkki}.")

    viimeinen = kierros >= KAIUN_KIERROKSET
    sanottavat.append("Testi päättyi. Hei hei." if viimeinen else "Sano jotain uutta.")
    return Vuoro(sanottavat=sanottavat, kuuntele=not viimeinen), tulos
