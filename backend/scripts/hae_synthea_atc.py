"""VAIN DEV: Synthean RxNorm-koodeille ATC-koodit NLM RxNav -palvelusta.

Tuotannossa ATC tulee datan mukana (Kanta/Reseptikeskus), eika tata skriptia tarvita.
Kayttaa vain julkisia laakekoodeja; potilastietoa ei lahde.

ATC voi olla myos 5-merkkinen ryhmataso (esim. N02AJ), kun aine ei ole yksiselitteinen.
Valmisteen ATC = sen ainesosan 7-merkkinen ATC, joka mainitaan valmisteen nimessa ensimmaisena
(yhdistelmavalmisteissa ensisijainen aine). Jos aineella on useita ATC-koodeja (antotapa),
valitaan laakemuodon mukaan: inhalaattori -> R03, nenasumute -> R01, iho -> D, silma/korva -> S,
muuten systeeminen (ei D/S). KASIN_TARKISTETUT ohittaa automaattisen valinnan (ks. syyt).

Aja (backend/):  PYTHONPATH=. uv run python scripts/hae_synthea_atc.py
Tulos:           app/data/synthea_atc.json (versionhallintaan)
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import text

from app.tietokanta import hae_engine

RXNAV = "https://rxnav.nlm.nih.gov/REST"
TULOS = Path(__file__).resolve().parents[1] / "app" / "data" / "synthea_atc.json"
TAUKO_S = 0.1  # kohtuullinen kuorma julkiselle palvelulle

# Tarkistetut ATC:t (WHO ATC), jotka ohittavat automaattisen valinnan. Syyt:
# - RxNav ei anna ATC:ta (brandi- tai suolamuoto)
# - aineella on useita systeemisia koodeja, eika valmisteelle ole yksiselitteista valintaa
#   (tuotannossa ATC on valmistekohtainen tieto, eika tata ongelmaa ole)
# - yhdistelmassa kliinisesti olennainen aine on opioidi (N02AJ = opioidi + ei-opioidi)
# - Synthean datavirhe: koodi ei vastaa nimea (silloin noudatetaan nimea)
# Ehkaisyvalmisteet, joiden koodista ei olla varmoja, jatetaan tyhjiksi (null).
KASIN_TARKISTETUT: dict[str, str | None] = {
    "205923": "B03XA01",  # epoetiini alfa (ei ATC:ta RxNavista)
    "904419": "M05BA04",  # alendronihappo (ei ATC:ta RxNavista)
    "966222": "H03AA01",  # levotyroksiini (ei ATC:ta RxNavista)
    "105078": "J01CE01",  # bentsyylipenisilliini (ei ATC:ta RxNavista)
    "1803932": "V03AF03",  # kalsiumfolinaatti (ei ATC:ta RxNavista)
    "1650142": "J01AA02",  # doksisykliini suun kautta (ei A01AB22)
    "1652673": "J01AA02",  # doksisykliini suun kautta
    "1660014": "C01CA24",  # adrenaliini injektio (ei A01AD01)
    "1870230": "C01CA24",  # adrenaliini autoinjektori
    "243670": "B01AC06",  # asetyylisalisyylihappo 81 mg: veren hyytymisen esto
    "312617": "H02AB07",  # prednisoni (ei A07EA03)
    "198405": "M01AE01",  # ibuprofeeni suun kautta (ei C01EB16)
    "310965": "M01AE01",  # ibuprofeeni suun kautta
    "198014": "M01AE02",  # naprokseeni suun kautta (ei G02CC02)
    "849574": "M01AE02",  # naprokseeni suun kautta
    "1049221": "N02AJ",  # oksikodoni + parasetamoli: opioidiyhdistelma
    "856987": "N02AJ",  # hydrokodoni + parasetamoli
    "857005": "N02AJ",  # hydrokodoni + parasetamoli
    "856980": "N02AJ",  # hydrokodoni + parasetamoli
    "833036": "C09AA01",  # Synthean nimi "Captopril", koodi on RxNormissa toinen valmiste
    "2119714": "L01FD01",  # trastutsumabi (ei hyaluronidaasi B06AA03)
    "1359133": None,  # ehkaisyvalmiste rautalisalla: koodi epavarma
}

_MUOTO_ETULIITE = (
    (("inhaler", "inhalation", "actuat"), "R03"),
    (("nasal",), "R01"),
    (("topical", "cream", "ointment", "lotion", "transdermal"), "D"),
    (("ophthalmic", "otic"), "S"),
)


def valitse_muodon_mukaan(koodit: list[str], nimi: str) -> str | None:
    if not koodit:
        return None
    nimi_pienena = nimi.lower()
    for avainsanat, etuliite in _MUOTO_ETULIITE:
        if any(sana in nimi_pienena for sana in avainsanat):
            sopivat = [koodi for koodi in koodit if koodi.startswith(etuliite)]
            if sopivat:
                return sopivat[0]
    systeemiset = [koodi for koodi in koodit if koodi[0] not in ("D", "S")]
    return (systeemiset or koodit)[0]


def ainesosat(asiakas: httpx.Client, rxcui: str) -> list[tuple[str, str]]:
    vastaus = asiakas.get(f"{RXNAV}/rxcui/{rxcui}/related.json", params={"tty": "IN"})
    vastaus.raise_for_status()
    ryhmat = vastaus.json().get("relatedGroup", {}).get("conceptGroup", []) or []
    return [
        (kasite["rxcui"], kasite["name"])
        for ryhma in ryhmat
        for kasite in ryhma.get("conceptProperties", []) or []
    ]


def ainesosan_atc_koodit(asiakas: httpx.Client, rxcui: str) -> list[str]:
    vastaus = asiakas.get(f"{RXNAV}/rxcui/{rxcui}/property.json", params={"propName": "ATC"})
    vastaus.raise_for_status()
    ryhma = vastaus.json().get("propConceptGroup") or {}
    return sorted(p["propValue"] for p in ryhma.get("propConcept", []) if p.get("propValue"))


def valmisteen_atc(asiakas: httpx.Client, rxcui: str, nimi: str) -> str | None:
    nimi_pienena = nimi.lower()

    def sijainti(aine: tuple[str, str]) -> int:
        kohta = nimi_pienena.find(aine[1].lower())
        return kohta if kohta >= 0 else len(nimi_pienena)

    for aine_rxcui, _ in sorted(ainesosat(asiakas, rxcui), key=sijainti):
        time.sleep(TAUKO_S)
        atc = valitse_muodon_mukaan(ainesosan_atc_koodit(asiakas, aine_rxcui), nimi)
        if atc:
            return atc
    return None


def main() -> None:
    with hae_engine().connect() as yhteys:
        rivit = yhteys.execute(
            text("SELECT code, min(description) FROM demo.medications GROUP BY code ORDER BY code")
        ).all()

    vastaavuus: dict[str, str | None] = {}
    with httpx.Client(timeout=20) as asiakas:
        for koodi, nimi in rivit:
            if koodi in KASIN_TARKISTETUT:
                vastaavuus[koodi] = KASIN_TARKISTETUT[koodi]
            else:
                vastaavuus[koodi] = valmisteen_atc(asiakas, koodi, nimi)
            time.sleep(TAUKO_S)

    sisalto = {
        "_kuvaus": "Synthea RxNorm-koodi -> ATC (7 merkkia). VAIN DEV-data; tuotannossa ATC "
        "tulee datan mukana. Tuotettu skriptilla scripts/hae_synthea_atc.py.",
        "_lahde": "NLM RxNav (rxnav.nlm.nih.gov): valmiste -> ainesosa (IN) -> ATC; "
        "monitulkintaiset ja puuttuvat tarkistettu kasin (KASIN_TARKISTETUT skriptissa)",
        "_haettu": datetime.now(UTC).date().isoformat(),
        "atc": vastaavuus,
    }
    TULOS.write_text(json.dumps(sisalto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loytyi = sum(1 for atc in vastaavuus.values() if atc)
    print(f"{loytyi}/{len(vastaavuus)} valmisteelle ATC -> {TULOS}")


if __name__ == "__main__":
    main()
