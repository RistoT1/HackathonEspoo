"""Soittojarjestyksen pisteytys. Puhdas funktio: ei IO:ta, ei LLM:aa.

Tama on SOITTOJARJESTYS (kenelle soitetaan ensin), EI triage: kiireellisyys arvioidaan vasta
puhelun jalkeen (ei viela backendissa). Perusteet ovat lapinakyvia: miksi tama soitetaan ensin.
"""

from dataclasses import dataclass, field

from app.kysymykset.sisalto import Kysymyssisalto
from app.mallit import Laakemuutos, MuutosTyyppi

MUUTOKSEN_PISTEET: dict[MuutosTyyppi, int] = {
    MuutosTyyppi.UUSI_LAAKE: 2,
    MuutosTyyppi.ANNOSMUUTOS: 2,
    MuutosTyyppi.LOPETETTU_LAAKE: 1,
    MuutosTyyppi.LYHYT_KUURI: 0,
}
KORKEA_RISKI_PISTEET = 3
IAKAS_RAJA = 80
MONILAAKITYS_RAJA = 5

# Riski koskee laakkeen kayttoa: aloitus tai annoksen muutos, ei lopetus.
_RISKITYYPIT = (MuutosTyyppi.UUSI_LAAKE, MuutosTyyppi.ANNOSMUUTOS)
_TYYPPITEKSTI = {
    MuutosTyyppi.UUSI_LAAKE: "Uusi lääke",
    MuutosTyyppi.ANNOSMUUTOS: "Annosmuutos",
}


@dataclass(frozen=True)
class Prioriteetti:
    pisteet: int
    perusteet: list[str] = field(default_factory=list)


def laske_prioriteetti(
    muutokset: list[Laakemuutos], ika: int | None, aktiivisia: int, sisalto: Kysymyssisalto
) -> Prioriteetti:
    """Pisteet ovat saantoja (koodissa); korkean riskin laakeryhmat ovat sisaltoa (kannassa)."""
    pisteet = 0
    perusteet: list[str] = []

    for muutos in muutokset:
        pisteet += MUUTOKSEN_PISTEET[muutos.tyyppi]
        if muutos.tyyppi not in _RISKITYYPIT:
            continue
        ryhma = sisalto.tunnista_ryhma(muutos.laake.nimi, muutos.laake.atc)
        if ryhma is None or not ryhma.korkea_riski:
            continue
        pisteet += KORKEA_RISKI_PISTEET
        perusteet.append(
            f"{_TYYPPITEKSTI[muutos.tyyppi]} korkean riskin lääkeryhmässä: {ryhma.nimi}"
        )

    iakas = ika is not None and ika >= IAKAS_RAJA
    if iakas:
        pisteet += 1
        perusteet.append(f"Ikä {ika} vuotta")

    monilaakitys = aktiivisia >= MONILAAKITYS_RAJA
    if monilaakitys:
        pisteet += 1
        perusteet.append(f"Monilääkitys: {aktiivisia} lääkettä käytössä")

    return Prioriteetti(pisteet=pisteet, perusteet=perusteet)
