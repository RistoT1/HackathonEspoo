"""VAIN DEV: synteettisten henkilotietojen generointi hoitajan nakyman demoa varten.

EI oikeita henkilotietoja (saannot). Nimet ja henkilotunnus johdetaan deterministisesti
vanhus_id:sta, joten sama vanhus saa aina saman synteettisen nimen eri ajoilla.
"""

import hashlib

from sqlalchemy.orm import Session

from app.mallit import HenkilotietojenTaytto
from app.puhelutehtavat import varasto

# Yleisia suomalaisia etu- ja sukunimia (synteettiseen demodataan).
_ETUNIMET_NAINEN = [
    "Aino", "Eeva", "Helmi", "Impi", "Kaarina", "Liisa", "Maija", "Oili",
    "Pirkko", "Ritva", "Sirkka", "Tuula", "Vieno", "Aune", "Hilkka",
]
_ETUNIMET_MIES = [
    "Antero", "Eino", "Heikki", "Ilmari", "Kalevi", "Lauri", "Matti", "Olavi",
    "Pentti", "Reino", "Seppo", "Tapio", "Veikko", "Aarne", "Hannu",
]
_SUKUNIMET = [
    "Virtanen", "Korhonen", "Makinen", "Nieminen", "Makela", "Hamalainen",
    "Laine", "Heikkinen", "Koskinen", "Jarvinen", "Lehtonen", "Lehtinen",
    "Saarinen", "Salminen", "Heinonen", "Niemi", "Heikkila", "Kinnunen",
]

_HETU_VALIMERKIT = "-"  # 1900-luku; demossa riittaa yksi vuosisadan merkki


def _tiiviste(vanhus_id: str) -> int:
    """Vakaa kokonaisluku vanhus_id:sta (ei riipu Pythonin hash-siemenesta)."""
    return int(hashlib.sha256(vanhus_id.encode("utf-8")).hexdigest(), 16)


def _synteettinen_hetu(vanhus_id: str) -> str:
    """Teknisesti oikean muotoinen mutta synteettinen HETU. Ei vastaa oikeaa henkiloa."""
    h = _tiiviste(vanhus_id)
    paiva = h % 28 + 1
    kuukausi = (h // 100) % 12 + 1
    vuosi = 1935 + (h // 10000) % 25  # ikaihmisia: 1935-1959
    jarjestysluku = 2 + (h // 1_000_000) % 898  # 002-899
    runko = f"{paiva:02d}{kuukausi:02d}{vuosi % 100:02d}"
    tunnusosa = int(runko + f"{jarjestysluku:03d}")
    tarkiste_merkit = "0123456789ABCDEFHJKLMNPRSTUVWXY"
    tarkiste = tarkiste_merkit[tunnusosa % 31]
    return f"{runko}{_HETU_VALIMERKIT}{jarjestysluku:03d}{tarkiste}"


def synteettinen_henkilotieto(vanhus_id: str) -> tuple[str, str, str]:
    """(etunimi, sukunimi, hetu) deterministisesti vanhus_id:sta."""
    h = _tiiviste(vanhus_id)
    nainen = h % 2 == 0
    etunimet = _ETUNIMET_NAINEN if nainen else _ETUNIMET_MIES
    etunimi = etunimet[(h // 7) % len(etunimet)]
    sukunimi = _SUKUNIMET[(h // 13) % len(_SUKUNIMET)]
    return etunimi, sukunimi, _synteettinen_hetu(vanhus_id)


def tayta_henkilotiedot(sessio: Session) -> HenkilotietojenTaytto:
    """Tayttaa jonon vanhuksille synteettiset nimet ja hetun. Idempotentti: ajaa aina
    uudelleen samoilla arvoilla (deterministinen generointi)."""
    vanhus_idt = varasto.vanhus_idt_jonossa(sessio)
    for vanhus_id in vanhus_idt:
        etunimi, sukunimi, hetu = synteettinen_henkilotieto(vanhus_id)
        varasto.tallenna_henkilotieto(sessio, vanhus_id, etunimi, sukunimi, hetu)
    return HenkilotietojenTaytto(taytettyja=len(vanhus_idt))
