#!/usr/bin/env python3
"""
Käynnistää puheavustajan automaattisesti yhdellä komennolla.

Tekee järjestyksessä:
  1. Tarkistaa Python-paketit (kysyy luvan asentaa puuttuvat).
  2. Varmistaa että Ollama on käynnissä (käynnistää 'ollama serve' tarvittaessa)
     ja että malli on ladattu (ajaa 'ollama pull' tarvittaessa).
  3. Tarkistaa suomenkielisen äänen (Piper) ja lataa sen tarvittaessa aanet/-kansioon.
  4. Rakentaa knowledge/soittoprofiilit.json, jos se puuttuu tai data/tietopohja on muuttunut.
  5. Käynnistää puheavustaja.py:n.

Käyttö:
    python autorun.py                       # tarkistukset + puhelu
    python autorun.py --potilas 1bacaab1    # muut argumentit välitetään puheavustajalle
    python autorun.py --teksti              # testaus kirjoittamalla
    python autorun.py --vain-tarkistus      # aja tarkistukset, älä käynnistä puhelua
    python autorun.py --kylla               # älä kysy, asenna puuttuvat automaattisesti

Vain standardikirjasto, jotta tämä toimii ennen kuin paketit on asennettu.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

KANSIO = Path(__file__).resolve().parent
DATA = KANSIO.parent / "data" / "csv"
KNOWLEDGE = KANSIO / "knowledge"
PROFIILIT = KNOWLEDGE / "soittoprofiilit.json"

OLLAMA_PERUS = "http://localhost:11434"


def _lue_malli() -> str:
    """Lukee mallin nimen puheavustaja.py:n OLLAMA_MODEL-rivistä (yksi totuuden lähde).
    Ei importtia, koska paketteja ei ehkä ole vielä asennettu."""
    teksti = (KANSIO / "puheavustaja.py").read_text(encoding="utf-8")
    osuma = re.search(r'^OLLAMA_MODEL\s*=\s*"([^"]+)"', teksti, flags=re.MULTILINE)
    return osuma.group(1) if osuma else "gemma4:e4b"


OLLAMA_MALLI = _lue_malli()           # muuta mallia vain puheavustaja.py:ssä

# (tuontinimi, pip-nimi, milloin tarvitaan: "aina" / "mikrofoni" / "aani")
PAKETIT = [
    ("numpy", "numpy", "aina"),
    ("requests", "requests", "aina"),
    ("sounddevice", "sounddevice", "mikrofoni"),
    ("faster_whisper", "faster-whisper", "mikrofoni"),
    ("sounddevice", "sounddevice", "aani"),
    ("piper", "piper-tts", "aani"),
]

OMAT_LIPUT = {"--vain-tarkistus", "--kylla"}


def ok(teksti: str) -> None:
    print(f"  [ok]   {teksti}")


def varoitus(teksti: str) -> None:
    print(f"  [!]    {teksti}")


def virhe(teksti: str) -> None:
    sys.exit(f"  [VIRHE] {teksti}")


def kysy(kysymys: str, kylla: bool) -> bool:
    if kylla:
        return True
    try:
        return input(f"  {kysymys} [k/E] ").strip().lower() in ("k", "kyllä", "kylla", "y", "yes")
    except EOFError:
        return False


# ---------------------------------------------------------------- 1. PAKETIT
def tarkista_paketit(tekstitila: bool, ei_tts: bool, kylla: bool) -> None:
    print("1/4 Python-paketit")
    kaytossa = {"aina", *([] if tekstitila else ["mikrofoni"]), *([] if ei_tts else ["aani"])}
    tarvittavat = [(t, p) for t, p, milloin in PAKETIT if milloin in kaytossa]
    puuttuvat = list(dict.fromkeys(p for t, p in tarvittavat if importlib.util.find_spec(t) is None))
    if not puuttuvat:
        ok("kaikki asennettu")
        return
    varoitus(f"puuttuu: {', '.join(puuttuvat)}")
    if not kysy(f"Asennetaanko pip:llä ({' '.join(puuttuvat)})?", kylla):
        virhe(f"Asenna käsin: {sys.executable} -m pip install {' '.join(puuttuvat)}")
    subprocess.run([sys.executable, "-m", "pip", "install", *puuttuvat], check=True)
    ok("asennettu")


# ---------------------------------------------------------------- 2. OLLAMA
def ollama_mallit() -> list[str] | None:
    """Palauttaa ladatut mallit, tai None jos Ollama ei vastaa."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_PERUS}/api/tags", timeout=2) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return None


def kaynnista_ollama() -> None:
    ollama = shutil.which("ollama")
    if not ollama:
        virhe("Ollama ei ole käynnissä eikä 'ollama'-komentoa löydy. Asenna: https://ollama.com")
    print("  Käynnistetään 'ollama serve' taustalle...")
    liput = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [ollama, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=liput,
        start_new_session=os.name != "nt",
    )
    for _ in range(30):
        time.sleep(1)
        if ollama_mallit() is not None:
            return
    virhe("Ollama ei käynnistynyt 30 sekunnissa.")


def tarkista_ollama(kylla: bool) -> None:
    print("2/4 Ollama")
    mallit = ollama_mallit()
    if mallit is None:
        kaynnista_ollama()
        mallit = ollama_mallit() or []
    ok(f"käynnissä ({OLLAMA_PERUS})")

    loytyy = any(m == OLLAMA_MALLI or m.startswith(OLLAMA_MALLI + ":") for m in mallit)
    if loytyy:
        ok(f"malli {OLLAMA_MALLI} ladattu")
        return
    varoitus(f"mallia {OLLAMA_MALLI} ei ole ladattu")
    if not kysy(f"Ladataanko 'ollama pull {OLLAMA_MALLI}' (voi kestää)?", kylla):
        virhe(f"Lataa käsin: ollama pull {OLLAMA_MALLI}")
    subprocess.run([shutil.which("ollama") or "ollama", "pull", OLLAMA_MALLI], check=True)
    ok("malli ladattu")


# ---------------------------------------------------------------- 3. ÄÄNI
def tarkista_aani(aani_nimi: str, ei_tts: bool, kylla: bool) -> None:
    print("3/4 Suomenkielinen ääni (Piper)")
    if ei_tts:
        ok("ei tarvita (--no-tts)")
        return
    sys.path.insert(0, str(KANSIO))
    import aani

    if aani.aani_loytyy(aani_nimi):
        ok(f"{aani_nimi}")
        return
    if aani_nimi not in aani.TUNNETUT_AANET:
        varoitus(f"ääntä '{aani_nimi}' ei löydy kansiosta aanet/ (kopioi sinne .onnx ja .onnx.json)")
        varoitus("jatketaan, vastaukset näytetään tekstinä")
        return
    varoitus(f"ääni {aani_nimi} puuttuu")
    if not kysy(f"Ladataanko suomenkielinen ääni {aani_nimi}?", kylla):
        varoitus("jatketaan ilman ääntä, vastaukset näytetään tekstinä")
        return
    aani.lataa_aani(aani_nimi)
    ok("ladattu kansioon aanet/")


# ---------------------------------------------------------------- 4. TIETOPOHJA
def tietopohja_vanhentunut() -> bool:
    if not PROFIILIT.exists():
        return True
    lahteet = [
        DATA / "medications.csv",
        DATA / "patients.csv",
        KNOWLEDGE / "laakkeet.json",
        KNOWLEDGE / "keskustelusaannot.json",
        KANSIO / "rakenna_tietopohja.py",
    ]
    uusin = max(p.stat().st_mtime for p in lahteet if p.exists())
    return uusin > PROFIILIT.stat().st_mtime


def tarkista_tietopohja() -> None:
    print("4/4 Tietopohja")
    if not (DATA / "medications.csv").exists() and not PROFIILIT.exists():
        virhe(f"Dataa ei löydy: {DATA}. Kopioi CSV:t sinne (ei ole repossa).")
    if not tietopohja_vanhentunut():
        ok(f"{PROFIILIT.name} ajan tasalla")
        return
    print("  Rakennetaan soittoprofiilit datasta...")
    subprocess.run([sys.executable, str(KANSIO / "rakenna_tietopohja.py")], check=True, cwd=KANSIO)
    ok("rakennettu")


# ---------------------------------------------------------------- PÄÄOHJELMA
def main() -> None:
    argumentit = sys.argv[1:]
    vain_tarkistus = "--vain-tarkistus" in argumentit
    kylla = "--kylla" in argumentit
    valitettavat = [a for a in argumentit if a not in OMAT_LIPUT]
    tekstitila = "--teksti" in valitettavat
    ei_tts = "--no-tts" in valitettavat
    aani_nimi = "fi_FI-harri-medium"
    if "--aani" in valitettavat and valitettavat.index("--aani") + 1 < len(valitettavat):
        aani_nimi = valitettavat[valitettavat.index("--aani") + 1]

    print("Puheavustajan automaattikäynnistys\n")
    try:
        tarkista_paketit(tekstitila, ei_tts, kylla)
        tarkista_ollama(kylla)
        tarkista_aani(aani_nimi, ei_tts, kylla)
        tarkista_tietopohja()
    except subprocess.CalledProcessError as e:
        virhe(f"Komento epäonnistui: {' '.join(map(str, e.cmd))}")

    if vain_tarkistus:
        print("\nTarkistukset valmiit.")
        return

    print("\nKäynnistetään puheavustaja...\n")
    tulos = subprocess.run([sys.executable, str(KANSIO / "puheavustaja.py"), *valitettavat], cwd=KANSIO)
    sys.exit(tulos.returncode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nKeskeytetty.")
