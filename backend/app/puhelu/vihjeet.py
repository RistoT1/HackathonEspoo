"""Puheentunnistuksen vihjesanat: perussanasto tiedostosta + puhelun omat laakeryhmat."""

import json
from functools import lru_cache
from pathlib import Path

from app.mallit import Puhelukonteksti

_TIEDOSTO = Path(__file__).with_name("vihjesanat.json")


@lru_cache
def perusvihjeet() -> tuple[str, ...]:
    sisalto = json.loads(_TIEDOSTO.read_text(encoding="utf-8"))
    return tuple(
        sana for ryhma, sanat in sisalto.items() if not ryhma.startswith("_") for sana in sanat
    )


@lru_cache
def oiresanat() -> tuple[str, ...]:
    """vihjesanat.json:n oireet pienellä, esim. 'huimaa', 'ummetus'."""
    sisalto = json.loads(_TIEDOSTO.read_text(encoding="utf-8"))
    return tuple(sana.lower() for sana in sisalto["oireet"])


def puhelun_vihjeet(
    konteksti: Puhelukonteksti | None, lisavihjeet: tuple[str, ...]
) -> tuple[str, ...]:
    """Perussanasto, .env:n lisavihjeet ja muutosten laakeryhmat (esim. 'verenohennus')."""
    laakeryhmat = tuple(
        muutos.laakeryhma
        for muutos in (konteksti.muutokset if konteksti else [])
        if muutos.laakeryhma
    )
    return (*perusvihjeet(), *lisavihjeet, *laakeryhmat)
