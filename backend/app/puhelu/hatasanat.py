"""Hatamerkkien tunnistus puheesta. Puhdas funktio, ei LLM:aa: punainen lippu ei saa nojata malliin.

Sanat ovat versionhallitussa tiedostossa hatasanat.json (ei kannassa), jotta jokainen muutos
kulkee katselmoinnin ja testipankin kautta.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

_TIEDOSTO = Path(__file__).with_name("hatasanat.json")


@lru_cache
def lataa_hatasanat() -> dict[str, tuple[str, ...]]:
    """Luokka -> ilmaukset (sanan alkuja, pienilla kirjaimilla)."""
    sisalto = json.loads(_TIEDOSTO.read_text(encoding="utf-8"))
    return {
        luokka: tuple(ilmaukset)
        for luokka, ilmaukset in sisalto.items()
        if not luokka.startswith("_")
    }


def normalisoi(teksti: str) -> str:
    """Pienet kirjaimet, valimerkit pois, valit yhdeksi. Alkuun vali sananrajaa varten."""
    pienet = teksti.lower()
    ilman_valimerkkeja = re.sub(r"[^\w\s]", " ", pienet)
    yksi_vali = " ".join(ilman_valimerkkeja.split())
    return f" {yksi_vali}"


def tunnista_hatamerkki(teksti: str) -> str | None:
    """Palauttaa hatamerkin luokan (esim. 'rintakipu') tai None. Ilmaus osuu sanan alkuun."""
    normalisoitu = normalisoi(teksti)
    for luokka, ilmaukset in lataa_hatasanat().items():
        for ilmaus in ilmaukset:
            if f" {ilmaus}" in normalisoitu:
                return luokka
    return None
