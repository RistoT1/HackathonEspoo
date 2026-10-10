"""Puhelun tilakone ilman Twiliota: lupa, kysymykset, tarkennus, hiljaisuus, hata, agentin vika."""

import asyncio

from app.mallit import Kysymys, Puhelukonteksti, PuhelunLoppu
from app.puhelu.agentti import KiinteaAgentti, Reaktio
from app.puhelu.keskustelu import (
    EI_KUULUNUT,
    EI_NYT,
    HATAOHJE,
    HOITAJALLE,
    LUPAKYSYMYS,
    SELVENNYSPYYNTO,
    Puheluistunto,
    kaiun_vuoro,
    kasittele,
)
from app.puhelu.puhe import AVAUS, LOPETUS

KYSYMYKSET = [
    Kysymys(tunniste="UUSI_LAAKE", teksti="Miten uusi lääke on sopinut?"),
    Kysymys(tunniste="vointi", teksti="Miten voit tänään?"),
]


def istunto() -> Puheluistunto:
    return Puheluistunto(
        istunto_id="i-1",
        tehtava_id="t-1",
        kysymykset=list(KYSYMYKSET),
        konteksti=Puhelukonteksti(ika=80, aktiivisia_laakkeita=3, muutokset=[]),
        paivita_jono=False,
    )


def vuoro(tila: Puheluistunto, puhe: str | None, agentti=None, varmuus: float | None = None):
    return asyncio.run(kasittele(tila, puhe, varmuus, agentti or KiinteaAgentti()))


class TarkentavaAgentti:
    """Feikki-LLM: muotoilee kysymyksen omalla tavallaan ja tarkentaa kerran."""

    def __init__(self) -> None:
        self.reaktiot: list[bool] = []

    async def muotoile_kysymys(self, kysymys, konteksti, historia) -> str:
        return f"LLM: {kysymys.teksti}"

    async def reagoi(self, kysymys, vastaus, konteksti, historia, jatkokysymys_sallittu):
        self.reaktiot.append(jatkokysymys_sallittu)
        if jatkokysymys_sallittu:
            return Reaktio(kuittaus="Ymmärrän.", jatkokysymys="Milloin se alkoi?")
        return Reaktio(kuittaus="Kiitos tarkennuksesta.")


class KaatuvaAgentti:
    async def muotoile_kysymys(self, kysymys, konteksti, historia) -> str:
        raise TimeoutError("malli ei vastannut")

    async def reagoi(self, kysymys, vastaus, konteksti, historia, jatkokysymys_sallittu):
        raise TimeoutError("malli ei vastannut")


def test_avaus_kertoo_tekoalysta_ja_kysyy_luvan():
    tila = istunto()

    avaus = vuoro(tila, None)

    assert avaus.sanottavat == [AVAUS, LUPAKYSYMYS]
    assert avaus.kuuntele


def test_kiintea_agentti_kay_kysymykset_ja_lopettaa():
    tila = istunto()
    vuoro(tila, None)

    eka = vuoro(tila, "Kyllä on.")
    toka = vuoro(tila, "Hyvin on sopinut.", varmuus=0.8)
    loppu = vuoro(tila, "Ihan hyvin.")

    assert eka.sanottavat == ["Kiitos.", KYSYMYKSET[0].teksti]
    assert toka.sanottavat == ["Kiitos.", KYSYMYKSET[1].teksti]
    assert loppu.sanottavat[-1] == LOPETUS
    assert not loppu.kuuntele
    assert tila.loppu == PuhelunLoppu.VALMIS
    assert [v.vastaus for v in tila.vastaukset] == ["Hyvin on sopinut.", "Ihan hyvin."]
    assert tila.vastaukset[0].varmuus == 0.8


def test_llm_agentti_muotoilee_ja_tarkentaa_vain_kerran():
    tila = istunto()
    agentti = TarkentavaAgentti()
    vuoro(tila, None, agentti)

    kysymys = vuoro(tila, "Joo.", agentti)
    tarkennus = vuoro(tila, "Vähän huimaa.", agentti)
    seuraava = vuoro(tila, "Eilen illalla.", agentti)

    assert kysymys.sanottavat[-1] == "LLM: Miten uusi lääke on sopinut?"
    assert tarkennus.sanottavat == ["Ymmärrän.", "Milloin se alkoi?"]
    assert seuraava.sanottavat == ["Kiitos tarkennuksesta.", "LLM: Miten voit tänään?"]
    assert agentti.reaktiot == [True, False]
    eka = tila.vastaukset[0]
    assert (eka.vastaus, eka.jatkokysymys, eka.jatkovastaus) == (
        "Vähän huimaa.",
        "Milloin se alkoi?",
        "Eilen illalla.",
    )


def test_kaatuva_agentti_ei_katkaise_puhelua():
    tila = istunto()
    agentti = KaatuvaAgentti()
    vuoro(tila, None, agentti)

    kysymys = vuoro(tila, "Kyllä.", agentti)
    seuraava = vuoro(tila, "Hyvin.", agentti)

    assert kysymys.sanottavat[-1] == KYSYMYKSET[0].teksti
    assert seuraava.sanottavat == ["Kiitos.", KYSYMYKSET[1].teksti]


def test_hatamerkki_ohittaa_agentin_ja_lopettaa():
    tila = istunto()
    agentti = TarkentavaAgentti()
    vuoro(tila, None, agentti)
    vuoro(tila, "Kyllä.", agentti)

    hata = vuoro(tila, "Rintaan sattuu.", agentti)

    assert hata.sanottavat == [HATAOHJE]
    assert not hata.kuuntele
    assert agentti.reaktiot == []
    assert tila.loppu == PuhelunLoppu.HATAMERKKI
    assert tila.hatamerkki == "rintakipu"
    assert tila.vastaukset[0].vastaus == "Rintaan sattuu."


def test_hatamerkki_jo_lupavaiheessa_kirjataan():
    tila = istunto()
    vuoro(tila, None)

    vuoro(tila, "En saa henkeä")

    assert tila.loppu == PuhelunLoppu.HATAMERKKI
    assert tila.vastaukset[0].tunniste == "lupa"


def test_kieltaytyminen_lopettaa_kohteliaasti():
    tila = istunto()
    vuoro(tila, None)

    ei_nyt = vuoro(tila, "Ei nyt, soita myöhemmin.")

    assert ei_nyt.sanottavat == [EI_NYT]
    assert tila.loppu == PuhelunLoppu.EI_LUPAA
    assert tila.vastaukset == []


def test_pyynto_puhua_hoitajalle_ohjataan_hoitajalle():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Kyllä.")

    pyynto = vuoro(tila, "Haluaisin puhua hoitajan kanssa.")

    assert pyynto.sanottavat == [HOITAJALLE]
    assert tila.loppu == PuhelunLoppu.HALUAA_HOITAJAN


def test_laakarin_mainitseminen_ei_ole_hoitajapyynto():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Kyllä.")

    vastaus = vuoro(tila, "Kävin lääkärissä ja kaikki hyvin.")

    assert vastaus.kuuntele
    assert tila.loppu is None


def test_hiljaisuus_toistaa_kerran_ja_sitten_ohittaa_kysymyksen():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Kyllä.")

    toisto = vuoro(tila, "")
    ohitus = vuoro(tila, None)

    assert toisto.sanottavat == [EI_KUULUNUT, KYSYMYKSET[0].teksti]
    assert ohitus.sanottavat == [KYSYMYKSET[1].teksti]
    assert tila.vastaukset[0].vastaus == ""


def test_hiljaisuus_lupavaiheessa_lopettaa():
    tila = istunto()
    vuoro(tila, None)

    vuoro(tila, None)
    loppu = vuoro(tila, None)

    assert not loppu.kuuntele
    assert tila.loppu == PuhelunLoppu.EI_VASTAUSTA


def test_paattynyt_puhelu_ei_jatku():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Ei nyt.")

    jalkeen = vuoro(tila, "Hei?")

    assert jalkeen.sanottavat == []
    assert not jalkeen.kuuntele


def test_kaiku_kertoo_kuullun_ja_hatamerkin():
    avaus, ei_tulosta = kaiun_vuoro(0, None, None)
    vuoro_1, tulos = kaiun_vuoro(1, "rintaan sattuu", 0.91)
    viimeinen, _ = kaiun_vuoro(3, "", None)

    assert avaus.kuuntele and ei_tulosta is None
    assert "Kuulin: rintaan sattuu." in vuoro_1.sanottavat
    assert "Varmuus 91 prosenttia." in vuoro_1.sanottavat
    assert tulos.hatamerkki == "rintakipu"
    assert "En kuullut mitään." in viimeinen.sanottavat
    assert not viimeinen.kuuntele


# --- Matala varmuus ---


def test_matala_varmuus_pyytaa_toistamaan_kerran_ja_selva_toisto_jatkaa():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Kyllä.")

    pyynto = vuoro(tila, "mmh huim", varmuus=0.3)
    jatko = vuoro(tila, "Vähän huimaa aamuisin.", varmuus=0.9)

    assert pyynto.sanottavat == [SELVENNYSPYYNTO]
    assert pyynto.kuuntele
    assert jatko.sanottavat == ["Kiitos.", KYSYMYKSET[1].teksti]
    eka = tila.vastaukset[0]
    assert (eka.vastaus, eka.selvennys, eka.epavarma) == (
        "mmh huim",
        "Vähän huimaa aamuisin.",
        False,
    )


def test_toistokin_epaselva_merkitaan_epavarmaksi_eika_kysyta_kolmatta_kertaa():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Kyllä.")

    vuoro(tila, "mmh", varmuus=0.2)
    jatko = vuoro(tila, "hmm joo", varmuus=0.3)

    assert jatko.sanottavat[-1] == KYSYMYKSET[1].teksti
    assert tila.vastaukset[0].epavarma


def test_ohitettu_kysymys_on_epavarma():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Kyllä.")

    vuoro(tila, None)
    vuoro(tila, None)

    assert tila.vastaukset[0].vastaus == ""
    assert tila.vastaukset[0].epavarma


def test_hatasana_tunnistetaan_vaikka_varmuus_matala():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Kyllä.")

    hata = vuoro(tila, "rintaan sattuu", varmuus=0.2)

    assert hata.sanottavat == [HATAOHJE]
    assert tila.loppu == PuhelunLoppu.HATAMERKKI


def test_puuttuva_varmuus_ei_pyyda_toistoa():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Kyllä.")

    jatko = vuoro(tila, "Hyvin.", varmuus=None)

    assert jatko.sanottavat == ["Kiitos.", KYSYMYKSET[1].teksti]
    assert not tila.vastaukset[0].epavarma


def test_kaiku_kertoo_mallin():
    _, tulos = kaiun_vuoro(1, "hei", 0.5, "googlev2_telephony")

    assert tulos.stt_malli == "googlev2_telephony"
