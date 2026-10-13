import asyncio

import httpx
import pytest

from app.config import Settings
from app.puhelu import lammitys
from app.puhelu.lammitys import LAMMIN_RAJA_S, Lammitysvahti


class Lammitin:
    def __init__(self, kestot: list[float | Exception]) -> None:
        self.kestot = kestot
        self.kutsuja = 0

    def __call__(self) -> float:
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

    v.kierros()
    assert v.lammin is False
    assert v.viimeisin_s == 52.7

    v.kierros()
    assert v.lammin is True
    assert v.viimeisin_s == 0.6


def test_puhelun_aikana_ei_lammiteta():
    lammitin = Lammitin([0.6])
    v = vahti(lammitin, kaynnissa=True)

    v.kierros()

    assert lammitin.kutsuja == 0
    assert v.lammin is False


def test_malli_ei_vastaa_ei_ole_lammin():
    lammitin = Lammitin([0.6, httpx.ConnectError("yhteys katkesi")])
    v = vahti(lammitin)
    v.kierros()

    v.kierros()

    assert v.lammin is False


def test_raja_on_lammin():
    v = vahti(Lammitin([LAMMIN_RAJA_S]))

    v.kierros()

    assert v.lammin is True


def test_lammitys_lahettaa_puhelun_alun_ja_pitaa_mallin_muistissa(monkeypatch):
    lahetetyt: list[dict] = []

    def feikki_post(url: str, json: dict, headers: dict, timeout: float) -> httpx.Response:
        lahetetyt.append({"url": url, "runko": json})
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(lammitys.httpx, "post", feikki_post)
    kesto = lammitys.lammita_malli(
        asetukset().model_copy(update={"llm_base_url": "http://ollama/v1/"})
    )

    assert kesto >= 0
    assert len(lahetetyt) == len(lammitys.esimerkkipuhelut()) == 2
    for lahetetty in lahetetyt:
        assert lahetetty["url"] == "http://ollama/v1/chat/completions"
        runko = lahetetty["runko"]
        assert runko["model"] == "testimalli"
        assert runko["max_tokens"] == 1
        assert runko["keep_alive"] == -1
        assert [viesti["role"] for viesti in runko["messages"]] == ["system", "assistant"]


def test_esimerkit_eroavat_heti_puhelukohtaisen_osan_alussa():
    # Välimuisti oppii haarakohdan vain, jos esimerkit eroavat siinä kohdassa, josta
    # oikeat puhelut alkavat erota: ikäluokan puhetapa on puhelukohtaisen osan ensimmäinen.
    ohjeet = [lammitys.lammitysviestit(i)[0]["content"] for i in lammitys.esimerkkipuhelut()]
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


def asetukset() -> Settings:
    return Settings(_env_file=None, database_url="sqlite://", llm_malli="testimalli")


def test_kaynnistys_odottaa_lamminta_mallia_ja_simuloi_puhelut():
    kestot = [97.6, 6.3]
    malli = Malli()

    def lammita(_asetukset: Settings) -> float:
        return kestot.pop(0)

    valmis = asyncio.run(lammitys.lammita_kaynnistyksessa(asetukset(), malli, lammita))

    assert valmis is True
    assert kestot == []  # kylmä kierros ja lämmin kierros
    assert malli.kutsuja == len(lammitys.esimerkkipuhelut())  # simuloitu vuoro kumpaankin


def test_kaynnistys_ei_jaa_odottamaan_jos_malli_ei_vastaa():
    def lammita(_asetukset: Settings) -> float:
        raise httpx.ConnectError("Ollama ei käynnissä")

    malli = Malli()
    valmis = asyncio.run(lammitys.lammita_kaynnistyksessa(asetukset(), malli, lammita))

    assert valmis is False
    assert malli.kutsuja == 0


def test_kaynnistys_luovuttaa_jos_malli_ei_lammitysta_huolimatta_lammi():
    kierroksia = []

    def lammita(_asetukset: Settings) -> float:
        kierroksia.append(1)
        return 60.0

    valmis = asyncio.run(lammitys.lammita_kaynnistyksessa(asetukset(), Malli(), lammita))

    assert valmis is False
    assert len(kierroksia) == lammitys.MAX_KAYNNISTYSKIERROKSET


def test_simuloitu_puhelu_huomaa_kun_malli_ei_vastaa():
    istunto = lammitys.esimerkkipuhelut()[0]

    kesto = asyncio.run(lammitys.simuloi_puhelu(istunto, Malli(vastaus=None)))

    assert kesto is None


def test_vahti_aloittaa_kaynnistyksen_tuloksesta():
    v = Lammitysvahti(Lammitin([]), lambda: False, vali_s=60, lammin=True)

    assert v.lammin is True


def test_lammitysvirhe_valittyy(monkeypatch):
    def feikki_post(url: str, json: dict, headers: dict, timeout: float) -> httpx.Response:
        return httpx.Response(500, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(lammitys.httpx, "post", feikki_post)
    with pytest.raises(httpx.HTTPStatusError):
        lammitys.lammita_malli(asetukset())
