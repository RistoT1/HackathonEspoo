"""VAIN DEV: puhelulokin aikajana luettavaan muotoon.

Kaytto (backend/):
    uv run python scripts/puheluloki.py              # uusin puhelu
    uv run python scripts/puheluloki.py <tiedosto>   # tietty loki (lokit/puhelut/*.jsonl)
    uv run python scripts/puheluloki.py --seuraa     # reaaliajassa: odottaa uutta puhelua

Sarakkeet: aika puhelun alusta (s). Ajoitus kertoo, kauanko AI:n edellisestä vastauksesta
kului ennen kuin Twilio lähetti henkilön vastauksen, ja arvion AI:n puheen kestosta:
pieni tai negatiivinen kuunteluaika = henkilö puhui AI:n päälle. Osittaiset tunnistukset
näyttävät, milloin puhe alkoi kuulua ja mitä alusta mahdollisesti puuttui.
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

LOKIKANSIO = Path(__file__).resolve().parents[2] / "lokit" / "puhelut"


def _aika(rivi: dict) -> datetime:
    return datetime.fromisoformat(rivi["aika"])


def _ms(arvo: object) -> str:
    return f"{arvo / 1000:.1f}s" if isinstance(arvo, int | float) else "-"


def tulosta(polku: Path) -> None:
    rivit = [json.loads(rivi) for rivi in polku.read_text(encoding="utf-8").splitlines() if rivi]
    if not rivit:
        print("Tyhjä loki.")
        return
    alku = _aika(rivit[0])
    erillinen_kuultu = any(rivi["tapahtuma"] == "kuultu" for rivi in rivit)
    print(f"Puhelu {polku.name}\n")
    for rivi in rivit:
        _tulosta_rivi(rivi, alku, erillinen_kuultu)


def _tulosta_rivi(rivi: dict, alku: datetime, erillinen_kuultu: bool) -> None:
    t = (_aika(rivi) - alku).total_seconds()
    tapahtuma = rivi["tapahtuma"]
    if tapahtuma == "osittainen":
        print(
            f"{t:7.1f}  .. kuuluu: {rivi.get('teksti')!r}  "
            f"(AI:n vastauksesta {_ms(rivi.get('vastauksesta_ms'))})"
        )
    elif tapahtuma == "ohitettu_puhe":
        print(f"{t:7.1f}  xx ohitettu ({rivi.get('syy')}): {rivi.get('kuultu')!r}")
    elif tapahtuma == "jatkettu_puhe":
        print(f"{t:7.1f}  ++ jatkoi puhetta (malli mietti): {rivi.get('kuultu')!r}")
    elif tapahtuma == "puhe_ai_puhuessa":
        minne = "liitetty edelliseen vastaukseen" if rivi.get("liitetty") else "ohitettu"
        print(f"{t:7.1f}  .. puhui AI:n lukiessa ({minne}): {rivi.get('kuultu')!r}")
    elif tapahtuma == "kaiku":
        print(f"{t:7.1f}  xx kaiku (AI:n oma puhe, hylätty): {rivi.get('kuultu')!r}")
    elif tapahtuma == "puhe_alkoi":
        print(f"{t:7.1f}  .. henkilö alkoi puhua")
    elif tapahtuma == "hiljaisuus":
        print(f"{t:7.1f}  .. hiljaisuus {rivi.get('odotettu_s')} s: kysymys toistetaan")
    elif tapahtuma == "vuoro_alusta":
        print(f"{t:7.1f}  ++ vuoro alusta ({rivi.get('jatkoja')}.): {rivi.get('kuultu')!r}")
    elif tapahtuma == "kiireellinen_puhe":
        print(f"{t:7.1f}  !! hätä/vakava kesken mietinnän: {rivi.get('kuultu')!r}")
    elif tapahtuma == "vuoro_keskeytetty":
        print(f"{t:7.1f}  !! mallin vuoro keskeytetty, kiireellinen puhe käsitellään heti")
    elif tapahtuma == "valivastaus":
        print(
            f"{t:7.1f}  ~~ välitäyte: {rivi.get('sanottu')!r} "
            f"(malli miettinyt {_ms(rivi.get('vuoron_alusta_ms'))})"
        )
    elif tapahtuma == "puhelu_paattyi":
        tila, kesto = rivi.get("twilio_tila"), rivi.get("kesto_s")
        print(f"{t:7.1f}  ## puhelu päättyi: {tila}, {kesto} s")
    elif tapahtuma == "kuultu":
        _tulosta_kuultu(t, rivi)
    elif tapahtuma == "vuoro":
        _tulosta_vuoro(t, rivi, erillinen_kuultu)
    else:
        print(f"{t:7.1f}  {tapahtuma}: {rivi}")


def _tulosta_kuultu(t: float, rivi: dict) -> None:
    if rivi.get("vaihe") == "ALKU":
        print(f"{t:7.1f}  -- puheluun vastattiin")
        return
    print(f"{t:7.1f}  HENKILÖ: {rivi.get('kuultu')!r}  (varmuus {rivi.get('varmuus')})")
    _tulosta_ajoitus(rivi.get("ajoitus") or {})


def _tulosta_ajoitus(ajoitus: dict) -> None:
    if not ajoitus:
        return
    kuuntelu = ajoitus.get("kuunteluaika_arvio_ms")
    varoitus = "  <- puhui AI:n päälle?" if isinstance(kuuntelu, int) and kuuntelu < 500 else ""
    print(
        f"           ajoitus: vastauksesta {_ms(ajoitus.get('edellisesta_vastauksesta_ms'))}, "
        f"AI:n puhe ~{_ms(ajoitus.get('ai_puhe_arvio_ms'))}, "
        f"kuunteluaika ~{_ms(kuuntelu)}{varoitus}"
    )


def _tulosta_vuoro(t: float, rivi: dict, erillinen_kuultu: bool) -> None:
    if not erillinen_kuultu:
        kuultu = rivi.get("kuultu")
        if kuultu is not None or rivi.get("vaihe_ennen") not in (None, "ALKU"):
            print(f"{t:7.1f}  HENKILÖ: {kuultu!r}  (varmuus {rivi.get('varmuus')})")
        _tulosta_ajoitus(rivi.get("ajoitus") or {})
    else:
        print(f"{t:7.1f}  << AI vastaa")
    mittaukset = rivi.get("mittaukset") or {}
    if "malli_ms" in mittaukset:
        malli = _ms(mittaukset["malli_ms"])
        print(f"           malli: {malli}  vastasi: {mittaukset.get('malli')!r}")
    print(f"           syyt: {rivi.get('syyt')}")
    lopullinen = f"vuoro valmis {_ms(rivi.get('kesto_ms'))}" + (
        ", välitäytteen jälkeen" if rivi.get("valivastattu") else ""
    )
    print(f"           {lopullinen}")
    keskeytymaton = rivi.get("keskeytymaton") or 0
    for indeksi, lause in enumerate(rivi.get("sanottu") or []):
        merkki = " (ei keskeytettävissä)" if indeksi < keskeytymaton else ""
        print(f"           AI: {lause}{merkki}")
    if not rivi.get("kuuntele"):
        print("           [puhelu lopetetaan]")


def _uusin() -> Path | None:
    lokit = sorted(LOKIKANSIO.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return lokit[-1] if lokit else None


def seuraa() -> None:
    """Odottaa uutta puhelulokia ja tulostaa sen rivit sitä mukaa kuin niitä tulee."""
    edellinen = _uusin()
    print(f"Odotetaan uutta puhelua ({LOKIKANSIO})... Ctrl+C lopettaa.")
    while True:
        uusin = _uusin()
        if uusin is None or uusin == edellinen:
            time.sleep(0.3)
            continue
        edellinen = uusin
        print(f"\n=== Puhelu {uusin.name} ===\n")
        _seuraa_tiedostoa(uusin)


def _seuraa_tiedostoa(polku: Path) -> None:
    """Tulostaa rivit, kunnes puhelu päättyy tai uudempi puhelu alkaa."""
    alku: datetime | None = None
    kesken = ""
    with polku.open(encoding="utf-8") as tiedosto:
        while True:
            kesken += tiedosto.readline()
            if not kesken.endswith("\n"):
                if _uusin() != polku:
                    return
                time.sleep(0.2)
                continue
            tieto = json.loads(kesken)
            kesken = ""
            alku = alku or _aika(tieto)
            _tulosta_rivi(tieto, alku, erillinen_kuultu=True)
            sys.stdout.flush()


def main() -> None:
    if sys.argv[1:] == ["--seuraa"]:
        try:
            seuraa()
        except KeyboardInterrupt:
            pass
        return
    if len(sys.argv) > 1:
        polku = Path(sys.argv[1])
    else:
        lokit = sorted(LOKIKANSIO.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not lokit:
            sys.exit(f"Ei lokeja kansiossa {LOKIKANSIO} (PUHELU_LOKI=true ja DEV_TILA=true?)")
        polku = lokit[-1]
    tulosta(polku)


if __name__ == "__main__":
    main()
