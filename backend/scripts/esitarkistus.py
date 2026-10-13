"""VAIN DEV: demon esitarkistus. Aja 5-10 min ennen demoa ja aina demon harjoittelun jälkeen.

Kaytto (backend/):
    uv run python scripts/esitarkistus.py            # nopea (~5 s)
    uv run python scripts/esitarkistus.py --lammita  # + lämmittää mallin oikealla ohjeella

Tarkistaa ketjun siinä järjestyksessä kuin puhelu käyttää sitä: asetukset -> backend ->
ngrok-tunneli julkisen osoitteen kautta (sama reitti kuin Twiliolla) -> soittojono -> malli
muistissa ja lämmin -> koneen muisti -> Twilion tunnukset ja tuoreet virheet. Ei soita
eikä kirjoita mitään. VIRHE = korjaa ennen demoa (paluukoodi 1), VAROITUS = riski.

11.10.: "application error" kesken puhelun johtui ngrok-katkosta (Twilio sai 404:n ngrokin
reunalta). Tunnelin tarkistus julkisen osoitteen kautta näkee sen, paikallinen ei.
"""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/: app-paketti

from app.config import hae_asetukset  # noqa: E402
from app.puhelu.lammitys import LAMMIN_RAJA_S, lammita_malli  # noqa: E402

BACKEND = "http://127.0.0.1:8000"
NGROK_API = "http://127.0.0.1:4040/api/tunnels"
DOCKER_ASETUKSET = Path.home() / "Library/Group Containers/group.com.docker/settings-store.json"
# Lämmin malli: uuden puhelun lämmitys 6-8 s (mitattu 11.10.), kylmä 46-54 s.
DOCKER_MUISTI_MAX_MIB = 4096
SWAP_VAROITUS_GT = 4.0

virheita = 0


def ok(viesti: str) -> None:
    print(f"  OK        {viesti}")


def varoitus(viesti: str) -> None:
    print(f"  VAROITUS  {viesti}")


def virhe(viesti: str) -> None:
    global virheita
    virheita += 1
    print(f"  VIRHE     {viesti}")


def tarkista_asetukset() -> None:
    print("Asetukset (.env)")
    a = hae_asetukset()
    pakolliset = {
        "LLM_MALLI": a.llm_malli,
        "JULKINEN_URL": a.julkinen_url,
        "DEMO_PUHELINNUMERO": a.demo_puhelinnumero,
        "TWILIO_ACCOUNT_SID": a.twilio_account_sid,
        "TWILIO_AUTH_TOKEN": a.twilio_auth_token,
        "TWILIO_NUMERO": a.twilio_numero,
    }
    puuttuvat = [nimi for nimi, arvo in pakolliset.items() if not arvo]
    if puuttuvat:
        virhe(f"puuttuu: {', '.join(puuttuvat)}")
    else:
        ok("pakolliset asetukset löytyvät")
    if a.puhelu_tapa != "relay":
        varoitus(f"PUHELU_TAPA={a.puhelu_tapa} (demo on harjoiteltu relaylla)")
    if not a.llm_pida_muistissa:
        varoitus("LLM_PIDA_MUISTISSA=false: malli voi pudota muistista soittojen välissä")


def tarkista_backend() -> None:
    print("Backend")
    try:
        vastaus = httpx.get(f"{BACKEND}/terveys", timeout=5)
    except httpx.HTTPError as poikkeus:
        virhe(f"ei vastaa ({poikkeus.__class__.__name__}): käynnistä uvicorn")
        return
    if vastaus.status_code == 200:
        ok("vastaa paikallisesti")
    else:
        virhe(f"/terveys palautti {vastaus.status_code}")


def tarkista_tunneli() -> None:
    print("ngrok-tunneli (Twilion reitti)")
    julkinen = (hae_asetukset().julkinen_url or "").rstrip("/")
    try:
        tunnelit = httpx.get(NGROK_API, timeout=5).json()["tunnels"]
    except (httpx.HTTPError, KeyError, ValueError):
        virhe("ngrok-agentti ei vastaa (127.0.0.1:4040): käynnistä ngrok")
        return
    osoitteet = [tunneli["public_url"].rstrip("/") for tunneli in tunnelit]
    if julkinen not in osoitteet:
        virhe(f"JULKINEN_URL ({julkinen}) ei ole ngrokin tunneli: {osoitteet}")
        return
    ok(f"tunneli auki: {julkinen}")

    alku = time.perf_counter()
    try:
        vastaus = httpx.get(
            f"{julkinen}/terveys", headers={"ngrok-skip-browser-warning": "1"}, timeout=10
        )
    except httpx.HTTPError as poikkeus:
        virhe(f"julkinen osoite ei vastaa ({poikkeus.__class__.__name__}): verkko tai ngrok poikki")
        return
    kesto_ms = round((time.perf_counter() - alku) * 1000)
    if vastaus.status_code != 200:
        # 404 ngrokin reunalta = tunneli katki (11.10. application error).
        virhe(f"julkinen /terveys palautti {vastaus.status_code}: käynnistä ngrok uudelleen")
        return
    ok(f"julkinen osoite vastaa ({kesto_ms} ms)")
    if kesto_ms > 2000:
        varoitus("tunneli hidas: verkko pätkii? Harkitse puhelimen hotspotia")


def tarkista_soittojono() -> None:
    print("Soittojono")
    try:
        tehtavat = httpx.get(f"{BACKEND}/puhelutehtavat", timeout=10).json()
    except (httpx.HTTPError, ValueError):
        virhe("soittojonoa ei saatu (kanta pystyssä? docker compose up -d)")
        return
    if not tehtavat:
        virhe("soittojono tyhjä: POST /puhelutehtavat/tunnista tai /dev/-reitit")
        return
    ok(f"{len(tehtavat)} soittotehtävää jonossa")


def tarkista_malli(lammita: bool) -> None:
    print("Malli (Ollama)")
    a = hae_asetukset()
    ollama = a.llm_base_url.removesuffix("/v1").rstrip("/")
    try:
        ladatut = httpx.get(f"{ollama}/api/ps", timeout=5).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        virhe("Ollama ei vastaa: OLLAMA_KEEP_ALIVE=-1 ollama serve")
        return
    malli = next((m for m in ladatut if m["name"].split(":")[0] == a.llm_malli.split(":")[0]), None)
    if malli is None:
        varoitus(f"{a.llm_malli} ei ole muistissa: ensimmäinen soitto on kylmä (aja --lammita)")
    elif malli.get("expires_at", "")[:4] < "2100":
        varoitus(f"{a.llm_malli} putoaa muistista {malli.get('expires_at')}")
    else:
        ok(f"{a.llm_malli} muistissa pysyvästi")
    if lammita:
        _lammita()


def _lammita() -> None:
    """Sama lämmitys kuin backendin lämmitysvahdissa (app/puhelu/lammitys.py)."""
    try:
        kesto = lammita_malli(hae_asetukset())
    except httpx.HTTPError as poikkeus:
        virhe(f"lämmitys epäonnistui: {poikkeus}")
        return
    if kesto <= LAMMIN_RAJA_S:
        ok(f"lämmitys {kesto:.1f} s: malli lämmin")
    else:
        varoitus(f"lämmitys {kesto:.1f} s: oli kylmä, aja --lammita vielä kerran tai kaksi")


def tarkista_muisti() -> None:
    print("Koneen muisti (16 Gt Mac)")
    try:
        swap = subprocess.run(
            ["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, check=True
        ).stdout
        kaytetty_mt = float(swap.split("used = ")[1].split("M")[0])
    except (OSError, subprocess.CalledProcessError, IndexError, ValueError):
        varoitus("swapin käyttöä ei saatu luettua")
        kaytetty_mt = 0.0
    kaytetty_gt = kaytetty_mt / 1024
    if kaytetty_gt > SWAP_VAROITUS_GT:
        varoitus(
            f"swap {kaytetty_gt:.1f} Gt: vuorot hidastuvat. Sulje raskaat ohjelmat, "
            "ollama stop <malli> ja --lammita (lataa mallin puhtaaseen muistiin)"
        )
    else:
        ok(f"swap {kaytetty_gt:.1f} Gt")
    try:
        docker_mib = json.loads(DOCKER_ASETUKSET.read_text())["MemoryMiB"]
    except (OSError, KeyError, ValueError):
        return
    if docker_mib > DOCKER_MUISTI_MAX_MIB:
        varoitus(f"Docker Desktopin muisti {docker_mib} MiB: laita 2048 (Postgres riittää)")
    else:
        ok(f"Docker Desktopin muisti {docker_mib} MiB")


def _pmset(*argumentit: str) -> str:
    try:
        return subprocess.run(
            ["pmset", *argumentit], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return ""


def tarkista_virta() -> None:
    """11.10.: molemmat "application error" -kaatumiset olivat Macin lepotila kesken puhelun
    (akulla näyttö 2 min + lepo 1 min): Twilion kutsut eivät päässeet koneelle."""
    print("Virta ja lepotila")
    akku = _pmset("-g", "batt")
    if not akku:
        varoitus("virtatietoja ei saatu (ei macOS?)")
        return
    verkkovirta = "AC Power" in akku
    prosentti = next((osa for osa in akku.split() if osa.endswith("%;")), "?").rstrip(";")
    if verkkovirta:
        ok(f"verkkovirrassa ({prosentti})")
    else:
        virhe(f"akulla ({prosentti}): kytke laturi, malli syö akun nopeasti")
    # Vain rajoittamaton caffeinate kelpaa: Claude Code ajaa "caffeinate -i -t 300" vain
    # työskentelyn ajan, ja Mac nukahti heti sen jälkeen (11.10.).
    try:
        prosessit = subprocess.run(
            ["ps", "-axo", "command"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        prosessit = []
    # PowerShell käynnistää koko polulla (/usr/bin/caffeinate), zsh pelkällä nimellä.
    pysyva = [
        rivi
        for rivi in prosessit
        if rivi.split(" ", 1)[0].endswith("caffeinate") and " -t " not in rivi
    ]
    if pysyva:
        ok(f"lepotila estetty: {pysyva[0][:60]}")
    else:
        virhe("Mac voi nukahtaa kesken puhelun: käynnistä uvicorn caffeinaten kautta (CLAUDE.md)")


def tarkista_twilio() -> None:
    print("Twilio")
    a = hae_asetukset()
    if not (a.twilio_account_sid and a.twilio_auth_token):
        return
    tunnus = (a.twilio_account_sid, a.twilio_auth_token)
    try:
        tili = httpx.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{a.twilio_account_sid}.json",
            auth=tunnus,
            timeout=10,
        )
    except httpx.HTTPError as poikkeus:
        virhe(f"Twilioon ei saada yhteyttä ({poikkeus.__class__.__name__}): verkko?")
        return
    if tili.status_code != 200:
        virhe(f"Twilion tunnukset eivät kelpaa ({tili.status_code})")
        return
    ok(f"tili {tili.json().get('status')}")
    alku = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        halytykset = (
            httpx.get(
                "https://monitor.twilio.com/v1/Alerts",
                params={"StartDate": alku, "PageSize": 20},
                auth=tunnus,
                timeout=10,
            )
            .json()
            .get("alerts", [])
        )
    except (httpx.HTTPError, ValueError):
        varoitus("Twilion hälytyksiä ei saatu")
        return
    virheet = [h for h in halytykset if h.get("log_level") == "error"]
    if not virheet:
        ok("ei Twilio-virheitä viimeisen tunnin aikana")
        return
    for halytys in virheet:
        varoitus(
            f"Twilio {halytys['error_code']} {halytys['date_created']}: "
            f"{(halytys.get('alert_text') or '')[:120]}"
        )


def main() -> None:
    # 12.10.: kirjoitusvirhe "--lammitaa" ohitti lämmityksen hiljaa.
    tuntemattomat = [arg for arg in sys.argv[1:] if arg != "--lammita"]
    if tuntemattomat:
        print(f"Tuntematon valitsin: {' '.join(tuntemattomat)} (ainoa valitsin: --lammita)")
        sys.exit(2)
    lammita = "--lammita" in sys.argv
    tarkista_asetukset()
    tarkista_backend()
    tarkista_tunneli()
    tarkista_soittojono()
    tarkista_malli(lammita)
    tarkista_muisti()
    tarkista_virta()
    tarkista_twilio()
    print()
    if virheita:
        print(f"{virheita} VIRHETTÄ: korjaa ennen demoa.")
        sys.exit(1)
    print("Valmis demoon (lue varoitukset).")


if __name__ == "__main__":
    main()
