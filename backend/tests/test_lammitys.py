import asyncio
import json

import httpx
import pytest

from app.puhelu import lammitys
from app.puhelu.agentti import LlmAgentti
from app.puhelu.lammitys import KYLMAN_UUSINTA_S, LAMMIN_RAJA_S, Lammitysvahti


class Lammitin:
    def __init__(self, kestot: list[float | Exception]) -> None:
        self.kestot = kestot
        self.kutsuja = 0

    async def __call__(self) -> float:
        self.kutsuja += 1
        kesto = self.kestot.pop(0)
        if isinstance(kesto, Exception):
            raise kesto
        return kesto


def vahti(lammitin: Lammitin, kaynnissa: bool = False) -> Lammitysvahti:
    return Lammitysvahti(lammitin, lambda: kaynnissa, vali_s=60)


def test_kylma_lammitys_ei_viela_lammin_ja_nopea_on():
    lammitin = Lammitin([52.7, 0.6])
    v = vahti(lammitin)

    asyncio.run(v.kierros())
    assert v.lammin is False
    assert v.viimeisin_s == 52.7

    asyncio.run(v.kierros())
    assert v.lammin is True
    assert v.viimeisin_s == 0.6


def test_puhelun_aikana_ei_lammiteta():
    lammitin = Lammitin([0.6])
    v = vahti(lammitin, kaynnissa=True)

    asyncio.run(v.kierros())

    assert lammitin.kutsuja == 0
    assert v.lammin is False


def test_malli_ei_vastaa_ei_ole_lammin():
    lammitin = Lammitin([0.6, httpx.ConnectError("yhteys katkesi")])
    v = vahti(lammitin)
    asyncio.run(v.kierros())

    asyncio.run(v.kierros())

    assert v.lammin is False


def test_raja_on_lammin():
    v = vahti(Lammitin([LAMMIN_RAJA_S]))

    asyncio.run(v.kierros())

    assert v.lammin is True


class Seis(Exception):
    """Pysäyttää vahdin silmukan testissä halutun unimäärän jälkeen."""


def aja_vahtia(
    v: Lammitysvahti, lammitin: Lammitin, unia: int, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[float, int]]:
    """Ajaa vahdin silmukkaa ilman oikeaa odotusta. Palauttaa jokaisesta unesta
    (odotus sekunteina, montako lämmitystä ennen sitä)."""
    unet: list[tuple[float, int]] = []

    async def feikki_uni(sekunnit: float) -> None:
        unet.append((sekunnit, lammitin.kutsuja))
        if len(unet) == unia:
            raise Seis

    monkeypatch.setattr(lammitys.asyncio, "sleep", feikki_uni)
    with pytest.raises(Seis):
        asyncio.run(v.aja())
    return unet


def test_vahti_uusii_kylmaa_nopeasti_ja_lamminta_valin_paasta(monkeypatch):
    lammitin = Lammitin([52.7, 0.6, 0.6])

    unet = aja_vahtia(vahti(lammitin), lammitin, 3, monkeypatch)

    assert unet == [(KYLMAN_UUSINTA_S, 1), (60, 2), (60, 3)]


def test_vahti_aloittaa_valin_paasta_kun_kaynnistys_lammitti(monkeypatch):
    lammitin = Lammitin([0.6])
    v = Lammitysvahti(lammitin, lambda: False, vali_s=60, lammin=True)

    unet = aja_vahtia(v, lammitin, 2, monkeypatch)

    assert unet == [(60, 0), (60, 1)]


def test_vahti_odottaa_puhelun_loppuun(monkeypatch):
    kaynnissa = [True, False]
    lammitin = Lammitin([0.6])
    v = Lammitysvahti(lammitin, lambda: kaynnissa.pop(0), vali_s=60)

    unet = aja_vahtia(v, lammitin, 2, monkeypatch)

    assert unet == [(KYLMAN_UUSINTA_S, 0), (60, 1)]


def test_vahti_pysahtyy_valin_aikana():
    lammitin = Lammitin([0.6])
    v = vahti(lammitin)

    async def aja() -> None:
        v.kaynnista()
        await asyncio.sleep(0.01)  # ensimmäinen kierros, sitten 60 s väli
        await v.pysayta()

    asyncio.run(asyncio.wait_for(aja(), timeout=5))

    assert lammitin.kutsuja == 1
    assert v.lammin is True


def test_vahti_peruu_kesken_olevan_lammityksen():
    perutut: list[str] = []

    async def jumittuva() -> float:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            perutut.append("lammitys")
            raise
        return 0.0

    v = Lammitysvahti(jumittuva, lambda: False, vali_s=60)

    async def aja() -> None:
        v.kaynnista()
        await asyncio.sleep(0.01)
        await v.pysayta()

    asyncio.run(asyncio.wait_for(aja(), timeout=5))

    assert perutut == ["lammitys"]
    assert v.lammin is False


def llm(pyynnot: list[httpx.Request], tila: int = 200) -> LlmAgentti:
    def kasittele(pyynto: httpx.Request) -> httpx.Response:
        pyynnot.append(pyynto)
        return httpx.Response(tila, json={})

    return LlmAgentti(
        base_url="http://ollama/v1/",
        malli="testimalli",
        api_avain="avain",
        aikaraja_s=120,
        ajattelu_pois=True,
        transport=httpx.MockTransport(kasittele),
        pida_muistissa=True,
    )


def test_lammitys_lahettaa_puhelun_alun_ja_pitaa_mallin_muistissa():
    pyynnot: list[httpx.Request] = []

    kesto = asyncio.run(lammitys.lammita_malli(llm(pyynnot)))

    assert kesto >= 0
    assert len(pyynnot) == len(lammitys.esimerkkipuhelut()) == 2
    for pyynto in pyynnot:
        assert pyynto.url == "http://ollama/v1/chat/completions"
        assert pyynto.extensions["timeout"]["read"] == lammitys.LAMMITYKSEN_AIKARAJA_S
        runko = json.loads(pyynto.content)
        assert runko["model"] == "testimalli"
        assert runko["max_tokens"] == 1
        assert runko["keep_alive"] == -1
        assert runko["reasoning_effort"] == "none"
        assert [viesti["role"] for viesti in runko["messages"]] == ["system", "assistant"]


def test_esimerkit_eroavat_heti_puhelukohtaisen_osan_alussa():
    # Välimuisti oppii haarakohdan vain, jos esimerkit eroavat siinä kohdassa, josta
    # oikeat puhelut alkavat erota: ikäluokan puhetapa on puhelukohtaisen osan ensimmäinen.
    ohjeet = [i.lammitysviestit()[0]["content"] for i in lammitys.esimerkkipuhelut()]
    otsikko = "TÄMÄN PUHELUN TIEDOT\n"
    yhteinen_a, puhelu_a = ohjeet[0].split(otsikko)
    yhteinen_b, puhelu_b = ohjeet[1].split(otsikko)

    assert yhteinen_a == yhteinen_b
    assert puhelu_a.splitlines()[0] != puhelu_b.splitlines()[0]


class Malli:
    """Feikki-LLM: vastaa aina samalla tekstillä ja laskee kutsut."""

    def __init__(self, vastaus: str | None = "Mukava kuulla. Oletko aloittanut lääkkeen?"):
        self.vastaus = vastaus
        self.kutsuja = 0

    async def lammita(self, viestit) -> None:
        return None

    async def vastaa(self, viestit):
        self.kutsuja += 1
        if self.vastaus is None:
            raise TimeoutError("malli ei vastannut")
        return self.vastaus


def test_kaynnistys_odottaa_lamminta_mallia_ja_simuloi_puhelut():
    kestot = [97.6, 6.3]
    malli = Malli()

    async def lammita() -> float:
        return kestot.pop(0)

    valmis = asyncio.run(lammitys.lammita_kaynnistyksessa(malli, lammita))

    assert valmis is True
    assert kestot == []  # kylmä kierros ja lämmin kierros
    assert malli.kutsuja == len(lammitys.esimerkkipuhelut())  # simuloitu vuoro kumpaankin


def test_kaynnistys_ei_jaa_odottamaan_jos_malli_ei_vastaa():
    async def lammita() -> float:
        raise httpx.ConnectError("Ollama ei käynnissä")

    malli = Malli()
    valmis = asyncio.run(lammitys.lammita_kaynnistyksessa(malli, lammita))

    assert valmis is False
    assert malli.kutsuja == 0


def test_kaynnistys_luovuttaa_jos_malli_ei_lammitysta_huolimatta_lammi():
    kierroksia = []

    async def lammita() -> float:
        kierroksia.append(1)
        return 60.0

    valmis = asyncio.run(lammitys.lammita_kaynnistyksessa(Malli(), lammita))

    assert valmis is False
    assert len(kierroksia) == lammitys.MAX_KAYNNISTYSKIERROKSET


def test_simuloitu_puhelu_huomaa_kun_malli_ei_vastaa():
    istunto = lammitys.esimerkkipuhelut()[0]

    kesto = asyncio.run(lammitys.simuloi_puhelu(istunto, Malli(vastaus=None)))

    assert kesto is None


def test_vahti_aloittaa_kaynnistyksen_tuloksesta():
    v = Lammitysvahti(Lammitin([]), lambda: False, vali_s=60, lammin=True)

    assert v.lammin is True


def test_lammitysvirhe_valittyy():
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(lammitys.lammita_malli(llm([], tila=500)))
