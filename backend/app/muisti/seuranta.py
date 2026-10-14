"""Edellisen oireen seurantakysymys ja vastauksen varovainen luokittelu.

Kysymys on backendin kysymyslistassa, joten se kysytään vain kerran. Luokittelu ei
diagnosoi: epäselvä ilmaus jää hoitajan tarkistettavaksi.
"""

import re

from app.mallit import (
    EdellinenPuhelu,
    Kysymys,
    OireenSeurannanTila,
    Oireseuranta,
)

MUISTIKYSYMYS_ETULIITE = "muisti:"

_POISTUNUT = re.compile(
    r"\b(ei (ole )?enää|ei tunnu enää|loppunut|mennyt ohi|poissa|parantunut kokonaan)\b"
)
_PAHENTUNUT = re.compile(r"\b(pahentunut|pahempi|pahempaa|enemän|voimistunut|kovempi)\b")
_LIEVITTYNYT = re.compile(r"\b(lievittynyt|lievempi|helpottanut|parempi|vähentynyt|vähemmän)\b")
_ENNALLAAN = re.compile(
    r"\b(ennallaan|samanlainen|samanlaista|sama|yhtä|ei muutosta|ei ole muuttunut)\b"
)
_KIELLETTY_MUUTOS = re.compile(
    r"\bei\s+(?:se\s+)?(?:(?:ole|oo)\s+)?(?:yhtään\s+)?"
    r"(pahentunut|pahempi|pahempaa|voimistunut|kovempi|lievittynyt|lievempi|"
    r"helpottanut|parempi|vähentynyt)\b"
)

_VOIMAKAS = re.compile(r"\b(voimakas|voimakkaasti|kova|kovaa|hirveä|hirveästi|paha)\b")
_KOHTALAINEN = re.compile(r"\b(kohtalainen|kohtalaisesti|jonkin verran)\b")
_LIEVA = re.compile(r"\b(lievä|lievästi|vähän|pieni|hieman)\b")


def _kohde(edellinen: EdellinenPuhelu) -> tuple[str, str, str] | None:
    """Oireavain, henkilölle luettava kuvaus ja aiempi voimakkuus."""
    if not edellinen.oireet and not edellinen.kuvaukset:
        return None
    oire = edellinen.oireet[0] if edellinen.oireet else edellinen.kuvaukset[0].split(" (")[0]
    kuvaus = edellinen.kuvaukset[0] if edellinen.kuvaukset else oire
    return oire, kuvaus, edellinen.voimakkuudet.get(oire, "ei tiedossa")


def luo_seurantakysymys(edellinen: EdellinenPuhelu | None) -> Kysymys | None:
    if edellinen is None:
        return None
    kohde = _kohde(edellinen)
    if kohde is not None:
        oire, kuvaus, _ = kohde
        return Kysymys(
            tunniste=f"{MUISTIKYSYMYS_ETULIITE}{oire}",
            teksti=(
                f"Viime puhelussa kerroit, että sinulla oli {kuvaus}. "
                "Onko oire helpottanut, pysynyt samana vai pahentunut?"
            ),
        )
    if edellinen.laakkeen_kaytto == "kyllä":
        teksti = (
            "Viime puhelussa kerroit, että lääkkeen käyttö oli sujunut hyvin eikä oireita "
            "ollut. Onko sen jälkeen tullut huimausta, pahoinvointia tai muita oireita?"
        )
    elif edellinen.yleisvointi:
        teksti = (
            f"Viime puhelussa kerroit voinnistasi näin: {edellinen.yleisvointi}. Miten voit nyt?"
        )
    else:
        teksti = (
            "Viime puhelussa kävimme läpi lääkemuutosta. Onko sen jälkeen tullut uusia oireita?"
        )
    return Kysymys(
        tunniste=f"{MUISTIKYSYMYS_ETULIITE}vointi",
        teksti=teksti,
    )


def on_seurantakysymys(kysymys: Kysymys | None) -> bool:
    return kysymys is not None and kysymys.tunniste.startswith(MUISTIKYSYMYS_ETULIITE)


def _voimakkuus(teksti: str) -> str:
    if _VOIMAKAS.search(teksti):
        return "voimakas"
    if _KOHTALAINEN.search(teksti):
        return "kohtalainen"
    if _LIEVA.search(teksti):
        return "lievä"
    return "ei tiedossa"


def luokittele_seuranta(
    kysymys: Kysymys,
    vastaus: str,
    edellinen: EdellinenPuhelu,
) -> Oireseuranta:
    oire = kysymys.tunniste.removeprefix(MUISTIKYSYMYS_ETULIITE)
    teksti = vastaus.lower()
    if _POISTUNUT.search(teksti):
        tila = OireenSeurannanTila.POISTUNUT
    elif _KIELLETTY_MUUTOS.search(teksti):
        # "Ei ole parempi/pahempi" ei todista suuntaa. Turvallinen tulkinta on, että
        # oire ei ole osoitetusti helpottanut; ennallaan siirtää asian hoitajalle.
        tila = OireenSeurannanTila.ENNALLAAN
    elif _PAHENTUNUT.search(teksti):
        tila = OireenSeurannanTila.PAHENTUNUT
    elif _LIEVITTYNYT.search(teksti):
        tila = OireenSeurannanTila.LIEVITTYNYT
    elif _ENNALLAAN.search(teksti):
        tila = OireenSeurannanTila.ENNALLAAN
    else:
        tila = OireenSeurannanTila.EPASELVA
    return Oireseuranta(
        oire=oire,
        aiempi_voimakkuus=edellinen.voimakkuudet.get(oire, "ei tiedossa"),
        nykyinen_voimakkuus=_voimakkuus(teksti),
        tila=tila,
    )
