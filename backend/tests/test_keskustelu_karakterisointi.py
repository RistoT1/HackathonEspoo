"""Karakterisointitesti: tilakoneen koko käytös ennallaan rakenteen siistimisen yli.

Käsikirjoitetut keskustelut ajetaan kasittele/aloita-funktioiden läpi feikkimallilla, ja
jokaisesta vuorosta talletetaan mallille lähteneet viestit (tavu tavulta: Ollaman
välimuisti ja mitatut ohjeet), sanottavat, syyt ja kirjanpito, lopuksi tulos (sääntöjen
triage) ja litterointi. Vertailukohta (data/keskustelu_karakterisointi.json) on tehty
ennen rakenteen siistimistä 13.10.

Järjestelmäohjeesta talletetaan tiiviste (sha256): se on sama pitkä teksti joka kutsussa.

Uusi vertailukohta, kun käytös muuttuu TARKOITUKSELLA (tarkista ero ennen committia):
    uv run python -m tests.test_keskustelu_karakterisointi
"""

import asyncio
import hashlib
import json
import random
from pathlib import Path
from unittest.mock import patch

import pytest

from app.mallit import Kysymys, Muutoskonteksti, MuutosTyyppi, Puhelukonteksti
from app.puhelu import keskustelu, tietopohja
from app.puhelu.keskustelu import Puheluistunto, Rajat, kasittele

VERTAILU = Path(__file__).with_name("data") / "keskustelu_karakterisointi.json"
OLETUSVASTAUS = "Selvä. Miten muuten menee?"

UUSI = Kysymys(tunniste="UUSI_LAAKE", teksti="Miten uusi lääke on sopinut?")
VOINTI = Kysymys(tunniste="vointi", teksti="Miten voit tänään?")
LOPETETTU = Kysymys(
    tunniste="LOPETETTU_LAAKE",
    teksti="Oletko lopettanut sen käytön, ja onko vanhoja oireita palannut?",
)
KUURI = Muutoskonteksti(tyyppi=MuutosTyyppi.LYHYT_KUURI, laakeryhma="antibiootti", paivia_sitten=5)
UUSI_MUUTOS = Muutoskonteksti(
    tyyppi=MuutosTyyppi.UUSI_LAAKE, laakeryhma="verenpaine", paivia_sitten=3
)
ANNOS = Muutoskonteksti(tyyppi=MuutosTyyppi.ANNOSMUUTOS, laakeryhma=None, paivia_sitten=2)


class Malli:
    """Feikki-LLM: vastaukset järjestyksessä (None = ei vastausta, Exception = kaatuu)."""

    def __init__(self, vastaukset: list[str | None | Exception]) -> None:
        self.vastaukset = list(vastaukset)
        self.kutsut: list[list[dict[str, str]]] = []

    async def lammita(self, viestit: list[dict[str, str]]) -> None:
        return None

    async def vastaa(self, viestit: list[dict[str, str]]) -> str | None:
        self.kutsut.append([dict(viesti) for viesti in viestit])
        vastaus = self.vastaukset.pop(0) if self.vastaukset else OLETUSVASTAUS
        if isinstance(vastaus, Exception):
            raise vastaus
        return vastaus


def _sk(
    vuorot: list,
    vastaukset: list,
    kysymykset: list[Kysymys] | None = None,
    muutokset: list[Muutoskonteksti] | None = None,
    max_vuorot: int | None = None,
    lammitys_kesken: bool = False,
) -> dict:
    """vuorot: puhe (str/None) tai (puhe, varmuus). Ensimmäinen vuoro on avaus."""
    return {
        "vuorot": [vuoro if isinstance(vuoro, tuple) else (vuoro, 0.9) for vuoro in vuorot],
        "vastaukset": vastaukset,
        "kysymykset": kysymykset if kysymykset is not None else [UUSI, VOINTI],
        "muutokset": muutokset or [],
        "max_vuorot": max_vuorot,
        "lammitys_kesken": lammitys_kesken,
    }


SKENAARIOT: dict[str, dict] = {
    "oire_jatkokysymykset_ja_kotihoito": _sk(
        [
            None,
            "Kuvottaa aamuisin ja on vähän huimannut.",
            "Pari päivää sitten alkoi.",
            "Aamuisin lähinnä.",
            "Kyllä pystyn.",
            "Hyvin on sopinut.",
            "Ihan hyvin.",
        ],
        [
            "Ikävä kuulla. Milloin oireet alkoivat?",
            "Selvä. Onko pahoinvointia enemmän ruokailun jälkeen?",
            "Ymmärrän. Pystytkö syömään ja juomaan normaalisti?",
            "Syö pieniä annoksia kerrallaan ja useammin. Miten uusi lääke on sopinut?",
            "Hyvä kuulla. Miten voit tänään?",
            "Mukava kuulla. Kiitos, että kerroit. LOPETUS",
        ],
        muutokset=[KUURI],
    ),
    "ajoitus_kuurin_jalkeen_ja_unettomuus": _sk(
        [
            None,
            "Väsyttää vähän ja päänsärkyä tuli sen jälkeen kun lopetin.",
            "Ei ole. Tuli nukuttaa vähän heikosti.",
        ],
        ["Ikävä kuulla.", "Ymmärrän."],
        muutokset=[KUURI],
    ),
    "ajoitus_usea_muutostyyppi": _sk(
        [None, "On huimannut vähän tänään.", "Kaaduin eilen kylpyhuoneessa."],
        ["Ikävä kuulla. Milloin huimaus alkoi?", "Voi ei! Satutitko itsesi?"],
        muutokset=[UUSI_MUUTOS, ANNOS],
    ),
    "lopetus_huono_vointi_ja_lista": _sk(
        [None, "No huonosti", "huonosti edelleen", "Ei mitään erityistä.", "Hyvin menee."],
        [
            "Onpa sääli. Mikä vaivaa? LOPETUS",
            "Onpa sääli. Kiitos kun kerroit. LOPETUS",
            "Selvä. LOPETUS",
            "Hyvä. LOPETUS",
        ],
    ),
    "lopetus_oire_kesken": _sk(
        [
            None,
            "Ihan hyvin.",
            "Hyvin.",
            "huonosti, ja on ollut vapinaa käsissä",
            "Aamuisin.",
            "Ei muuta.",
        ],
        [
            "Mukava kuulla. Miten uusi lääke on sopinut?",
            "Hyvä. Miten voit tänään?",
            "Olen pahoillani vapinasta. Kiitos, että kerroit. LOPETUS",
            "Selvä. Milloin se alkoi? LOPETUS",
            "Kiitos. LOPETUS",
        ],
    ),
    "lopetus_henkilo_kysyi": _sk(
        [
            None,
            "Mitä kello on?",
            "Onko tänään tiistai?",
            "No huonosti, mitä kello on?",
            "Paljonko kello on?",
        ],
        [
            "Kello on kymmenen. LOPETUS",
            "On se. Kiitos. LOPETUS",
            "Kymmenen. Kiitos. LOPETUS",
            "Kymmenen. Mikä on olo? LOPETUS",
        ],
    ),
    "lopetus_lista_kesken_toinen_hyvaksytaan": _sk(
        [None, "Ihan hyvin menee.", "Ei sitä nyt kunnolla.", "Ihan hyvin."],
        [
            "Mukava kuulla. Miten uusi lääke on sopinut?",
            "Kiitos, että kerroit tästä. LOPETUS",
            "Selvä. LOPETUS",
        ],
    ),
    "lopetus_lopuillaan_sallitaan": _sk(
        [None, "Ihan hyvin.", "Hyvin on mennyt."],
        ["Mukava kuulla. Miten uusi lääke on sopinut?", "Hyvä kuulla. LOPETUS"],
        max_vuorot=2,
    ),
    "vuororaja": _sk(
        [None, "Ihan hyvin.", "Ei ole."],
        ["Selvä. Onko muuta?"],
        max_vuorot=1,
    ),
    "laakepoikkeama_hiljaisuuden_jalkeen": _sk(
        [
            None,
            "Ihan hyvin.",
            "",
            "No mä oon jatkanut sitä vähän, oon vähän huolissani.",
            "On huimannut vähän tänään.",
            "Ei muuta.",
        ],
        [
            f"Selvä. {LOPETETTU.teksti}",
            "Kiitos, että kerroit. Välitän tämän hoitajalle.",
            "Ikävä kuulla. Milloin huimaus alkoi?",
            "Selvä. LOPETUS",
        ],
        kysymykset=[LOPETETTU, VOINTI],
    ),
    "laakepoikkeama_sallii_lopetuksen": _sk(
        [None, "Ihan hyvin.", "No mulla on niitä ylimääräisenä, niin mä jatkanut vähän."],
        [
            f"Selvä. {LOPETETTU.teksti}",
            "Kiitos, että kerroit. Välitän tämän hoitajalle. LOPETUS",
        ],
        kysymykset=[LOPETETTU, VOINTI],
    ),
    "laakepoikkeama_lisatyn_kysymyksen_jalkeen": _sk(
        [None, "Ihan hyvin.", "Hyvin.", "En oo lopettanut."],
        ["Mukava kuulla. Miten voit tänään?", "Hyvä kuulla.", "Selvä. LOPETUS"],
        kysymykset=[VOINTI, LOPETETTU],
    ),
    "laakepoikkeama_kaksi_kysymysta_kerralla": _sk(
        [None, "Ihan hyvin.", "Jatkanut olen."],
        ["Mukava kuulla. Onko uusi lääke sopinut sinulle hyvin?", "Selvä. LOPETUS"],
        kysymykset=[
            Kysymys(tunniste="UUSI_LAAKE", teksti="Onko uusi lääke sopinut?"),
            Kysymys(tunniste="LOPETETTU_LAAKE", teksti="Onko lääke sopinut, vai oletko jatkanut?"),
        ],
    ),
    "malli_ei_vastaa_ja_kaatuu": _sk(
        [None, "Ihan hyvin.", "jatkanut vaan", "Hyvin.", "Ei muuta."],
        [None, "   ", TimeoutError("malli ei vastannut")],
        kysymykset=[LOPETETTU, VOINTI],
    ),
    "uudelleen_ja_selkeys": _sk(
        [
            None,
            "Kävin kaupassa.",
            "Kävin taas kaupassa.",
            "Hyvin on sopinut.",
            ("mmh huimaa jotain", 0.2),
            ("mmh huimaa jotain", 0.2),
            ("mmh huimaa jotain", 0.2),
            "Mitä kysyit?",
            "Ihan hyvin.",
        ],
        [
            "UUDELLEEN Onko lääke sopinut hyvin, kyllä vai ei?",
            "UUDELLEEN: Onko lääke sopinut, kyllä vai ei?",
            "Hyvä kuulla. Miten voit tänään?",
            "Selvä, siirrytään eteenpäin. Miten voit tänään?",
        ],
    ),
    "tukitila_itsetuho": _sk(
        [
            None,
            "En halua enää elää.",
            "Olen tosi huolissani kaikesta.",
            "En tiedä.",
            "",
            "Olen kotona.",
            "joo olen turvassa",
        ],
        ["LOPETUS", None, "Olen tässä. Oletko nyt turvassa?", "Hyvä kuulla. LOPETUS"],
    ),
    "vakava_huoli": _sk(
        [None, "setä kosketti mua luvatta", "En tiedä mitä tehdä.", "Olen kotona yksin."],
        ["Kiitos, että kerroit. Oletko nyt turvassa?", "LOPETUS"],
    ),
    "varmistuskysymys": _sk(
        [None, "ei ole ollut itsetuhoisia ajatuksia", "en tiedä", "Olen tässä."],
        ["Olen tässä kanssasi. Oletko turvassa?"],
    ),
    "mallin_itsetuhokysymys": _sk(
        [None, "Vähän raskasta on ollut.", "joo vähän"],
        ["Kuulostaa raskaalta. Onko sinulla ollut ajatuksia vahingoittaa itseäsi?"],
    ),
    "lopetuspyynto_huolen_jalkeen": _sk(
        [None, "Vähän on huimannut aamuisin.", "Pitää nyt mennä."],
        ["Ikävä kuulla. Milloin se alkoi?"],
    ),
    "lopetuspyynto_itsetuhon_jalkeen": _sk(
        [None, "en halua enää elää", "lopetetaan tämä puhelu", "joo olen turvassa"],
        [],
    ),
    "kieltaytyminen_avauksen_jalkeen": _sk([None, "en ehdi nyt"], []),
    "hiljaisuus": _sk([None, "", None], []),
    "lammitys_kesken_varatoiminta": _sk(
        [None, "Ihan hyvin.", "Hyvin on sopinut.", "En halua enää elää.", "En tiedä."],
        ["Olen tässä."],
        lammitys_kesken=True,
    ),
    "saannot_ennen_mallia": _sk(
        [
            None,
            "Mikä lääke se on?",
            "Pitäisikö minun lopettaa se uusi lääke?",
            "tyhmä kone",
            "aikamoista ripulia kun vittu turpa kiinni",
            "turpa kiinni",
            "Haluan puhua hoitajan kanssa.",
        ],
        ["Ikävä kuulla ripulista. Montako päivää sitä on ollut?"],
    ),
    "loukkaus_kolmas_lopettaa": _sk([None, "tyhmä kone", "turpa kiinni", "haista vittuun"], []),
    "hata_kaatunut_lattialla": _sk(
        [None, "Kaaduin äsken keittiössä.", "En pääse ylös."],
        ["Voi ei! Satutitko itsesi?"],
    ),
    "hata_rintakipu": _sk([None, "Rintaan sattuu."], []),
    "pitka_huoli_vaihtelu_ja_historia": _sk(
        [
            None,
            "Olen vähän huolissani tästä lääkkeestä.",
            "No kun se tuntuu oudolta.",
            "Ei muuta oikeastaan.",
            "Vähän tämä kaikki pelottaa.",
            "Vähän tämä kaikki pelottaa.",
            "Vähän tämä kaikki pelottaa.",
            "Vähän tämä kaikki pelottaa.",
            "Ihan hyvin muuten.",
            "Hyvin on sopinut.",
            "Vähän väsyttää.",
            "Iltaisin lähinnä.",
            "Ei muuta.",
            "Ihan hyvin.",
        ],
        [
            "Ymmärrän, että se huolettaa. Kerrotko lisää?",
            "Ymmärrän. Miltä se tuntuu?",
            "Ymmärrän. Haluatko kertoa vielä?",
            "Ymmärrän, että pelottaa. Mikä pelottaa eniten?",
            "Ymmärrän, että pelottaa. Mikä pelottaa eniten?",
            "Ymmärrän, että pelottaa. Mikä pelottaa eniten?",
            "Ymmärrän, että pelottaa. Mikä pelottaa eniten?",
            "Kuulostaa siltä, että tämä pelottaa sinua. Voitko kertoa lisää? Välitän tämän "
            "asian hoitajalle.\n\nTietojemme mukaan sinulle on merkitty uusi lääke. "
            "Oletko aloittanut sen käytön?",
            "Soita numeroon 992-200. Miten muuten menee?",
            "Ikävä kuulla. Milloin väsymys alkoi?",
            "Selvä. Haittaako se arkea?",
            "Lepää riittävästi. Miten voit tänään?",
            "Hyvä kuulla. LOPETUS",
        ],
        max_vuorot=30,
    ),
}


def _istunto(skenaario: dict) -> Puheluistunto:
    istunto = Puheluistunto(
        istunto_id="i-1",
        tehtava_id="t-1",
        kysymykset=list(skenaario["kysymykset"]),
        konteksti=Puhelukonteksti(
            ika=82, aktiivisia_laakkeita=3, muutokset=list(skenaario["muutokset"])
        ),
        paivita_jono=False,
    )
    if skenaario["max_vuorot"] is not None:
        istunto.rajat = Rajat(max_vuorot=skenaario["max_vuorot"])
    if skenaario["lammitys_kesken"]:
        istunto.lammitetty = True
    return istunto


def _tiivistetty(viestit: list[dict[str, str]]) -> list[dict[str, str]]:
    """Järjestelmäohje tiivisteeksi, muut viestit sellaisinaan."""
    tulos = []
    for viesti in viestit:
        sisalto = viesti["content"]
        if viesti["role"] == "system":
            sisalto = "sha256:" + hashlib.sha256(sisalto.encode()).hexdigest()
        tulos.append({**viesti, "content": sisalto})
    return tulos


async def _keskustele(skenaario: dict) -> dict:
    istunto = _istunto(skenaario)
    malli = Malli(skenaario["vastaukset"])
    tulos: dict = {"lammitysviestit": _tiivistetty(istunto.lammitysviestit()), "vuorot": []}
    for puhe, varmuus in skenaario["vuorot"]:
        kutsuja = len(malli.kutsut)
        vuoro = await kasittele(istunto, puhe, varmuus, malli)
        tulos["vuorot"].append(
            {
                "puhe": puhe,
                "viestit": [_tiivistetty(kutsu) for kutsu in malli.kutsut[kutsuja:]],
                "sanottavat": vuoro.sanottavat,
                "kuuntele": vuoro.kuuntele,
                "keskeytymaton": vuoro.keskeytymaton,
                "syyt": list(istunto.syyt),
                "malli": istunto.mittaukset.get("malli"),
                "seuraava": istunto.seuraava,
                "vaihe": istunto.vaihe.value,
            }
        )
    tulos["tulos"] = istunto.tulos().model_dump(mode="json", exclude={"paattyi"})
    tulos["litterointi"] = istunto.litterointi()
    return tulos


def aja(nimi: str) -> dict:
    """Repliikkien versiot arvotaan siemenellä: myös arvontojen järjestys pysyy samana."""
    with (
        patch.object(tietopohja, "_ARPA", random.Random(nimi)),
        patch.object(keskustelu, "LAMMITYKSEN_ODOTUS_S", 0),
    ):
        return json.loads(json.dumps(asyncio.run(_keskustele(SKENAARIOT[nimi]))))


@pytest.mark.parametrize("nimi", list(SKENAARIOT))
def test_keskustelu_ennallaan(nimi: str) -> None:
    vertailu = json.loads(VERTAILU.read_text(encoding="utf-8"))

    tulos = aja(nimi)

    assert tulos == vertailu[nimi]


def test_vertailukohdassa_kaikki_skenaariot() -> None:
    vertailu = json.loads(VERTAILU.read_text(encoding="utf-8"))

    assert sorted(vertailu) == sorted(SKENAARIOT)


if __name__ == "__main__":
    kaikki = {nimi: aja(nimi) for nimi in SKENAARIOT}
    VERTAILU.parent.mkdir(exist_ok=True)
    VERTAILU.write_text(json.dumps(kaikki, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Kirjoitettu {VERTAILU} ({len(kaikki)} skenaariota)")
