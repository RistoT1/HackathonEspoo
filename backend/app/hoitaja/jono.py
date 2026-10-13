"""Hoitajan jono: puhelun kiireellisyys tehtävittäin ja jonon järjestys.

Taso luetaan tallennetusta yhteenvedosta (sääntötaso heti puhelun jälkeen, mallin analyysi
voi vain nostaa sitä). Järjestys on hoitajan näkymää varten: soittojonon API
(GET /puhelutehtavat) pysyy soittoprioriteetin järjestyksessä.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mallit import Kiireellisyys, Puhelutehtava
from app.tietokanta import PuhelunTulosRivi

TASOJEN_JARJESTYS: dict[Kiireellisyys, int] = {
    Kiireellisyys.PUNAINEN: 0,
    Kiireellisyys.KELTAINEN: 1,
    Kiireellisyys.VIHREA: 2,
}
EI_ARVIOTA = len(TASOJEN_JARJESTYS)
HOITAJA_SOITTAA = (Kiireellisyys.PUNAINEN, Kiireellisyys.KELTAINEN)
_AIKOJEN_ALKU = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class JononTaso:
    taso: Kiireellisyys
    analyysi_valmis: bool


def _paattyi(rivi: PuhelunTulosRivi) -> datetime:
    if rivi.paattyi is None:
        return _AIKOJEN_ALKU
    if rivi.paattyi.tzinfo is None:
        return rivi.paattyi.replace(tzinfo=UTC)
    return rivi.paattyi


def hae_tasot(sessio: Session, tehtava_idt: list[str]) -> dict[str, JononTaso]:
    """Tehtävän uusimman yhteenvedon taso. Tehtävä ilman yhteenvetoa puuttuu tuloksesta."""
    if not tehtava_idt:
        return {}
    rivit = sessio.scalars(
        select(PuhelunTulosRivi).where(PuhelunTulosRivi.tehtava_id.in_(tehtava_idt))
    ).all()

    tasot: dict[str, JononTaso] = {}
    for rivi in sorted(rivit, key=_paattyi):
        # JSON-sarakkeen None tallentuu JSON-nulliksi, ei SQL NULLiksi: tarkistus tässä.
        yhteenveto = rivi.yhteenveto
        if not yhteenveto:
            continue
        tasot[rivi.tehtava_id] = JononTaso(
            taso=Kiireellisyys(yhteenveto["taso"]),
            analyysi_valmis=bool(yhteenveto.get("analyysi_valmis", False)),
        )
    return tasot


def jarjesta(tapaukset: list[Puhelutehtava], tasot: dict[str, JononTaso]) -> list[Puhelutehtava]:
    """Punainen, keltainen, vihreä, ei arviota. Saman tason sisällä tulojärjestys säilyy
    (varasto.listaa järjestää soittoprioriteetin mukaan)."""

    def tason_jarjestys(tapaus: Puhelutehtava) -> int:
        jonon_taso = tasot.get(tapaus.id)
        if jonon_taso is None:
            return EI_ARVIOTA
        return TASOJEN_JARJESTYS[jonon_taso.taso]

    return sorted(tapaukset, key=tason_jarjestys)


def hoitaja_soittaa(tasot: dict[str, JononTaso]) -> int:
    return sum(jonon_taso.taso in HOITAJA_SOITTAA for jonon_taso in tasot.values())
