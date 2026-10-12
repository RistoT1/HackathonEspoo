"""Mallin puhuttavan suomen tarkistus ennen kuin se sanotaan ääneen.

Pieni malli kirjoittaa välillä sanoja, joita suomessa ei ole ("toimintaenneenne",
"raskalta", "tytönne"; mitattu 11.10.) ja lipsahtaa puhuttelusta toiseen. Voikko (avoin suomen
oikolukija, deterministinen, millisekunteja) löytää tuntemattomat sanat; tulos kirjataan lokiin
(kieli:epavarma) eikä estä puhetta. Toistuvat virheet korjataan ennen puhetta taulukolla
(korjaa_kieli, kielikorjaukset.json).

Voikko ei huomaa oikeista sanoista tehtyä väärää rakennetta ("tuloksellista" väärässä
paikassa). Jos Voikkoa ei ole asennettu, tarkistetaan vain tyyli (ei teitittelyä).
"""

import json
import logging
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from app.config import Settings, hae_asetukset

logger = logging.getLogger(__name__)

Tarkistin = Callable[[str], bool]

# Sana; perään jäävä yhdysmerkki ("ylä- vai alavatsa") kuuluu sanaan, jolloin se ohitetaan:
# katkoviivainen alkuosa ei ole itsenäinen sana (väärä hylkäys mitattu 11.10.).
_SANA = re.compile(r"[A-Za-zÅÄÖåäöÉé]+(?:-[A-Za-zÅÄÖåäöÉé]+)*-?")
# Puhelu sinuttelee (Jeren tietopohja, kannan kysymykset ja kiinteät repliikit, päätetty
# 11.10.); mallin teitittely kuulostaisi epäjohdonmukaiselta.
_TEITITTELY = re.compile(
    r"\b(te|teillä|teitä|teidän|teille|teistä|teihin|teissä|olette|oletteko|ette|voitte|"
    r"voitteko|voisitte|voisitteko|kerroitte|kertoisitte|kertoisitteko|haluaisitte|"
    r"haluaisitteko|tunnette|tunnetteko|jaksatte|jaksatteko|huomasitte|huomasitteko|"
    r"otitte|sanoitte|nukuitte|aloititte|soittakaa|älkää|kertokaa)\b",
    re.IGNORECASE,
)
# Omistusliite -nne ("vointinne", "olonne"); paikan adverbit eivät ole teitittelyä.
_NNE = re.compile(r"\b\w{3,}nne\b", re.IGNORECASE)
_EI_OMISTUSLIITE = frozenset({"sinne", "tänne", "minne", "jonne", "tuonne", "kanne"})


def sinuttelee(teksti: str) -> bool:
    if _TEITITTELY.search(teksti):
        return False
    return all(sana.lower() in _EI_OMISTUSLIITE for sana in _NNE.findall(teksti))


class Voikkotarkistin:
    def __init__(self, kirjastopolku: str | None) -> None:
        import libvoikko

        if kirjastopolku:
            libvoikko.Voikko.setLibrarySearchPath(kirjastopolku)
        self._voikko = libvoikko.Voikko("fi")

    def on_nimisana(self, sana: str) -> bool:
        luokat = {analyysi.get("CLASS", "") for analyysi in self._voikko.analyze(sana)}
        return any("nimisana" in luokka for luokka in luokat)

    def tuntemattomat(self, teksti: str) -> list[str]:
        sanat = [sana for sana in _SANA.findall(teksti) if not sana.endswith("-")]
        return [sana for sana in sanat if len(sana) > 2 and not self._voikko.spell(sana)]


@lru_cache
def _voikko(kirjastopolku: str | None) -> Voikkotarkistin | None:
    try:
        return Voikkotarkistin(kirjastopolku)
    except Exception as virhe:  # kirjasto tai sanakirja puuttuu
        logger.warning("Voikko ei käytettävissä, tarkistetaan vain tyyli: %s", virhe)
        return None


def luo_tarkistin(kirjastopolku: str | None) -> Tarkistin:
    voikko = _voikko(kirjastopolku)

    def kelpaa(teksti: str) -> bool:
        if not sinuttelee(teksti):
            return False
        if voikko is None:
            return True
        tuntemattomat = voikko.tuntemattomat(teksti)
        if tuntemattomat:
            logger.info("Kielentarkistus hylkäsi: %s (%s)", teksti, ", ".join(tuntemattomat))
        return not tuntemattomat

    return kelpaa


_KORJAUKSET = Path(__file__).with_name("kielikorjaukset.json")


@lru_cache
def _korjauskuviot() -> tuple[tuple[re.Pattern, str], ...]:
    """Fraasit ensin (pidempi osuma), sitten kokonaiset sanat (kielikorjaukset.json)."""
    sisalto = json.loads(_KORJAUKSET.read_text(encoding="utf-8"))
    fraasit = [
        (re.compile(rf"\b{re.escape(vaara)}", re.IGNORECASE), oikea)
        for vaara, oikea in sisalto["fraasit"]
    ]
    sanat = [
        (re.compile(rf"\b{re.escape(vaara)}\b", re.IGNORECASE), oikea)
        for vaara, oikea in sisalto["sanat"].items()
    ]
    return (*fraasit, *sanat)


def korjaa_kieli(teksti: str) -> tuple[str, list[str]]:
    """Mallin toistuvat suomen virheet korjattuina ja korjatut kohdat lokia varten.
    Deterministinen taulukko, ei mallia: Voikko vain löysi virheet (12.10.)."""
    korjatut: list[str] = []
    for kuvio, oikea in _korjauskuviot():

        def korvaa(osuma: re.Match, oikea: str = oikea) -> str:
            isolla = osuma.group(0)[:1].isupper()
            return oikea[:1].upper() + oikea[1:] if isolla else oikea

        teksti, maara = kuvio.subn(korvaa, teksti)
        if maara:
            korjatut.append(kuvio.pattern.removeprefix(r"\b").removesuffix(r"\b"))
    if not korjatut:
        return teksti, korjatut
    # Poistettu sana jättää ylimääräisen välin.
    siistitty = re.sub(r"\s+([,.!?])", r"\1", " ".join(teksti.split()))
    return siistitty, korjatut


def hae_tarkistin(asetukset: Annotated[Settings, Depends(hae_asetukset)]) -> Tarkistin:
    """FastAPI-dependency. Testit antavat oman (esim. lambda teksti: True)."""
    return luo_tarkistin(asetukset.voikko_kirjasto)
