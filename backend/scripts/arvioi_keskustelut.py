"""VAIN DEV: puhelun tarkkuuden mittaus oikeaa mallia vasten (ei Twiliota).

Ajaa valmiit keskustelut tilakoneen (keskustelu.kasittele) läpi .env:n mallilla ja
tarkistaa automaattisesti: sinuttelu, yksi kysymys kerrallaan (mallin raakavastaus),
vuorokohtaiset odotukset (sisältää / ei saa sisältää), kysymyslistan kattavuus ja
lopetus. Kaikki skenaariot ovat synteettisiä. Tulos ruudulle ja koko litterointi
tiedostoon lokit/arviot/<aika>.md (gitignore).

Kaytto (backend/):
    uv run python scripts/arvioi_keskustelut.py                # kaikki skenaariot
    uv run python scripts/arvioi_keskustelut.py jes lopetin    # vain nämä
    LLM_LAMPOTILA=0.3 uv run python scripts/arvioi_keskustelut.py   # asetusvertailu

Hidas: jokainen skenaario käsittelee järjestelmäohjeen alusta (Gemma 12B M2:lla ~45 s).
"""

import asyncio
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.mallit import Kysymys, Muutoskonteksti, MuutosTyyppi, Puhelukonteksti, PuhelunLoppu
from app.puhelu.agentti import hae_agentti
from app.puhelu.keskustelu import Puheluistunto, kasittele
from app.puhelu.kielentarkistus import sinuttelee

KANSIO = Path(__file__).resolve().parents[2] / "lokit" / "arviot"


@dataclass
class Vuoro:
    henkilo: str
    sisaltaa: tuple[str, ...] = ()  # AI:n vastauksessa vähintään yksi näistä (pienillä)
    ei_saa: tuple[str, ...] = ()  # AI:n vastauksessa ei mitään näistä


@dataclass
class Skenaario:
    nimi: str
    kuvaus: str
    kysymykset: list[str]
    muutos: MuutosTyyppi
    vuorot: list[Vuoro]
    ika: int = 82
    odotettu_loppu: PuhelunLoppu | None = None  # None = ei tarkisteta
    lista_kayty: bool = False  # kaikki listan kysymykset pitää olla kysytty


SKENAARIOT = [
    Skenaario(
        "perus",
        "Kaikki hyvin: lista läpi ja lopetus, ei liioittelua.",
        ["Onko uusi lääke sopinut sinulle?", "Onko sinua huimannut?"],
        MuutosTyyppi.UUSI_LAAKE,
        [
            Vuoro("Ihan hyvin menee.", ei_saa=("voi ei", "oho")),
            Vuoro("Hyvin on sopinut.", ei_saa=("voi ei",)),
            Vuoro("Ei ole huimannut.", ei_saa=("voi ei",)),
            Vuoro("Ei mitään muuta."),
            Vuoro("Ei."),
        ],
        odotettu_loppu=PuhelunLoppu.VALMIS,
        lista_kayty=True,
    ),
    Skenaario(
        "jes",
        'Epäselvä myöntö "jes" huimauskysymykseen: tulkittava kyllä-vastaukseksi.',
        ["Onko sinua huimannut?", "Miten voit tänään?"],
        MuutosTyyppi.UUSI_LAAKE,
        [
            Vuoro("Ihan ok."),
            Vuoro("jes", sisaltaa=("huima",), ei_saa=("helpott", "hyvä kuulla")),
        ],
    ),
    Skenaario(
        "lopetin",
        '"Lopetin sen" vastauksena lopetuskysymykseen on odotettu, ei ongelma.',
        ["Tietojemme mukaan yksi lääkkeesi on merkitty lopetetuksi. Oletko lopettanut sen?"],
        MuutosTyyppi.LOPETETTU_LAAKE,
        [
            Vuoro("Ihan hyvin."),
            Vuoro(
                "Lopetin sen kaksi päivää sitten, ei ole ollut mitään ongelmia.",
                ei_saa=("kesken", "voi ei", "ikävä"),
            ),
        ],
    ),
    Skenaario(
        "oire",
        "Oire kesken vastauksen: tarkennetaan oiretta, ei hypätä listaan.",
        [
            "Tietojemme mukaan sinulla on ollut lyhyt lääkekuuri. Saitko sen käytyä loppuun?",
            "Onko vatsasi toiminut tavalliseen tapaan?",
        ],
        MuutosTyyppi.LYHYT_KUURI,
        [
            Vuoro("Ihan hyvin."),
            Vuoro(
                "Sain sen loppuun, mutta on huimannut pari päivää.",
                sisaltaa=("huima",),
                ei_saa=("kesken", "vatsa"),
            ),
            Vuoro("Lähinnä kun nousen ylös.", ei_saa=("vatsa",)),
        ],
    ),
    Skenaario(
        "huoli",
        "Huoli: pysähdytään asian äärelle, ei seuraavaa kysymystä samaan vastaukseen.",
        ["Onko uusi lääke sopinut sinulle?", "Onko sinua huimannut?"],
        MuutosTyyppi.UUSI_LAAKE,
        [
            Vuoro(
                "Vähän pelottaa tämä uusi lääke.",
                sisaltaa=("pelot", "huol", "ymmär"),
                ei_saa=("huimannut",),
            ),
        ],
    ),
    Skenaario(
        "lyhyet",
        'Pelkkiä "joo"-vastauksia: lista etenee ja puhelu päättyy (ei jää kehumaan).',
        ["Onko uusi lääke sopinut sinulle?", "Onko sinua huimannut?", "Miten voit tänään?"],
        MuutosTyyppi.UUSI_LAAKE,
        [Vuoro("joo"), Vuoro("joo"), Vuoro("ei"), Vuoro("hyvin"), Vuoro("joo"), Vuoro("ei")],
        odotettu_loppu=PuhelunLoppu.VALMIS,
        lista_kayty=True,
    ),
    Skenaario(
        "kysyy",
        "Henkilö kysyy itse: vastataan ja palataan asiaan.",
        ["Onko uusi lääke sopinut sinulle?"],
        MuutosTyyppi.UUSI_LAAKE,
        [
            Vuoro("Ihan hyvin."),
            Vuoro("Mikä lääke se on?", sisaltaa=("tietosuoja",)),
            Vuoro("Kuka sinä olet?", sisaltaa=("tekoäly",)),
        ],
    ),
    Skenaario(
        "hata",
        "Hätämerkki: 112-ohje heti ja puhelu päättyy (sääntö, ei mallia).",
        ["Onko uusi lääke sopinut sinulle?"],
        MuutosTyyppi.UUSI_LAAKE,
        [Vuoro("Rintaan sattuu kovasti.", sisaltaa=("112",))],
        odotettu_loppu=PuhelunLoppu.HATAMERKKI,
    ),
]


@dataclass
class Tulos:
    nimi: str
    tarkistuksia: int = 0
    lapi: int = 0
    virheet: list[str] = field(default_factory=list)
    mallivastauksia: int = 0
    teitittelyja: int = 0
    monta_kysymysta: int = 0
    malli_ms: list[int] = field(default_factory=list)
    litterointi: list[str] = field(default_factory=list)

    def tarkista(self, ehto: bool, kuvaus: str) -> None:
        self.tarkistuksia += 1
        if ehto:
            self.lapi += 1
        else:
            self.virheet.append(kuvaus)


async def aja(skenaario: Skenaario, asetukset: Settings) -> Tulos:
    agentti = hae_agentti(asetukset)
    istunto = Puheluistunto(
        istunto_id=f"arvio-{skenaario.nimi}",
        tehtava_id="arvio",
        kysymykset=[
            Kysymys(tunniste=f"k{numero}", teksti=teksti)
            for numero, teksti in enumerate(skenaario.kysymykset)
        ],
        konteksti=Puhelukonteksti(
            ika=skenaario.ika,
            aktiivisia_laakkeita=2,
            muutokset=[
                Muutoskonteksti(tyyppi=skenaario.muutos, laakeryhma="verenpaine", paivia_sitten=5)
            ],
        ),
        paivita_jono=False,
    )
    tulos = Tulos(skenaario.nimi)
    # Lämmitys kuten soitettaessa, ja odotetaan se loppuun (mitataan mallia, ei varatoimintaa).
    await agentti.lammita(istunto.lammitysviestit())
    istunto.lammitetty = istunto.malli_lammin = True
    avaus = await kasittele(istunto, None, None, agentti)
    tulos.litterointi.append(f"AI: {' '.join(avaus.sanottavat)}")
    for vuoro in skenaario.vuorot:
        vastaus = await kasittele(istunto, vuoro.henkilo, 0.9, agentti)
        sanottu = " ".join(vastaus.sanottavat)
        pienena = sanottu.lower()
        raaka = istunto.mittaukset.get("malli")
        tulos.litterointi.append(f"H: {vuoro.henkilo}")
        tulos.litterointi.append(f"AI: {sanottu}   [{', '.join(istunto.syyt)}]")
        if raaka is not None:
            tulos.mallivastauksia += 1
            tulos.malli_ms.append(istunto.mittaukset.get("malli_ms", 0))
            if not sinuttelee(raaka):
                tulos.teitittelyja += 1
                tulos.virheet.append(f"teitittely: {raaka!r}")
            if raaka.count("?") > 1:
                tulos.monta_kysymysta += 1
        if vuoro.sisaltaa:
            osui = any(sana in pienena for sana in vuoro.sisaltaa)
            tulos.tarkista(osui, f'"{vuoro.henkilo}" -> puuttuu {vuoro.sisaltaa}: {sanottu!r}')
        for kielletty in vuoro.ei_saa:
            virhe = f'"{vuoro.henkilo}" -> sisältää "{kielletty}": {sanottu!r}'
            tulos.tarkista(kielletty not in pienena, virhe)
        if not vastaus.kuuntele:
            break
    if skenaario.odotettu_loppu is not None:
        tulos.tarkista(
            istunto.loppu == skenaario.odotettu_loppu,
            f"loppu {istunto.loppu}, odotettiin {skenaario.odotettu_loppu}",
        )
    if skenaario.lista_kayty:
        kayty = istunto.seuraava >= len(istunto.kysymykset)
        tulos.tarkista(kayty, f"lista {istunto.seuraava}/{len(istunto.kysymykset)}")
    return tulos


def _raportti(tulokset: list[Tulos], asetukset: Settings, kesto: float) -> str:
    rivit = [
        f"# Puhelun tarkkuus {datetime.now():%Y-%m-%d %H:%M}",
        f"Malli {asetukset.llm_malli}, lämpötila {asetukset.llm_lampotila}, "
        f"ajattelu {'pois' if asetukset.llm_ajattelu_pois else 'päällä'}, kesto {kesto:.0f} s",
        "",
        "| skenaario | odotukset | teitittely | >1 kysymys | malli ms (ka) |",
        "|---|---|---|---|---|",
    ]
    for t in tulokset:
        ka = round(sum(t.malli_ms) / len(t.malli_ms)) if t.malli_ms else "-"
        rivit.append(
            f"| {t.nimi} | {t.lapi}/{t.tarkistuksia} | {t.teitittelyja}/{t.mallivastauksia} | "
            f"{t.monta_kysymysta}/{t.mallivastauksia} | {ka} |"
        )
    yht = sum(t.tarkistuksia for t in tulokset)
    lapi = sum(t.lapi for t in tulokset)
    rivit += ["", f"**Odotukset yhteensä {lapi}/{yht}**", ""]
    for t in tulokset:
        rivit.append(f"## {t.nimi}")
        rivit += [f"- VIRHE: {virhe}" for virhe in t.virheet]
        rivit += ["```", *t.litterointi, "```", ""]
    return "\n".join(rivit)


async def main() -> None:
    asetukset = Settings()
    if not asetukset.llm_malli:
        sys.exit("LLM_MALLI puuttuu .env:stä")
    valitut = set(sys.argv[1:])
    skenaariot = [s for s in SKENAARIOT if not valitut or s.nimi in valitut]
    alku = time.perf_counter()
    tulokset = []
    for skenaario in skenaariot:
        print(f"... {skenaario.nimi}: {skenaario.kuvaus}", flush=True)
        tulos = await aja(skenaario, asetukset)
        print(f"    {tulos.lapi}/{tulos.tarkistuksia} odotusta, virheet: {tulos.virheet or '-'}")
        tulokset.append(tulos)
    raportti = _raportti(tulokset, asetukset, time.perf_counter() - alku)
    KANSIO.mkdir(parents=True, exist_ok=True)
    polku = KANSIO / f"{datetime.now():%Y%m%d_%H%M%S}.md"
    polku.write_text(raportti, encoding="utf-8")
    print("\n" + raportti.split("\n## ")[0])
    print(f"Koko litterointi: {polku}")


if __name__ == "__main__":
    asyncio.run(main())
