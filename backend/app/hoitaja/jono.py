"""Hoitajan jono: puhelun kiireellisyys tehtävittäin ja jonon järjestys.

Taso luetaan tehtävän uusimman puhelun yhteenvedosta (sääntötaso heti puhelun jälkeen,
mallin analyysi voi vain nostaa sitä). Järjestys on hoitajan näkymää varten: soittojonon API
(GET /puhelutehtavat) pysyy soittoprioriteetin järjestyksessä.
"""

from sqlalchemy.orm import Session

from app.hoitaja.palvelu import uusimmat_tulokset
from app.mallit import Kiireellisyys, Puhelutehtava, Yhteenveto

TASOJEN_JARJESTYS: dict[Kiireellisyys, int] = {
    Kiireellisyys.PUNAINEN: 0,
    Kiireellisyys.KELTAINEN: 1,
    Kiireellisyys.VIHREA: 2,
}
EI_ARVIOTA = len(TASOJEN_JARJESTYS)
HOITAJA_SOITTAA = (Kiireellisyys.PUNAINEN, Kiireellisyys.KELTAINEN)


def hae_arviot(sessio: Session, tehtava_idt: list[str]) -> dict[str, Yhteenveto]:
    """Tehtävän uusimman puhelun yhteenveto. Tehtävä ilman yhteenvetoa puuttuu tuloksesta."""
    tulokset = uusimmat_tulokset(sessio, tehtava_idt)
    return {
        tehtava_id: tulos.yhteenveto
        for tehtava_id, tulos in tulokset.items()
        if tulos.yhteenveto is not None
    }


def jarjesta(tapaukset: list[Puhelutehtava], arviot: dict[str, Yhteenveto]) -> list[Puhelutehtava]:
    """Punainen, keltainen, vihreä, ei arviota. Saman tason sisällä tulojärjestys säilyy
    (varasto.listaa järjestää soittoprioriteetin mukaan)."""

    def tason_jarjestys(tapaus: Puhelutehtava) -> int:
        arvio = arviot.get(tapaus.id)
        if arvio is None:
            return EI_ARVIOTA
        return TASOJEN_JARJESTYS[arvio.taso]

    return sorted(tapaukset, key=tason_jarjestys)


def hoitaja_soittaa(arviot: dict[str, Yhteenveto]) -> int:
    return sum(arvio.taso in HOITAJA_SOITTAA for arvio in arviot.values())
