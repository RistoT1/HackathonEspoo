"""Improvisoi synteettisen vanhuksen vastauksen botin puheeseen persoonan faktoista.

Ei LLM:ää: paikallinen malli vanhuksena tyhjentäisi botin välimuistin joka vuorolla
(sama Ollama, yksi malli muistissa) ja moninkertaistaisi ajoajan. Improvisaatio on
sääntöjä ja vastausvariaatioita: mitä botti kysyi (kuulumiset, lääkemuutos, oire,
tarkennus, turvallisuus, varmistus) -> persoonan fakta -> puhetyyli.
"""

import random
import re
from dataclasses import dataclass, field

from arviointi.oireet import OIREET, OIREET_AIHEITTAIN, TARKENNUSTYYPIT, osuu
from arviointi.persoonat import muunna

_FAKTAJARJESTYS = ("alkoi", "tilanne", "voimakkuus", "vaikutus", "sijainti", "kuvaus")


@dataclass
class Simulaatiotila:
    rng: random.Random
    vuoro: int = 0
    mainitut: list[str] = field(default_factory=list)  # oireaiheet, jotka henkilö on kertonut
    aktiivinen: str | None = None  # oire, josta puhutaan nyt
    kerrotut: dict[str, set[str]] = field(default_factory=dict)  # aihe -> kerrotut faktat
    tapahtuma_tehty: bool = False
    lopetusvaihe: bool = False
    tukivuoroja: int = 0


def _kysymys(botti: str) -> str:
    """Botin viimeinen kysymyslause pienillä (koko teksti, jos kysymystä ei ole)."""
    lauseet = re.findall(r"[^.!?]*\?", botti)
    return (lauseet[-1] if lauseet else botti).lower()


def _persoonan_oire(persoona: dict, aihe: str) -> dict | None:
    return next((oire for oire in persoona["oireet"] if oire["aihe"] == aihe), None)


def _kerro_heti(persoona: dict, tila: Simulaatiotila) -> str:
    """Oma-aloitteisesti kerrottavat oireet ("heti"), joita ei ole vielä mainittu."""
    osat = []
    for oire in persoona["oireet"]:
        if oire["kerrotaan"] == "heti" and oire["aihe"] not in tila.mainitut:
            osat.append(f"{oire['kerronta']} {oire['alkoi']}".strip())
            _mainitse(tila, oire["aihe"], "alkoi")
    return ", mutta " + " ja ".join(osat) if osat else ""


def _mainitse(tila: Simulaatiotila, aihe: str, fakta: str | None = None) -> None:
    if aihe not in tila.mainitut:
        tila.mainitut.append(aihe)
    tila.aktiivinen = aihe
    if fakta:
        tila.kerrotut.setdefault(aihe, set()).add(fakta)


def _fakta(persoona: dict, tila: Simulaatiotila, aihe: str, toivottu: str | None) -> str:
    """Toivottu fakta, tai seuraava vielä kertomaton (ihminen ei toista samaa)."""
    oire = _persoonan_oire(persoona, aihe)
    if oire is None:
        return "en osaa sanoa"
    kerrotut = tila.kerrotut.setdefault(aihe, set())
    jarjestys = ([toivottu] if toivottu else []) + [f for f in _FAKTAJARJESTYS if f != toivottu]
    for fakta in jarjestys:
        if fakta not in kerrotut and oire.get(fakta):
            kerrotut.add(fakta)
            return oire[fakta]
    return "en osaa sanoa siitä enempää"


def _oireaihe_kysymyksessa(kysymys: str) -> str | None:
    for oire in OIREET:
        if any(osuu(sana, kysymys) for sana in oire.avainsanat):
            return oire.aihe
    return None


def _tarkennustyyppi(kysymys: str) -> str | None:
    for tyyppi, sanat in TARKENNUSTYYPIT:
        if any(osuu(sana, kysymys) for sana in sanat):
            return tyyppi
    return None


def _improvisoi(persoona: dict, tila: Simulaatiotila, botti: str) -> str:
    kysymys = _kysymys(botti)
    if "?" not in botti:
        return tila.rng.choice(("joo, selvä", "kiitos", "joo"))

    # Turvallisuus (tukitila tai lopetuspyynnön jälkeen).
    if "turvassa" in kysymys or "turvallinen" in kysymys:
        tila.tukivuoroja += 1
        if (
            tila.tukivuoroja == 1
            and persoona["tapahtuma"]
            and persoona["tapahtuma"]["tyyppi"]
            in (
                "itsetuho",
                "vakava",
            )
        ):
            return "en tiedä oikein"
        return "joo, olen turvassa nyt, tytär tulee illalla käymään"
    if "kaikki muuten" in kysymys or "onko kaikki" in kysymys:
        return "joo, kaikki on ihan hyvin"
    if any(s in kysymys for s in ("onko vielä jotain", "haluatko kertoa", "jotain muuta")):
        return "ei mitään muuta"
    if kysymys.strip().startswith("eli "):
        return "joo, niin on"

    # Kuulumiset ja yleinen vointi.
    if (
        any(
            s in kysymys
            for s in ("miten sinulla menee", "mitä kuuluu", "miten olet voinut", "miten menee")
        )
        and tila.vuoro == 1
    ):
        return f"{persoona['kuulumiset']}{_kerro_heti(persoona, tila)}"
    if "voit tänään" in kysymys or "vointisi" in kysymys:
        kertomattomat = [o for o in persoona["oireet"] if o["aihe"] not in tila.mainitut]
        if kertomattomat:
            oire = kertomattomat[0]
            _mainitse(tila, oire["aihe"])
            return f"ihan ok muuten, mutta {oire['kerronta']}"
        return tila.rng.choice(("ihan hyvin", "ihan ok tänään", "hyvin kiitos"))

    # Lääkemuutos (kannan muutoskysymys).
    laakesanat = (
        "aloittanut",
        "annoksen",
        "lopettanut",
        "kuuri",
        "sopinut",
        "sujunut",
        "lääkkeen",
        "lääke",
    )
    aihe = _oireaihe_kysymyksessa(kysymys)
    if aihe is None and any(s in kysymys for s in laakesanat):
        return f"{persoona['muutosvastaus']}{_kerro_heti(persoona, tila)}"

    # Oire: kysytty suoraan tai tarkennus.
    tyyppi = _tarkennustyyppi(kysymys)
    if aihe is not None:
        oire = _persoonan_oire(persoona, aihe)
        if oire is None:
            return tila.rng.choice(OIREET_AIHEITTAIN[aihe].kielto)
        if aihe not in tila.mainitut:
            _mainitse(tila, aihe)
            return oire["myonto"]
        tila.aktiivinen = aihe
        return _fakta(persoona, tila, aihe, tyyppi)
    if tila.aktiivinen is not None:
        return _fakta(persoona, tila, tila.aktiivinen, tyyppi)
    return tila.rng.choice(("en oikein tiedä", "ei siinä mitään erikoista", "no ihan tavallisesti"))


def vastaa(persoona: dict, tila: Simulaatiotila, botti: str) -> str:
    """Henkilön seuraava repliikki. Tapahtuma (hätä, lopetus, kysymys ...) omalla vuorollaan."""
    tila.vuoro += 1
    tapahtuma = persoona.get("tapahtuma")
    if tapahtuma and not tila.tapahtuma_tehty and tila.vuoro >= tapahtuma["vuoro"]:
        tila.tapahtuma_tehty = True
        return tapahtuma["teksti"]
    vastaus = _improvisoi(persoona, tila, botti)
    return muunna(vastaus, persoona["tyyli"], tila.rng)
