"""Mallin lämmitys: malli ja järjestelmäohjeen välimuisti valmiina ennen ensimmäistä soittoa.

Ollaman uudelleenkäynnistys pudottaa mallin ja välimuistin. 12.10. illalla kolme puhelua
alkoi kylmällä mallilla: ensimmäinen vuoro kulki varatoiminnalla (listan kysymys ilman
lisäkysymyksiä). Siksi:
1. Käynnistyksessä (lammita_kaynnistyksessa) backend odottaa, kunnes malli on lämmin, ja
   ajaa simuloidun puhelun oikean keskustelukoodin läpi. Kylmänä käynnistys kestää pari
   minuuttia, mutta ensimmäinen oikea puhelu on lämmin.
2. Lämmitysvahti tarkistaa sen jälkeen LLM_LAMMITYSVALI_S välein, kun puheluita ei ole,
   ja lataa mallin uudelleen, jos Ollama on käynnistetty uudelleen.
Mitattu 12.10.: kylmä lämmitys 52,7 s, lämmin 0,4-6 s.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from app.mallit import Kysymys, Muutoskonteksti, MuutosTyyppi, Puhelukonteksti
from app.puhelu.agentti import Keskusteluagentti, LlmAgentti
from app.puhelu.keskustelu import Puheluistunto, kasittele

logger = logging.getLogger(__name__)

# Lämmin malli vastaa välimuistista muutamassa sekunnissa; kylmä lataus kestää ~50 s.
LAMMIN_RAJA_S = 15.0
# Kylmän mallin uusinta: välimuisti oppii haarakohdan vasta parin ajon jälkeen (11.10.).
KYLMAN_UUSINTA_S = 5.0
LAMMITYKSEN_AIKARAJA_S = 180.0
# Käynnistyksessä: kylmä kierros ~100 s (12.10.), joten enintään ~10 min ennen luovutusta.
MAX_KAYNNISTYSKIERROKSET = 6
SIMULOITU_VASTAUS = "Ihan hyvin menee, kiitos."


def esimerkkipuhelut() -> list[Puheluistunto]:
    """Kaksi eri ikäluokan esimerkkipuhelua (uudet istunnot joka kerta). Välimuisti käyttää
    vain tallettamiaan tiloja: se oppii haarakohdan (puhelukohtaisen osan alku, "TÄMÄN
    PUHELUN TIEDOT") vasta, kun kaksi pyyntöä on eronnut siinä (CLAUDE.md, mitattu 11.10.).
    12.10. ilta: yhdellä esimerkillä lämmitetty malli oli kylmä ensimmäiselle oikealle
    puhelulle; kahdella erilainen kolmas puhelu lämpeni 3,9 s:ssa."""
    esimerkit = [
        (
            85,
            MuutosTyyppi.UUSI_LAAKE,
            "verenpainelääke",
            ["Oletko aloittanut uuden lääkkeen käytön?", "Onko sinua huimannut?"],
        ),
        (
            70,
            MuutosTyyppi.LYHYT_KUURI,
            "antibiootti",
            ["Saitko kuurin käytyä loppuun?", "Onko sinulla ollut kuumetta?"],
        ),
    ]
    return [
        Puheluistunto(
            istunto_id=f"lammitys-{numero}",
            tehtava_id=f"lammitys-{numero}",
            kysymykset=[
                Kysymys(tunniste=f"lammitys.{indeksi}", teksti=kysymys)
                for indeksi, kysymys in enumerate(kysymykset)
            ],
            konteksti=Puhelukonteksti(
                ika=ika,
                aktiivisia_laakkeita=4,
                muutokset=[Muutoskonteksti(tyyppi=tyyppi, laakeryhma=ryhma, paivia_sitten=3)],
            ),
            paivita_jono=False,
        )
        for numero, (ika, tyyppi, ryhma, kysymykset) in enumerate(esimerkit, start=1)
    ]


async def lammita_malli(agentti: LlmAgentti) -> float:
    """Lämmitys kaikilla esimerkeillä: puhelukohtaiset tiedot ovat ohjeen lopussa, joten
    esimerkkipuhelut lämmittävät yhteisen alun kaikille. Palauttaa yhteiskeston sekunteina;
    httpx.HTTPError, jos malli ei vastaa."""
    kesto = 0.0
    for istunto in esimerkkipuhelut():
        viestit = istunto.lammitysviestit()
        kesto += await agentti.lammita_ja_mittaa(viestit, LAMMITYKSEN_AIKARAJA_S)
    return kesto


async def simuloi_puhelu(istunto: Puheluistunto, agentti: Keskusteluagentti) -> float | None:
    """Avaus ja yksi henkilön vastaus oikean keskustelukoodin läpi: sama järjestelmäohje,
    piilo-ohjeet ja jälkikäsittely kuin puhelussa, ja malli myös tuottaa tekstiä (lämmitys
    pelkästään lukee ohjeen). Palauttaa vuoron keston; None, jos malli ei vastannut."""
    await kasittele(istunto, None, None, agentti)
    alku = time.perf_counter()
    await kasittele(istunto, SIMULOITU_VASTAUS, 0.9, agentti)
    kesto = time.perf_counter() - alku
    malli_vastasi = istunto.mittaukset.get("malli") is not None
    if not malli_vastasi:
        return None
    return kesto


async def lammita_kaynnistyksessa(
    agentti: Keskusteluagentti, lammita: Callable[[], Awaitable[float]]
) -> bool:
    """Backendin käynnistys odottaa tätä: lämmitys, kunnes malli vastaa välimuistista, sitten
    simuloidut puhelut. False = malli ei vastannut tai ei lämmennyt (vahti jatkaa yrityksiä,
    ja dev-soitto kertoo kylmästä mallista)."""
    logger.info("Mallin lämmitys käynnistyksessä: odotetaan, kunnes malli on lämmin")
    for kierros in range(1, MAX_KAYNNISTYSKIERROKSET + 1):
        try:
            kesto = await lammita()
        except httpx.HTTPError as virhe:
            logger.warning("Malli ei vastaa (%s): käynnistetään ilman lämmintä mallia", virhe)
            return False
        lammin = kesto <= LAMMIN_RAJA_S
        tila = "lämmin" if lammin else "kylmä, uudelleen"
        logger.info("Lämmitys %d/%d: %.1f s (%s)", kierros, MAX_KAYNNISTYSKIERROKSET, kesto, tila)
        if lammin:
            break
    else:
        logger.warning("Malli ei lämmennyt %d kierroksessa", MAX_KAYNNISTYSKIERROKSET)
        return False

    for istunto in esimerkkipuhelut():
        vuoro_s = await simuloi_puhelu(istunto, agentti)
        if vuoro_s is None:
            logger.warning("Simuloitu puhelu: malli ei vastannut (%s)", istunto.syyt)
            return False
        logger.info("Simuloitu puhelu (ikä %s): vuoro %.1f s", istunto.konteksti.ika, vuoro_s)
    logger.info("Malli valmis puheluihin")
    return True


class Lammitysvahti:
    """Tapahtumasilmukan tehtävä (kaynnista/pysayta FastAPIn elinkaaressa)."""

    def __init__(
        self,
        lammita: Callable[[], Awaitable[float]],
        puheluita_kaynnissa: Callable[[], bool],
        vali_s: float,
        lammin: bool = False,
    ) -> None:
        self.lammin = lammin  # käynnistyslämmityksen tulos
        self.viimeisin_s: float | None = None
        self._lammita = lammita
        # Puhelun oma lämmitys ja vuorot menevät edelle: Ollama palvelee yhtä kerrallaan.
        self._puheluita_kaynnissa = puheluita_kaynnissa
        self._vali_s = vali_s
        self._tehtava: asyncio.Task[None] | None = None

    def kaynnista(self) -> None:
        self._tehtava = asyncio.get_running_loop().create_task(self.aja(), name="lammitysvahti")

    async def pysayta(self) -> None:
        """Peruu vahdin ja kesken olevan lämmityspyynnön (Ollama keskeyttää perutun)."""
        if self._tehtava is None:
            return
        self._tehtava.cancel()
        await asyncio.wait([self._tehtava])

    async def kierros(self) -> None:
        if self._puheluita_kaynnissa():
            return
        oli_lammin = self.lammin
        try:
            kesto = await self._lammita()
        except httpx.HTTPError as virhe:
            self.lammin = False
            logger.warning("Lämmitysvahti: malli ei vastaa: %s", virhe)
            return

        self.viimeisin_s = kesto
        self.lammin = kesto <= LAMMIN_RAJA_S
        if not self.lammin:
            # Myös triage-analyysin aikana: pyyntö jonotti analyysin takana.
            logger.info("Lämmitysvahti: lämmitys %.1f s, malli oli kylmä tai varattu", kesto)
        elif not oli_lammin:
            logger.info("Lämmitysvahti: malli lämmin (%.1f s)", kesto)

    async def aja(self) -> None:
        # Käynnistyksessä juuri lämmitetty: ensimmäinen tarkistus vasta välin päästä.
        if self.lammin:
            await asyncio.sleep(self._vali_s)
        while True:
            await self.kierros()
            odotus = self._vali_s if self.lammin else KYLMAN_UUSINTA_S
            await asyncio.sleep(odotus)


_VAHTI: Lammitysvahti | None = None


def aseta_lammitysvahti(vahti: Lammitysvahti | None) -> None:
    global _VAHTI
    _VAHTI = vahti


def hae_lammitysvahti() -> Lammitysvahti | None:
    """FastAPI-dependency. None = vahti ei käytössä (testit, ei LLM_MALLIA)."""
    return _VAHTI
