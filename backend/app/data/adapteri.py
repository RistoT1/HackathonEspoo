"""Datan adapterikerros: KAIKKI data luetaan taman rajapinnan kautta.

Lahteen vaihto (Synthea -> Veil.AI 13.10.) = uusi Datalahde-toteutus + hae_datalahde() osoittamaan
siihen. Kenttanimet ovat vain toteutuksissa (esim. postgres_demo.py), eivat muualla koodissa.
"""

from datetime import datetime
from typing import Protocol

from app.mallit import Laakejakso, Vanhus


class DataEiSaatavilla(RuntimeError):
    """Datalahde on tyhja tai ei saatavilla. API palauttaa 503 selkealla viestilla."""


class Datalahde(Protocol):
    def uusin_paiva(self) -> datetime:
        """Datan uusin tapahtumapaiva. Kaytetaan oletusviitepaivana, koska data ei paivity."""
        ...

    def hae_vanhukset(self) -> list[Vanhus]: ...

    def hae_vanhus(self, vanhus_id: str) -> Vanhus | None: ...

    def hae_laakejaksot(self, vanhus_id: str) -> list[Laakejakso]: ...

    def hae_laakejaksot_monelle(self, vanhus_idt: list[str]) -> dict[str, list[Laakejakso]]: ...


class Testikirjoittaja(Protocol):
    """VAIN DEV: testimuutokset demodataan. Jokainen muutos on palautettavissa nollauksella."""

    def lisaa_laake(self, vanhus_id: str, koodi: str, nimi: str, alkoi: datetime) -> None: ...

    def lopeta_laake(
        self, vanhus_id: str, koodi: str, paattyi: datetime, viitepaiva: datetime
    ) -> bool:
        """False, jos koodilla ei ole viitepaivana kaytossa olevaa laaketta."""
        ...

    def poista_testimuutokset(self) -> tuple[int, int]:
        """Palauttaa (palautetut lopetukset, poistetut lisatyt laakkeet)."""
        ...


def hae_datalahde() -> Datalahde:
    """FastAPI-dependency. Testit korvaavat taman feikilla (app.dependency_overrides)."""
    from app.data.postgres_demo import PostgresDemoLahde
    from app.tietokanta import hae_engine

    return PostgresDemoLahde(hae_engine())


def hae_testikirjoittaja() -> Testikirjoittaja:
    from app.data.postgres_demo import PostgresDemoLahde
    from app.tietokanta import hae_engine

    return PostgresDemoLahde(hae_engine())
