"""Mallin LOPETUS: milloin puhelu ei vielä saa päättyä mallin merkkiin, ja lopetuksen sanat.

Puhtaita funktioita (ei istuntoa, ei mallia). Tilakone (keskustelu._mallin_vuoro) soveltaa
estoa yhdessä paikassa: syy lokiin, merkki pois ja jatko vastauksen perään.
"""

import re
from dataclasses import dataclass

from app.puhelu.tietopohja import LOPETUSMERKKI, jaa_lauseiksi

# Malli kirjoittaa lopetusmerkin joskus pienellä omana lauseenaan ("Lopetus."), ja se
# luettiin ääneen (12.10. läpikäynti). Sana lauseen keskellä ("lääkkeen lopetus") ei ole merkki.
_LOPETUS_LOPUSSA = re.compile(r"(^|[.!?]\s*|\n\s*)lopetus[.!]?\s*$", re.IGNORECASE)
# Lopetuslause kiittää ja lupaa hoitajan soiton: mallin omat kiitokset ennen sitä toistivat
# saman ("Kiitos, että kerroit. ... Kiitos kuulumisista. Välitän ne hoitajalle", 12.10.), ja
# kysymys juuri ennen lopetusta jäi vaille vastausta.
_KIITOSLAUSE = re.compile(r"^(kiitos|välitän|kerron)\b", re.IGNORECASE)

# Huono vointi henkilön viimeisessä vastauksessa: malli ei saa lopettaa siihen (11.10.).
HUONO_VOINTI = re.compile(
    r"\b(huonosti|heikosti|kurjasti|surkeasti|paskasti|huono olo|paha olo"
    r"|ei (kovin |oikein )?hyvin)\b"
)
HUONON_VOINNIN_JATKO = "Kerrotko vähän tarkemmin, mikä oloasi vaivaa?"
OIREEN_TARKENNUS = "Kerrotko siitä vähän tarkemmin?"


def yhtenaista_lopetusmerkki(raaka: str) -> str:
    """Lopussa omana lauseenaan oleva "Lopetus." lopetusmerkiksi."""
    return _LOPETUS_LOPUSSA.sub(lambda osuma: f"{osuma.group(1)}{LOPETUSMERKKI}", raaka)


@dataclass(frozen=True)
class Esto:
    """Miksi mallin LOPETUS ohitettiin ja mitä vastauksen perään lisätään."""

    syy: str  # lokiin (istunto.syyt)
    jatko: str = ""  # lisätään, jos vastauksessa ei ole kysymystä ("" = ei mitään)
    vain_kerran: bool = False  # listan esto: seuraava LOPETUS hyväksytään


def lopetuksen_esto(
    *,
    henkilon_teksti: str,
    henkilo_kysyi: bool,
    oire_kesken: bool,
    listan_kysymys: str,
    lista_kesken: bool,
    saa_lopettaa_kesken: bool,
) -> Esto | None:
    """Ohitetaanko mallin LOPETUS. None = puhelu saa päättyä. Ensimmäinen osuva syy ratkaisee.
    listan_kysymys: viimeksi kysytty listan kysymys (tai ensimmäinen; "" = lista tyhjä).
    saa_lopettaa_kesken: lopuillaan, lääkemuutoksen poikkeama tai listan esto jo kerran."""
    if henkilo_kysyi:
        # Henkilö kysyi juuri jotain: ei lopeteta hänen alta (11.10. arviointi: "Kuka sinä
        # olet?" -> vastaus ja lopetus, vaikka lääkekysymykseen ei ollut vastattu).
        return Esto("malli:lopetus_ohitettu_kysymys", jatko=listan_kysymys)
    if oire_kesken:
        # Oire käsittelyssä (jatkokysymykset, kotihoito-ohjeet kesken): malli ohitti ohjeen
        # ja lopetti (12.10. testipankki: "vapinaa käsissä" viimeiseen kysymykseen -> lopetus).
        return Esto("malli:lopetus_ohitettu_oire", jatko=OIREEN_TARKENNUS)
    if HUONO_VOINTI.search(henkilon_teksti.lower()):
        # "No huonosti" ei ole käsitelty asia (11.10.: malli kiitti ja lopetti puhelun).
        return Esto("malli:lopetus_ohitettu_huono_vointi", jatko=HUONON_VOINNIN_JATKO)
    if lista_kesken and not saa_lopettaa_kesken:
        # Ohje sallii LOPETUKSEN vasta, kun lääkekysymykset on käyty (12.10. puhelu: kolmesta
        # kysytty yksi, malli kuuli päänsäryn paranemiseksi ja lopetti). Vain kerran: toinen
        # LOPETUS hyväksytään, ettei ketään pidetä puhelimessa vastoin tahtoa, jos säännöt
        # eivät tunnistaneet lopetuspyyntöä. Seuraava kysymys lisätään (kysymyslista).
        return Esto("malli:lopetus_ohitettu_lista", vain_kerran=True)
    return None


def ilman_kiitoksia(puhuttava: str) -> str:
    """Mallin teksti ennen lopetuslausetta ilman kiitos- ja välitän-lauseita ja kysymyksiä."""
    return " ".join(
        lause
        for lause in jaa_lauseiksi(puhuttava)
        if not _KIITOSLAUSE.match(lause) and not lause.endswith("?")
    )
