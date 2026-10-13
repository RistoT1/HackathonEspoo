"""ConversationRelay-istunto ilman Twiliota: mallin miettiessä jatkettu puhe aloittaa vuoron
alusta koko puheella (11.10.: jatko hukattiin, "olo on kun kännissä")."""

import asyncio
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.mallit import Kiireellisyys, Kysymys, Puhelukonteksti, PuhelunLoppu
from app.puhelu import relay
from app.puhelu.istunnot import Istuntovarasto
from app.puhelu.keskustelu import EI_KUULUNUT, Puheluistunto, Vaihe, kasittele
from app.puhelu.relay import MAX_JATKOT, Relayistunto, on_kaikua

VASTAUS = "Ymmärrän, kuulostaa ikävältä. Pystytkö syömään ja juomaan normaalisti?"


class Ws:
    def __init__(self) -> None:
        self.lahetetty: list[dict] = []

    async def send_json(self, viesti: dict) -> None:
        self.lahetetty.append(viesti)


class HidasMalli:
    """Vastaa vasta, kun testi vapauttaa: vuoro on kesken niin kauan kuin testi haluaa."""

    def __init__(self) -> None:
        self.viestit: list[list[dict[str, str]]] = []
        self.vapauta = asyncio.Event()

    async def lammita(self, viestit) -> None:
        return None

    async def vastaa(self, viestit):
        self.viestit.append([dict(viesti) for viesti in viestit])
        await self.vapauta.wait()
        return VASTAUS


def istunto() -> Puheluistunto:
    return Puheluistunto(
        istunto_id="i-1",
        tehtava_id="t-1",
        kysymykset=[
            Kysymys(tunniste="UUSI_LAAKE", teksti="Miten uusi lääke on sopinut?"),
            Kysymys(tunniste="vointi", teksti="Miten voit tänään?"),
        ],
        konteksti=Puhelukonteksti(ika=82, aktiivisia_laakkeita=3, muutokset=[]),
        paivita_jono=False,
    )


async def _odota(ehto) -> None:
    for _ in range(300):
        if ehto():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("ehto ei täyttynyt")


async def _yhteys(malli: HidasMalli) -> tuple[Relayistunto, Puheluistunto, Ws]:
    tila = istunto()
    await kasittele(tila, None, None, malli)  # avaus
    ws = Ws()
    asetukset = SimpleNamespace(puhelu_valivastaus_s=30.0, relay_puheen_tauko_ms=1500)
    yhteys = Relayistunto(ws, tila, None, malli, lambda _: True, asetukset, None, None)
    return yhteys, tila, ws


def _henkilon_viestit(tila: Puheluistunto) -> list[str]:
    return [viesti["content"] for viesti in tila.historia if viesti["role"] == "user"]


def test_jatkettu_puhe_aloittaa_vuoron_alusta_koko_puheella():
    async def aja() -> None:
        malli = HidasMalli()
        yhteys, tila, ws = await _yhteys(malli)

        yhteys.ota_vastaan("Päässä pyörii kun nousen ja vatsa ei toimi ja")
        await _odota(lambda: len(malli.viestit) == 1)
        yhteys.ota_vastaan("olo on kun kännissä.")
        await _odota(lambda: len(malli.viestit) == 2)
        malli.vapauta.set()
        await yhteys.tyo

        koko = "Päässä pyörii kun nousen ja vatsa ei toimi ja olo on kun kännissä."
        assert malli.viestit[1][-1]["content"].startswith(koko)
        # Peruttu vuoro ei jää historiaan: yksi henkilön viesti, yksi vastaus.
        assert [viesti.split("\n(")[0] for viesti in _henkilon_viestit(tila)] == [koko]
        assert len(tila.vastaukset) == 1
        assert ws.lahetetty[-1]["token"] == VASTAUS

    asyncio.run(aja())


def test_jatkoja_rajataan_ettei_vuoro_veny_loputtomiin():
    async def aja() -> None:
        malli = HidasMalli()
        yhteys, tila, _ = await _yhteys(malli)

        yhteys.ota_vastaan("Vähän huono olo")
        for kerta in range(MAX_JATKOT + 1):
            await _odota(lambda kerta=kerta: len(malli.viestit) == min(kerta, MAX_JATKOT) + 1)
            yhteys.ota_vastaan(f"jatko {kerta + 1}")
        malli.vapauta.set()
        await yhteys.tyo

        viimeisin = malli.viestit[-1][-1]["content"]
        assert f"jatko {MAX_JATKOT}" in viimeisin
        assert f"jatko {MAX_JATKOT + 1}" not in viimeisin
        assert len(malli.viestit) == MAX_JATKOT + 1
        assert len(_henkilon_viestit(tila)) == 1

    asyncio.run(aja())


def test_puhe_vastauksen_jalkeen_on_uusi_vuoro():
    async def aja() -> None:
        malli = HidasMalli()
        malli.vapauta.set()
        yhteys, tila, _ = await _yhteys(malli)

        yhteys.ota_vastaan("Ihan hyvin menee.")
        await yhteys.tyo
        yhteys.kysymys_kuuluu = 0.0  # AI on lukenut kysymyksen
        yhteys.ota_vastaan("Hyvin on sopinut.")
        await yhteys.tyo

        assert len(_henkilon_viestit(tila)) == 2
        assert len(malli.viestit) == 2

    asyncio.run(aja())


def test_puhe_ennen_kysymysta_ohitetaan_eika_ohita_kysymysta():
    # 11.10.: "ei tää kyllä pelitä vielä" 0,9 s pitkän repliikin alusta luettiin vastaukseksi
    # lääkekysymykseen, jota AI ei ollut vielä ehtinyt sanoa.
    async def aja() -> None:
        malli = HidasMalli()
        malli.vapauta.set()
        yhteys, tila, _ = await _yhteys(malli)
        yhteys.ota_vastaan("Vähän huono olo.")
        await yhteys.tyo
        assert yhteys.kysymys_kuuluu > 0

        yhteys.ota_vastaan("ei tää kyllä pelitä vielä")

        assert len(malli.viestit) == 1  # ei uutta vuoroa
        assert len(tila.vastaukset) == 1
        assert tila.vastaukset[0].vastaus == "Vähän huono olo."
        assert "pelitä" not in _henkilon_viestit(tila)[0]

        yhteys.kysymys_kuuluu = 0.0
        yhteys.ota_vastaan("Joo, aloitin sen.")
        await yhteys.tyo
        assert len(tila.vastaukset) == 2

    asyncio.run(aja())


def test_hatamerkki_kasitellaan_vaikka_ai_puhuu(monkeypatch):
    monkeypatch.setattr(relay, "viimeistele_vuoro", lambda *_: None)

    async def aja() -> None:
        malli = HidasMalli()
        malli.vapauta.set()
        yhteys, tila, ws = await _yhteys(malli)
        yhteys.sessiotehdas = nullcontext
        yhteys.ota_vastaan("Vähän huono olo.")
        await yhteys.tyo

        yhteys.ota_vastaan("Rintaan sattuu kovasti.")
        await yhteys.tyo

        assert tila.hatamerkki is not None
        assert ws.lahetetty[-1]["type"] == "end"

    asyncio.run(aja())


def test_ennen_kysymysta_alkanut_puhe_ei_ole_vastaus_vaikka_loppuu_sen_jalkeen():
    # 11.10.: myöhästynyt vastaus lääkekuurikysymykseen alkoi ennen vatsakysymystä ja
    # loppui 2 s sen alun jälkeen -> luettiin vastaukseksi vatsakysymykseen.
    async def aja() -> None:
        malli = HidasMalli()
        malli.vapauta.set()
        yhteys, tila, _ = await _yhteys(malli)
        yhteys.ota_vastaan("Vähän huono olo.")
        await yhteys.tyo
        nyt = time.monotonic()
        yhteys.kysymys_kuuluu = nyt - 0.5  # kysymys alkoi kuulua 0,5 s sitten
        yhteys.puhe_alkoi = nyt - 3.0  # osittainen tunnistus: henkilö aloitti 3 s sitten

        yhteys.ota_vastaan("onhan minä ihan hyvin saanut sen käytettyä")

        assert len(tila.vastaukset) == 1
        assert "saanut sen käytettyä" not in tila.vastaukset[0].vastaus
        assert yhteys.puhe_alkoi is None

        yhteys.puhe_alkoi = time.monotonic() - 0.2  # aloitti kysymyksen jälkeen
        yhteys.ota_vastaan("Ei ole ollut vatsavaivoja.")
        await yhteys.tyo
        assert len(tila.vastaukset) == 2

    asyncio.run(aja())


def test_hiljaisuuden_jalkeen_kysymys_toistetaan(monkeypatch):
    monkeypatch.setattr(relay, "HILJAISUUS_S", 0.05)
    monkeypatch.setattr(relay, "puheen_kesto_arvio_ms", lambda _: 0)

    async def aja() -> None:
        malli = HidasMalli()
        malli.vapauta.set()
        yhteys, _, ws = await _yhteys(malli)
        yhteys.ota_vastaan("Vähän huono olo.")
        await yhteys.tyo

        await _odota(lambda: any(EI_KUULUNUT in v.get("token", "") for v in ws.lahetetty))

        toistettu = [v["token"] for v in ws.lahetetty if v.get("type") == "text"][-1]
        assert "Pystytkö syömään" in toistettu

    asyncio.run(aja())


@pytest.mark.parametrize(
    ("kuultu", "sanottu", "odotettu"),
    [
        # 11.10. puhelu: välitäyte palasi kaikuna ja malli puhui "kirjasta".
        ("Selvä kirja.", "Selvä, kirjaan tämän ylös.", True),
        ("soita 112", "Hätätilanteessa soita 112.", True),
        # Aidot vastaukset lainaavat kysymyksen sanoja, mutta eivät peräkkäin.
        ("On toiminut.", "Selvä. Onko vatsasi toiminut tavalliseen tapaan?", False),
        ("kyllä on", "Onko sinulla kipua, kyllä vai ei?", False),
        ("Ei.", "Onko sinulla kipua?", False),
        ("rintaan sattuu kovasti", "Onko sinulla rintakipua?", False),
    ],
)
def test_kaiun_tunnistus(kuultu, sanottu, odotettu):
    assert on_kaikua(kuultu, sanottu) is odotettu


def test_valitayte_kaikuna_ei_aloita_vuoroa_alusta():
    async def aja() -> None:
        malli = HidasMalli()
        yhteys, tila, _ = await _yhteys(malli)
        yhteys.ota_vastaan("Vähän huono olo.")
        await _odota(lambda: len(malli.viestit) == 1)
        await yhteys._puhu(["Selvä, kirjaan tämän ylös."], kysyy=False)
        yhteys.osittainen()  # kaiku alkaa kuulua välitäytteen aikana

        yhteys.ota_vastaan("Selvä kirja.")
        malli.vapauta.set()
        await yhteys.tyo

        assert len(malli.viestit) == 1  # ei aloitettu alusta
        assert "kirja" not in _henkilon_viestit(tila)[0].lower()

    asyncio.run(aja())


def test_kaikuna_palannut_hatanumero_ei_ole_hata():
    async def aja() -> None:
        malli = HidasMalli()
        malli.vapauta.set()
        yhteys, tila, _ = await _yhteys(malli)
        await yhteys._puhu(["Jos olo huononee, soita 112. Miten voit nyt?"], kysyy=True)
        yhteys.osittainen()

        yhteys.ota_vastaan("soita 112")

        assert tila.hatamerkki is None
        assert yhteys.tyo is None
        assert len(tila.vastaukset) == 0

    asyncio.run(aja())


def test_valitaytteen_jalkeen_alkanut_puhe_ei_aloita_vuoroa_alusta():
    # 12.10.: sivupuhe ("siinä on vieläkin tää bugi") aloitti vuoron alusta, vuoro kesti 52 s.
    async def aja() -> None:
        malli = HidasMalli()
        yhteys, tila, _ = await _yhteys(malli)
        yhteys.ota_vastaan("Vähän huono olo.")
        await _odota(lambda: len(malli.viestit) == 1)
        await yhteys._puhu(["Okei, merkitsen tämän."], kysyy=False)
        yhteys.ensimmainen_tayte = time.monotonic() - 3.0  # täyte kuului jo
        yhteys.osittainen()

        yhteys.ota_vastaan("siinä on vieläkin tää bugi")
        malli.vapauta.set()
        await yhteys.tyo

        assert len(malli.viestit) == 1
        assert "bugi" not in _henkilon_viestit(tila)[0]

    asyncio.run(aja())


def test_tukitilassa_valitaytteen_jalkeinen_puhe_kuullaan():
    # 12.10.: tukitilassa "En ole turvassa..." ohitettiin välitäytteen jälkeen.
    async def aja() -> None:
        malli = HidasMalli()
        yhteys, tila, _ = await _yhteys(malli)
        tila.vaihe = Vaihe.TUKI
        yhteys.ota_vastaan("Mies tuossa vieressä pelottaa.")
        await _odota(lambda: len(malli.viestit) == 1)
        await yhteys._puhu(["Okei, merkitsen tämän."], kysyy=False)
        yhteys.ensimmainen_tayte = time.monotonic() - 3.0
        yhteys.osittainen()

        yhteys.ota_vastaan("ja se huutaa mulle")
        await _odota(lambda: len(malli.viestit) == 2)  # vuoro alusta koko puheella
        malli.vapauta.set()
        await yhteys.tyo

        assert "huutaa" in malli.viestit[-1][-1]["content"]

    asyncio.run(aja())


def test_katkennut_yhteys_paattaa_puhelun_keskeytyneena(sessiotehdas):
    # 12.10.: ngrok katkesi kesken puhelun, Twilion paluukutsut saivat 404:n ja puhelu jäi
    # muistiin käynnissä-tilaan (ei tulosta hoitajalle, lämmitysvahti ja triage odottivat).
    async def aja() -> tuple[Puheluistunto, Istuntovarasto]:
        tila = istunto()
        await kasittele(tila, None, None, HidasMalli())
        istunnot = Istuntovarasto()
        istunnot.lisaa(tila)
        asetukset = SimpleNamespace(puhelu_valivastaus_s=30.0, relay_puheen_tauko_ms=1500)
        yhteys = Relayistunto(
            Ws(), tila, istunnot, HidasMalli(), lambda _: True, asetukset, None, sessiotehdas
        )
        yhteys.katkesi()
        yhteys.katkesi()  # toinen kutsu (Twilion myöhästynyt paluukutsu) ei tee mitään
        return tila, istunnot

    tila, istunnot = asyncio.run(aja())

    assert not istunnot.puheluita_kaynnissa()
    tulokset = istunnot.tulokset()
    assert len(tulokset) == 1
    assert tulokset[0].loppu == PuhelunLoppu.KESKEYTYI
    assert tulokset[0].yhteenveto.taso == Kiireellisyys.KELTAINEN  # katkesi kesken


def test_hallitun_lopetuksen_jalkeen_katkos_ei_muuta_tulosta():
    yhteys = Relayistunto(Ws(), istunto(), None, HidasMalli(), lambda _: True, None, None, None)
    yhteys.paattynyt = True

    yhteys.katkesi()  # ei kosketa varastoon eikä kantaan (molemmat None)
