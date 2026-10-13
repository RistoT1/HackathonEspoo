"""Triage taustalla: puhelun päätyttyä LLM-analyysi päivittää tuloksen yhteenvedon.

Sääntötaso on tuloksessa heti (keskustelu.Puheluistunto.tulos), joten hoitaja näkee
hälytyksen ilman odotusta; mallin analyysi tarkentaa sitä kymmenien sekuntien päästä.
Oma säie, koska analyysi on hidas eikä se saa estää puheluiden käsittelyä. Litterointi
on vain jonossa muistissa ja häviää analyysin jälkeen (datan minimointi).
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Protocol

from app.mallit import PuhelunTulos, Saantoloydokset, Yhteenveto

logger = logging.getLogger(__name__)


class Analysoija(Protocol):
    def analysoi(self, litterointi: str, saanto: Saantoloydokset) -> Yhteenveto: ...


class Triagetyontekija:
    def __init__(
        self,
        analysoija: Analysoija,
        puheluita_kaynnissa: Callable[[], bool] = lambda: False,
        tallenna: Callable[[PuhelunTulos], None] | None = None,
    ) -> None:
        self._analysoija = analysoija
        # Analyysi vasta kun yhtään puhelua ei ole käynnissä: puhelu menee aina edelle.
        self._puheluita_kaynnissa = puheluita_kaynnissa
        self._tallenna = tallenna
        self._jono: queue.Queue[tuple[PuhelunTulos, str]] = queue.Queue()
        self._saie = threading.Thread(target=self._aja, name="triage", daemon=True)
        self._saie.start()

    def lisaa(self, tulos: PuhelunTulos, litterointi: str) -> None:
        if tulos.yhteenveto is None or not litterointi.strip():
            return
        self._jono.put((tulos, litterointi))

    def odota_tyhjaksi(self) -> None:
        """Testeille ja sammutukseen."""
        self._jono.join()

    def _aja(self) -> None:
        while True:
            tulos, litterointi = self._jono.get()
            while self._puheluita_kaynnissa():
                time.sleep(2)
            try:
                saanto = tulos.yhteenveto.saanto
                tulos.yhteenveto = self._analysoija.analysoi(litterointi, saanto)
                logger.info(
                    "Triage valmis: istunto=%s taso=%s (sääntö %s, malli %s)",
                    tulos.istunto_id,
                    tulos.yhteenveto.taso,
                    saanto.taso,
                    tulos.yhteenveto.mallin_taso,
                )
                if self._tallenna is not None:
                    try:
                        self._tallenna(tulos)
                    except Exception:
                        logger.exception(
                            "Triage-yhteenvedon tallennus epäonnistui: istunto=%s",
                            tulos.istunto_id,
                        )
            except Exception:
                logger.exception("Triage-analyysi kaatui; sääntötaso jää voimaan")
            finally:
                self._jono.task_done()
