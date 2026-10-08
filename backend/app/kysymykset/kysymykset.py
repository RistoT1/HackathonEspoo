"""Puhelun kysymykset ja konteksti muutoksista. Puhtaat funktiot: sisalto annetaan parametrina.

Backend kertoo MITA kysytaan. Puhelun LLM muotoilee lauseet, reagoi vastauksiin ja hoitaa
avauksen, jatkokysymykset, lopetuksen ja hataohjauksen omien ohjeidensa mukaan.
Jarjestys: muutosten kysymykset -> tarkennukset -> yleiskysymykset.
"""

from app.kysymykset.sisalto import Kysymyssisalto
from app.mallit import Kysymys, Laakemuutos, Muutoskonteksti, MuutosTyyppi, Puhelukonteksti

# Saannot (ei sisaltoa): enintaan kaksi muutosta ja kaksi tarkennusta, jotta puhelu pysyy lyhyena.
MAX_MUUTOKSIA_PUHELUSSA = 2
MAX_TARKENNUKSIA = 2

# Haittavaikutusten tarkennus vain kun laaketta kaytetaan tai juuri kaytettiin.
_TARKENNETTAVAT_TYYPIT = (
    MuutosTyyppi.UUSI_LAAKE,
    MuutosTyyppi.ANNOSMUUTOS,
    MuutosTyyppi.LYHYT_KUURI,
)


def _uusin_ensin(muutokset: list[Laakemuutos]) -> list[Laakemuutos]:
    return sorted(muutokset, key=lambda muutos: muutos.tapahtui, reverse=True)


def valitse_muutokset(muutokset: list[Laakemuutos], sisalto: Kysymyssisalto) -> list[Laakemuutos]:
    """Uusin ensin, kukin muutostyyppi kerran, vain tyypit joille on kysymys kannassa."""
    valitut: list[Laakemuutos] = []
    for muutos in _uusin_ensin(muutokset):
        if len(valitut) >= MAX_MUUTOKSIA_PUHELUSSA:
            break
        tyyppi_jo_valittu = any(valittu.tyyppi == muutos.tyyppi for valittu in valitut)
        ei_kysymysta = muutos.tyyppi not in sisalto.muutoskysymykset
        if tyyppi_jo_valittu or ei_kysymysta:
            continue
        valitut.append(muutos)
    return valitut


def _tarkennusaiheet(muutokset: list[Laakemuutos], sisalto: Kysymyssisalto) -> list[str]:
    aiheet: list[str] = []
    for muutos in muutokset:
        if muutos.tyyppi not in _TARKENNETTAVAT_TYYPIT:
            continue
        ryhma = sisalto.tunnista_ryhma(muutos.laake.nimi, muutos.laake.atc)
        if ryhma is None:
            continue
        for aihe in ryhma.tarkennusaiheet:
            if aihe not in aiheet:
                aiheet.append(aihe)
    return aiheet[:MAX_TARKENNUKSIA]


def luo_kysymykset(muutokset: list[Laakemuutos], sisalto: Kysymyssisalto) -> list[Kysymys]:
    valitut = valitse_muutokset(muutokset, sisalto)

    kysymykset = [
        Kysymys(
            tunniste=muutos.tyyppi.value,
            teksti=sisalto.muutoskysymykset[muutos.tyyppi],
            muutos_tyyppi=muutos.tyyppi,
        )
        for muutos in valitut
    ]
    kysymykset.extend(
        Kysymys(tunniste=f"tarkennus.{aihe}", teksti=sisalto.tarkennuskysymykset[aihe])
        for aihe in _tarkennusaiheet(valitut, sisalto)
    )
    kysymykset.extend(
        Kysymys(tunniste=tunniste, teksti=teksti) for tunniste, teksti in sisalto.yleiskysymykset
    )
    return kysymykset


def luo_konteksti(
    muutokset: list[Laakemuutos], ika: int | None, aktiivisia: int, sisalto: Kysymyssisalto
) -> Puhelukonteksti:
    """Tausta LLM:lle. Laakkeen nimen tilalla ryhma, ei diagnooseja (datan minimointi)."""
    muutoskontekstit = []
    for muutos in _uusin_ensin(muutokset):
        ryhma = sisalto.tunnista_ryhma(muutos.laake.nimi, muutos.laake.atc)
        muutoskontekstit.append(
            Muutoskonteksti(
                tyyppi=muutos.tyyppi,
                laakeryhma=ryhma.nimi if ryhma else None,
                paivia_sitten=muutos.paivia_sitten,
            )
        )
    return Puhelukonteksti(ika=ika, aktiivisia_laakkeita=aktiivisia, muutokset=muutoskontekstit)
