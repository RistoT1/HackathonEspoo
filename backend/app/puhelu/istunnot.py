"""Kesken olevat puhelut ja paattyneiden tulokset MUISTISSA (ei kantaa).

Datan minimointi: vanhuksen raakavastaukset eivat paady kantaan. Triage lukee tuloksen
taalta ja tallentaa vain yhteenvedon. Hinta: backendin uudelleenkaynnistys kesken puhelun
katkaisee puhelun (Twilio saa virhe-TwiML:n), mika on demossa hyvaksyttava.
Yksi prosessi (uvicorn ilman workereita); useampi prosessi vaatisi jaetun varaston.
"""

from collections import deque
from threading import Lock

from app.mallit import KaikuTulos, PuhelunTulos
from app.puhelu.keskustelu import Puheluistunto

# Paattyneita tuloksia pidetaan vain rajallinen maara (dev-nakyma, triagen syote).
MAX_TULOKSIA = 100


class Istuntovarasto:
    def __init__(self) -> None:
        self._lukko = Lock()
        self._istunnot: dict[str, Puheluistunto] = {}
        self._tulokset: deque[PuhelunTulos] = deque(maxlen=MAX_TULOKSIA)
        self._kaiut: deque[KaikuTulos] = deque(maxlen=MAX_TULOKSIA)

    def lisaa(self, istunto: Puheluistunto) -> None:
        with self._lukko:
            self._istunnot[istunto.istunto_id] = istunto

    def hae(self, istunto_id: str) -> Puheluistunto | None:
        with self._lukko:
            return self._istunnot.get(istunto_id)

    def poista(self, istunto_id: str) -> None:
        """Kun soitto ei edes lahtenyt (Twilio hylkasi)."""
        with self._lukko:
            self._istunnot.pop(istunto_id, None)

    def paata(self, istunto: Puheluistunto) -> bool:
        """Siirtaa paattyneen puhelun tuloksiin. False, jos se oli jo siirretty."""
        with self._lukko:
            poistettu = self._istunnot.pop(istunto.istunto_id, None)
            if poistettu is None:
                return False
            self._tulokset.append(istunto.tulos())
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
