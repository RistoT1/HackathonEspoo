"""VAIN DEV: testipankin generointi ja ajo oikeaa mallia vasten (.env:n LLM_MALLI).

Kaytto (backend/):
    uv run python -m arviointi.aja generoi --persoonia 400          # vaatii kannan
    uv run python -m arviointi.aja keskustelut --maara 50           # persoonat 1-50
    uv run python -m arviointi.aja keskustelut --maara 50 --alku 50 # seuraavat 50
    uv run python -m arviointi.aja yksittaiset --maara 300 --sekoita
    uv run python -m arviointi.aja raportti <ajo>                   # raportti uudelleen
Jatkaa keskeytettyä ajoa: --ajo <nimi> (valmiit ohitetaan). Tulokset ja raportti:
lokit/testipankki/<ajo>/ (gitignore). Hidas: mlx-ajurilla jokainen keskustelu käsittelee
järjestelmäohjeen alusta (~45 s), llama.cpp-ajurilla yhteinen alku tulee välimuistista.
"""

import argparse
import asyncio
import json
import random
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.mallit import Kysymys, Muutoskonteksti, MuutosTyyppi, Puhelukonteksti
from app.puhelu.agentti import hae_agentti
from app.puhelu.keskustelu import Puheluistunto, kasittele
from arviointi.pankki import generoi, sisalto_kannasta
from arviointi.simulaattori import Simulaatiotila, vastaa
from arviointi.tarkistus import tarkista_keskustelu, tarkista_yksittainen

PANKKI = Path(__file__).with_name("pankki.json")
TULOKSET = Path(__file__).resolve().parents[2] / "lokit" / "testipankki"
MAX_VUOROT = 16


def _istunto(
    tunniste: str, ika: int, muutos: str, ryhma: str, kysymykset: list[str]
) -> Puheluistunto:
    return Puheluistunto(
        istunto_id=tunniste,
        tehtava_id="testipankki",
        kysymykset=[Kysymys(tunniste=f"k{n}", teksti=t) for n, t in enumerate(kysymykset)],
        konteksti=Puhelukonteksti(
            ika=ika,
            aktiivisia_laakkeita=2,
            muutokset=[
                Muutoskonteksti(tyyppi=MuutosTyyppi(muutos), laakeryhma=ryhma, paivia_sitten=5)
            ],
        ),
        paivita_jono=False,
    )


async def aja_keskustelu(persoona: dict, agentti) -> dict:
    istunto = _istunto(
        persoona["id"],
        persoona["ika"],
        persoona["muutos"],
        persoona["ryhma"],
        persoona["kysymykset"],
    )
    alku = time.perf_counter()
    await agentti.lammita(istunto.lammitysviestit())
    istunto.lammitetty = istunto.malli_lammin = True
    avaus = await kasittele(istunto, None, None, agentti)
    vuorot = [
        {
            "henkilo": None,
            "botti": " ".join(avaus.sanottavat),
            "raaka": None,
            "syyt": list(istunto.syyt),
            "kuuntele": True,
        }
    ]
    tila = Simulaatiotila(rng=random.Random(persoona["id"]))
    kuuntele = True
    while kuuntele and len(vuorot) <= MAX_VUOROT:
        henkilo = vastaa(persoona, tila, vuorot[-1]["botti"])
        vuoro = await kasittele(istunto, henkilo, 0.9, agentti)
        kuuntele = vuoro.kuuntele
        vuorot.append(
            {
                "henkilo": henkilo,
                "botti": " ".join(vuoro.sanottavat),
                "raaka": istunto.mittaukset.get("malli"),
                "malli_ms": istunto.mittaukset.get("malli_ms"),
                "syyt": list(istunto.syyt),
                "kuuntele": kuuntele,
            }
        )
    lista_kayty = istunto.seuraava >= len(istunto.kysymykset)
    taso = istunto.tulos().yhteenveto.taso.value
    loppu = istunto.loppu.value if istunto.loppu else None
    tarkistukset = tarkista_keskustelu(persoona, vuorot, loppu, lista_kayty, taso, MAX_VUOROT)
    return {
        "id": persoona["id"],
        "laji": "keskustelu",
        "tyyli": persoona["tyyli"],
        "tapahtuma": persoona["tapahtuma"]["tyyppi"] if persoona["tapahtuma"] else "ei",
        "oireita": len(persoona["oireet"]),
        "loppu": loppu,
        "taso": taso,
        "kesto_s": round(time.perf_counter() - alku, 1),
        "tarkistukset": tarkistukset,
        "vuorot": vuorot,
    }


async def aja_yksittainen(kohde: dict, agentti) -> dict:
    kysymykset = [kohde["kysymys"]]
    if kohde["kysymysaihe"] != "vointi":
        kysymykset.append("Miten voit tänään?")
    istunto = _istunto(kohde["id"], 82, "UUSI_LAAKE", "verenpaine ja sydän", kysymykset)
    alku = time.perf_counter()
    await agentti.lammita(istunto.lammitysviestit())
    istunto.lammitetty = istunto.malli_lammin = True
    await kasittele(istunto, None, None, agentti)
    # Botti on kysynyt kysymyksen (sama alku kaikille saman kysymyksen kohteille: välimuisti).
    istunto.historia += [
        {"role": "user", "content": "Ihan hyvin."},
        {"role": "assistant", "content": f"Mukava kuulla. {kohde['kysymys']}"},
    ]
    istunto.seuraava = 1
    # Lääkemuutoksen poikkeaman tunnistus lukee juuri kysytyn kysymyksen tästä.
    istunto.viimeksi_kysytty = istunto.kysymykset[0]
    istunto.vuoro = 1
    vuoro = await kasittele(istunto, kohde["vastaus"], 0.9, agentti)
    botti = " ".join(vuoro.sanottavat)
    raaka = istunto.mittaukset.get("malli")
    loppu = istunto.loppu.value if istunto.loppu else None
    tarkistukset = tarkista_yksittainen(kohde, botti, istunto.syyt, raaka, loppu, istunto.tukitila)
    return {
        "id": kohde["id"],
        "laji": "yksittainen",
        "tyyli": kohde["tyyli"],
        "tapahtuma": kohde["tyyppi"],
        "kysymysaihe": kohde["kysymysaihe"],
        "kesto_s": round(time.perf_counter() - alku, 1),
        "tarkistukset": tarkistukset,
        "vuorot": [
            {"henkilo": None, "botti": kohde["kysymys"]},
            {"henkilo": kohde["vastaus"], "botti": botti, "raaka": raaka, "syyt": istunto.syyt},
        ],
    }


def _lue_tulokset(polku: Path) -> list[dict]:
    if not polku.exists():
        return []
    return [json.loads(rivi) for rivi in polku.read_text(encoding="utf-8").splitlines() if rivi]


def raportti(kansio: Path) -> str:
    tulokset = _lue_tulokset(kansio / "tulokset.jsonl")
    tarkistuksittain: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    ryhmittain: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for tulos in tulokset:
        for nimi, lapi, _ in tulos["tarkistukset"]:
            tarkistuksittain[nimi][0] += bool(lapi)
            tarkistuksittain[nimi][1] += 1
            for ryhma in (f"tyyli:{tulos['tyyli']}", f"tapahtuma:{tulos['tapahtuma']}"):
                ryhmittain[ryhma][0] += bool(lapi)
                ryhmittain[ryhma][1] += 1
    kestot = [t["kesto_s"] for t in tulokset]
    rivit = [
        f"# Testipankki {kansio.name}",
        f"{len(tulokset)} ajoa, kesto yhteensä {sum(kestot) / 60:.0f} min "
        f"(ka {sum(kestot) / max(len(kestot), 1):.0f} s/ajo)",
        "",
        "| tarkistus | läpi | % |",
        "|---|---|---|",
    ]
    for nimi, (lapi, yht) in sorted(tarkistuksittain.items(), key=lambda x: x[1][0] / x[1][1]):
        rivit.append(f"| {nimi} | {lapi}/{yht} | {100 * lapi / yht:.0f} |")
    rivit += ["", "| ryhmä | läpi | % |", "|---|---|---|"]
    for nimi, (lapi, yht) in sorted(ryhmittain.items(), key=lambda x: x[1][0] / x[1][1]):
        rivit.append(f"| {nimi} | {lapi}/{yht} | {100 * lapi / yht:.0f} |")
    rivit += ["", "## Hylätyt (litteroinnit)"]
    for tulos in tulokset:
        hylatyt = [(n, s) for n, lapi, s in tulos["tarkistukset"] if not lapi]
        if not hylatyt:
            continue
        rivit.append(f"\n### {tulos['id']} ({tulos['tyyli']}, {tulos['tapahtuma']})")
        rivit += [f"- **{nimi}**: {str(selitys)[:300]}" for nimi, selitys in hylatyt]
        rivit.append("```")
        for vuoro in tulos["vuorot"]:
            if vuoro.get("henkilo"):
                rivit.append(f"H:  {vuoro['henkilo']}")
            rivit.append(f"AI: {vuoro['botti']}   {vuoro.get('syyt') or ''}")
        rivit.append("```")
    return "\n".join(rivit)


async def _aja(laji: str, kohteet: list[dict], kansio: Path) -> None:
    asetukset = Settings()
    if not asetukset.llm_malli:
        raise SystemExit("LLM_MALLI puuttuu .env:stä")
    agentti = hae_agentti(asetukset)
    kansio.mkdir(parents=True, exist_ok=True)
    tiedosto = kansio / "tulokset.jsonl"
    valmiit = {t["id"] for t in _lue_tulokset(tiedosto)}
    ajettavat = [k for k in kohteet if k["id"] not in valmiit]
    print(f"{laji}: {len(ajettavat)} ajettavaa ({len(valmiit)} valmiina) -> {kansio}", flush=True)
    for numero, kohde in enumerate(ajettavat, 1):
        if laji == "keskustelut":
            tulos = await aja_keskustelu(kohde, agentti)
        else:
            tulos = await aja_yksittainen(kohde, agentti)
        with tiedosto.open("a", encoding="utf-8") as ulos:
            ulos.write(json.dumps(tulos, ensure_ascii=False) + "\n")
        hylatyt = [n for n, lapi, _ in tulos["tarkistukset"] if not lapi]
        print(
            f"[{numero}/{len(ajettavat)}] {kohde['id']} {tulos['kesto_s']} s "
            f"{'OK' if not hylatyt else 'HYLÄTTY: ' + ', '.join(hylatyt)}",
            flush=True,
        )
    (kansio / "raportti.md").write_text(raportti(kansio), encoding="utf-8")
    print(f"Raportti: {kansio / 'raportti.md'}")


def main() -> None:
    jasentaja = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    alikomennot = jasentaja.add_subparsers(dest="komento", required=True)
    gen = alikomennot.add_parser("generoi")
    gen.add_argument("--persoonia", type=int, default=400)
    gen.add_argument("--siemen", type=int, default=2026)
    for nimi in ("keskustelut", "yksittaiset"):
        ali = alikomennot.add_parser(nimi)
        ali.add_argument("--maara", type=int, default=20)
        ali.add_argument("--alku", type=int, default=0)
        ali.add_argument("--sekoita", action="store_true", help="satunnainen otos (siemen 1)")
        ali.add_argument("--ajo", default=None, help="jatka tätä ajoa (kansion nimi)")
        ali.add_argument("--tapahtuma", default=None, help="vain tämä tapahtuma/vastaustyyppi")
        ali.add_argument("--idt", default=None, help="vain nämä tunnisteet pilkuilla (p0131,p0195)")
    rap = alikomennot.add_parser("raportti")
    rap.add_argument("ajo")
    argumentit = jasentaja.parse_args()

    if argumentit.komento == "generoi":
        pankki = generoi(argumentit.persoonia, argumentit.siemen, sisalto_kannasta())
        PANKKI.write_text(json.dumps(pankki, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            f"{len(pankki['persoonat'])} keskustelupohjaa, {len(pankki['yksittaiset'])} "
            f"yksittäistä vastausta -> {PANKKI}"
        )
        return
    if argumentit.komento == "raportti":
        kansio = TULOKSET / argumentit.ajo
        (kansio / "raportti.md").write_text(raportti(kansio), encoding="utf-8")
        print(raportti(kansio).split("## Hylätyt")[0])
        return

    pankki = json.loads(PANKKI.read_text(encoding="utf-8"))
    kohteet = pankki["persoonat" if argumentit.komento == "keskustelut" else "yksittaiset"]
    if argumentit.tapahtuma:
        kohteet = [
            k
            for k in kohteet
            if (k.get("tapahtuma") or {}).get("tyyppi", k.get("tyyppi")) == argumentit.tapahtuma
            or k.get("tyyppi") == argumentit.tapahtuma
        ]
    if argumentit.idt:
        halutut = set(argumentit.idt.split(","))
        kohteet = [k for k in kohteet if k["id"] in halutut]
    if argumentit.sekoita:
        kohteet = random.Random(1).sample(kohteet, len(kohteet))
    kohteet = kohteet[argumentit.alku : argumentit.alku + argumentit.maara]
    ajo = argumentit.ajo or f"{datetime.now():%Y%m%d_%H%M}_{argumentit.komento}"
    asyncio.run(_aja(argumentit.komento, kohteet, TULOKSET / ajo))


if __name__ == "__main__":
    main()
