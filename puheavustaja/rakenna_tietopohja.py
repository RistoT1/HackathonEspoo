#!/usr/bin/env python3
"""
Rakentaa puheavustajan potilaskohtaiset soittoprofiilit synteettisestä datasta.

    data/csv/patients.csv + data/csv/medications.csv
        + knowledge/laakkeet.json + knowledge/keskustelusaannot.json
        -> knowledge/soittoprofiilit.json

Soittoprofiili = mitä puhelussa saa kysyä yhdeltä henkilöltä: uudet lääkkeet
selkokielisinä ja niiden tunnetuista haittavaikutuksista muodostetut kysymykset.

Datan minimointi: profiiliin EI tallenneta nimeä, henkilötunnusta, osoitetta eikä
diagnooseja. Henkilö yksilöidään vain datan pseudonyymillä tunnuksella (Id).

Käyttö:
    python rakenna_tietopohja.py                  # oletukset
    python rakenna_tietopohja.py --paivia 730 --min-ika 65

Vain Python-standardikirjasto (csv, json), ei pandas-riippuvuutta.
"""

import argparse
import csv
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

TAMA_KANSIO = Path(__file__).resolve().parent
OLETUS_DATA = TAMA_KANSIO.parent / "data" / "csv"
KNOWLEDGE = TAMA_KANSIO / "knowledge"

# ---------------------------------------------------------------- DATAN KENTÄT
# Kaikki kenttänimet tässä yhdessä paikassa (Synthea-muoto). Kun Veil.AI:n data
# tulee 13.10., vaihda vain nämä. Oletukset:
#  - medications.csv: yksi rivi per määräysjakso; sama lääke uusitaan uutena
#    rivinä samalla CODE:lla -> "uusi lääke" = ensimmäinen START per (potilas, CODE).
#  - STOP tyhjä = lääke edelleen käytössä.
#  - Päivämäärät ISO-muodossa, mahdollisesti aikaleimalla (2020-04-28T18:58:37Z).
K_POTILAS_ID = "Id"
K_SYNTYMA = "BIRTHDATE"
K_KUOLEMA = "DEATHDATE"
K_LAAKE_POTILAS = "PATIENT"
K_LAAKE_ALKU = "START"
K_LAAKE_LOPPU = "STOP"
K_LAAKE_KOODI = "CODE"
K_LAAKE_KUVAUS = "DESCRIPTION"


def lue_pvm(arvo: str) -> date | None:
    if not arvo:
        return None
    return datetime.fromisoformat(arvo[:10]).date()


def ikaryhma(ika: int) -> str:
    alaraja = (ika // 10) * 10
    return f"{alaraja}-{alaraja + 9}"


# ---------------------------------------------------------------- LÄÄKETIETOPOHJA
def lataa_laakkeet(polku: Path) -> list[dict]:
    with open(polku, encoding="utf-8") as f:
        return json.load(f)["laakkeet"]


def tunnista_laake(kuvaus: str, laakkeet: list[dict]) -> dict | None:
    """Palauttaa ensimmäisen tietopohjan merkinnän, joka vastaa lääkkeen kuvausta."""
    teksti = re.sub(r"\s+", " ", kuvaus.lower())
    for laake in laakkeet:
        kaikki = laake.get("tunnisteet")
        joku = laake.get("tunnisteet_mika_tahansa")
        osuu_kaikki = bool(kaikki) and all(t in teksti for t in kaikki)
        osuu_joku = bool(joku) and any(t in teksti for t in joku)
        if osuu_kaikki or osuu_joku:
            return laake
    return None


# ---------------------------------------------------------------- PROFIILIT
def lue_potilaat(polku: Path, viitepaiva: date, min_ika: int) -> dict[str, int]:
    """Palauttaa {potilastunnus: ikä} elossa oleville, vähintään min_ika-vuotiaille."""
    potilaat = {}
    with open(polku, encoding="utf-8", newline="") as f:
        for rivi in csv.DictReader(f):
            if rivi[K_KUOLEMA]:
                continue
            syntynyt = lue_pvm(rivi[K_SYNTYMA])
            ika = (viitepaiva - syntynyt).days // 365
            if ika < min_ika:
                continue
            potilaat[rivi[K_POTILAS_ID]] = ika
    return potilaat


def lue_laakejaksot(polku: Path) -> list[dict]:
    with open(polku, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def rakenna_profiilit(
    data_kansio: Path, paivia: int, min_ika: int, max_kysymykset: int
) -> tuple[dict, dict]:
    laakkeet = lataa_laakkeet(KNOWLEDGE / "laakkeet.json")
    jaksot = lue_laakejaksot(data_kansio / "medications.csv")

    # Viitepäivä = datan uusin lääkkeen aloitus ("tänään" synteettisessä datassa).
    viitepaiva = max(lue_pvm(j[K_LAAKE_ALKU]) for j in jaksot)
    alkaen = viitepaiva - timedelta(days=paivia)
    potilaat = lue_potilaat(data_kansio / "patients.csv", viitepaiva, min_ika)

    # Ensimmäinen aloitus per (potilas, lääkekoodi) + onko yhä käytössä.
    ensimmainen: dict[tuple[str, str], dict] = {}
    kaytossa: set[tuple[str, str]] = set()
    for j in jaksot:
        potilas = j[K_LAAKE_POTILAS]
        if potilas not in potilaat:
            continue
        avain = (potilas, j[K_LAAKE_KOODI])
        alku = lue_pvm(j[K_LAAKE_ALKU])
        aiempi = ensimmainen.get(avain)
        if aiempi is None or alku < aiempi["alku"]:
            ensimmainen[avain] = {"alku": alku, "kuvaus": j[K_LAAKE_KUVAUS]}
        if not j[K_LAAKE_LOPPU]:
            kaytossa.add(avain)
    for avain, tieto in ensimmainen.items():
        tieto["kaytossa"] = avain in kaytossa

    tilasto = {"tunnistamaton": {}, "ei_kysyta": {}}
    profiilit: dict[str, dict] = {}
    for (potilas, _koodi), tieto in sorted(ensimmainen.items(), key=lambda x: x[1]["alku"]):
        if tieto["alku"] < alkaen:
            continue
        laake = tunnista_laake(tieto["kuvaus"], laakkeet)
        if laake is None:
            tilasto["tunnistamaton"][tieto["kuvaus"]] = tilasto["tunnistamaton"].get(tieto["kuvaus"], 0) + 1
            continue
        if not laake.get("puhelinkysely"):
            tilasto["ei_kysyta"][laake["avain"]] = tilasto["ei_kysyta"].get(laake["avain"], 0) + 1
            continue

        profiili = profiilit.setdefault(
            potilas,
            {
                "tunnus": potilas,
                "ikaryhma": ikaryhma(potilaat[potilas]),
                "uudet_laakkeet": [],
            },
        )
        jo_lisatty = {l["avain"] for l in profiili["uudet_laakkeet"]}
        if laake["avain"] in jo_lisatty:
            continue
        paivaa_sitten = (viitepaiva - tieto["alku"]).days
        profiili["uudet_laakkeet"].append(
            {
                "avain": laake["avain"],
                "selkonimi": laake["selkonimi"],
                "kerro_kayttotarkoitus": laake.get("kerro_kayttotarkoitus", True),
                "aloitettu": tieto["alku"].isoformat(),
                "viikkoa_sitten": max(1, round(paivaa_sitten / 7)),
                "kaytossa": tieto["kaytossa"],
                "kysymykset": [h["kysymys"] for h in laake["haittavaikutukset"][:max_kysymykset]],
                "seurattavat_oireet": [h["oire"] for h in laake["haittavaikutukset"]],
            }
        )

    # Uusin lääke ensin: siitä kysytään ensimmäisenä.
    for p in profiilit.values():
        p["uudet_laakkeet"].sort(key=lambda l: l["aloitettu"], reverse=True)

    meta = {
        "luotu": datetime.now().isoformat(timespec="seconds"),
        "viitepaiva": viitepaiva.isoformat(),
        "aikaikkuna_paivia": paivia,
        "min_ika": min_ika,
        "profiileja": len(profiilit),
        "ohitetut": tilasto,
    }
    return meta, profiilit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=OLETUS_DATA, help="CSV-kansio (oletus ../data/csv)")
    ap.add_argument("--paivia", type=int, default=365, help="kuinka tuoreet aloitukset lasketaan uusiksi")
    ap.add_argument("--min-ika", type=int, default=65)
    args = ap.parse_args()

    with open(KNOWLEDGE / "keskustelusaannot.json", encoding="utf-8") as f:
        max_kysymykset = json.load(f)["max_laakekysymykset"]

    meta, profiilit = rakenna_profiilit(args.data, args.paivia, args.min_ika, max_kysymykset)
    ulos = KNOWLEDGE / "soittoprofiilit.json"
    with open(ulos, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "profiilit": list(profiilit.values())}, f, ensure_ascii=False, indent=2)

    print(f"Viitepäivä {meta['viitepaiva']}, ikkuna {args.paivia} pv, ikä >= {args.min_ika}")
    print(f"Soittoprofiileja: {meta['profiileja']} -> {ulos}")
    if meta["ohitetut"]["ei_kysyta"]:
        print("Ohitettu (ei puhelinkyselyyn):", meta["ohitetut"]["ei_kysyta"])
    if meta["ohitetut"]["tunnistamaton"]:
        print("Tunnistamattomat lääkkeet (lisää laakkeet.json:iin):")
        for kuvaus, n in meta["ohitetut"]["tunnistamaton"].items():
            print(f"  {n:>3}  {kuvaus}")


if __name__ == "__main__":
    main()
