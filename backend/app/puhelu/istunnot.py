"""Kesken olevat puhelut ja viimeisimmät tulokset muistissa.

Datan minimointi: raakavastaukset eivät päädy kantaan. Puhelun päättyessä tietokantaan
tallennetaan triage-yhteenveto; muistin tuloslista palvelee kehityksen tarkastelua.
Hinta: backendin uudelleenkäynnistys kesken puhelun katkaisee puhelun (Twilio saa
virhe-TwiML:n), mikä on demossa hyväksyttävä.
Yksi prosessi (uvicorn ilman workereita); useampi prosessi vaatisi jaetun varaston.
"""

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from app.mallit import KaikuTulos, PuhelunTulos
from app.puhelu.keskustelu import Puheluistunto, Vuoro
from app.triage.tyontekija import Triagetyontekija

# Paattyneita tuloksia pidetaan vain rajallinen maara (dev-nakyma, triagen syote).
MAX_TULOKSIA = 100

Tallennus = Callable[[PuhelunTulos], None]


def _ei_tallennusta(tulos: PuhelunTulos) -> None:
    """Ilman kantaa (testit): tulos jää vain muistiin. main.py asettaa kantaan tallennuksen."""


@dataclass
class Keskenvuoro:
    """Vuoro, jonka käsittely jatkuu välivastauksen jälkeen (Twilio hakee sen uudelleen)."""

    tehtava: asyncio.Task[Vuoro]
    alku: float  # time.perf_counter() vuoron alussa
    kentat: dict[str, str]
    vaihe_ennen: str | None
    odotuksia: int = 0
    valivastattu: bool = False  # välitäyte sanottu: lyhyt kuittaus jätetään pois
    ajoitus: dict | None = None  # lokia varten: aika edellisestä vastauksesta ym.


class Istuntovarasto:
    def __init__(self) -> None:
        self._lukko = Lock()
        self._istunnot: dict[str, Puheluistunto] = {}
        self._tulokset: deque[PuhelunTulos] = deque(maxlen=MAX_TULOKSIA)
        self._kaiut: deque[KaikuTulos] = deque(maxlen=MAX_TULOKSIA)
        self._kesken: dict[str, Keskenvuoro] = {}
        self._tallenna: Tallennus = _ei_tallennusta
        self._triage: Triagetyontekija | None = None

    def aseta_tallennus(self, tallenna: Tallennus) -> None:
        """Yhteenvedon tallennus kantaan (main.py käynnistyksessä). Samaa käytetään puhelun
        päättyessä ja kun triage-analyysi päivittää yhteenvedon. paata kutsuu sitä lukon
        sisällä, joten tallennus ei saa kutsua varastoa."""
        self._tallenna = tallenna

    def tallenna(self, tulos: PuhelunTulos) -> None:
        """Triage-analyysin päivitys. Ei lukkoa: tallennus käyttää omaa kantasessiotaan."""
        self._tallenna(tulos)

    def aseta_triage(self, triage: Triagetyontekija | None) -> None:
        """LLM-analyysi päättyneille puheluille (main.py käynnistää, kun LLM_MALLI on)."""
        self._triage = triage

    def aseta_kesken(self, istunto_id: str, kesken: Keskenvuoro) -> None:
        with self._lukko:
            self._kesken[istunto_id] = kesken

    def ota_kesken(self, istunto_id: str) -> Keskenvuoro | None:
        with self._lukko:
            return self._kesken.pop(istunto_id, None)

    def lisaa(self, istunto: Puheluistunto) -> None:
        with self._lukko:
            self._istunnot[istunto.istunto_id] = istunto

    def hae(self, istunto_id: str) -> Puheluistunto | None:
        with self._lukko:
            return self._istunnot.get(istunto_id)

    def puheluita_kaynnissa(self) -> bool:
        """Triage odottaa tätä: Ollama palvelee yhtä pyyntöä kerrallaan, ja analyysi
        hidastaisi käynnissä olevan puhelun vuoroja (11.10.: puhelu odotti 70 s)."""
        with self._lukko:
            return bool(self._istunnot)

    def poista(self, istunto_id: str) -> None:
        """Kun soitto ei edes lahtenyt (Twilio hylkasi)."""
        with self._lukko:
            self._istunnot.pop(istunto_id, None)

    def paata(self, istunto: Puheluistunto) -> bool:
        """Tallentaa yhteenvedon ennen kuin istunto poistetaan ja tulos julkaistaan.
        False = puhelu oli jo päätetty (ei toista tallennusta)."""
        with self._lukko:
            if istunto.istunto_id not in self._istunnot:
                return False
            tulos = istunto.tulos()
            # Tallennus lukon sisällä: Twilion paluukutsut (/tila, /relay-loppu) ajetaan
            # säikeissä yhtä aikaa relayn lopetuksen kanssa, ja ne näkevät istunnon joko
            # käynnissä tai päättyneenä, eivät tallennuksen kesken. Jos kanta ei ole
            # käytettävissä, istunto jää aktiiviseksi uudelleenyritystä varten.
            self._tallenna(tulos)
            self._istunnot.pop(istunto.istunto_id)
            self._tulokset.append(tulos)
        if self._triage is not None:
            self._triage.lisaa(tulos, istunto.litterointi())
        return True

    def tulokset(self) -> list[PuhelunTulos]:
        with self._lukko:
            return list(reversed(self._tulokset))  # uusin ensin

    def lisaa_kaiku(self, tulos: KaikuTulos) -> None:
        with self._lukko:
            self._kaiut.append(tulos)

    def kaiut(self) -> list[KaikuTulos]:
        with self._lukko:
            return list(reversed(self._kaiut))


_VARASTO = Istuntovarasto()


def hae_istuntovarasto() -> Istuntovarasto:
    """FastAPI-dependency. Testit antavat oman tyhjan varaston."""
    return _VARASTO
