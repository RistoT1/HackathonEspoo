"""Puhelu: repliikit ja TwiML puhtaina funktioina, reitit feikatulla Twiliolla (ei verkkoa)."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.config import Settings, hae_asetukset
from app.data.adapteri import hae_datalahde
from app.main import app
from app.mallit import Kysymys, Muutoskonteksti, MuutosTyyppi, Puhelukonteksti
from app.puhelu.istunnot import Istuntovarasto, hae_istuntovarasto
from app.puhelu.lammitys import Lammitysvahti, hae_lammitysvahti
from app.puhelu.loki import Puheluloki, hae_puheluloki
from app.puhelu.palvelu import rajat_asetuksista
from app.puhelu.puhe import (
    AVAUS,
    MAX_TWIML_PITUUS,
    Kuuntelu,
    TwimlLiianPitka,
    lopetus_twiml,
    luo_twiml,
    muutoksen_kuvaus,
    puhelun_repliikit,
    relay_lopetusaani,
    vuoro_twiml,
)
from app.puhelu.puhe import (
    testipuhelun_repliikit as luo_testipuhelun_repliikit,
)
from app.puhelu.soittaja import hae_soittaja
from app.puhelu.vihjeet import puhelun_vihjeet
from app.tietokanta import PuhelunTulosRivi, hae_sessio
from tests.test_puhelutehtavat import FeikkiLahde

DEMONUMERO = "+358401111111"


JULKINEN = "https://testi.example"
TOKEN = "testitoken"


class FeikkiSoittaja:
    def __init__(self) -> None:
        self.soitot: list[tuple[str, str]] = []
        self.webhooksoitot: list[tuple[str, str, str | None]] = []

    def soita(self, numero: str, twiml: str) -> str:
        self.soitot.append((numero, twiml))
        return f"CA-feikki-{len(self.soitot)}"

    def soita_webhookilla(self, numero: str, url: str, tila_url: str | None) -> str:
        self.webhooksoitot.append((numero, url, tila_url))
        return f"CA-webhook-{len(self.webhooksoitot)}"


def asetukset(**muutokset) -> Settings:
    # _env_file=None: testi ei saa lukea oikeita Twilio-avaimia .env:sta.
    arvot = {
        "database_url": "sqlite://",
        "dev_tila": True,
        "demo_puhelinnumero": DEMONUMERO,
        "julkinen_url": JULKINEN,
        "twilio_auth_token": TOKEN,
    }
    arvot.update(muutokset)
    return Settings(_env_file=None, **arvot)


def twilio_post(client: TestClient, polku: str, kentat: dict[str, str], token: str = TOKEN):
    """Kuten Twilio: lomake + X-Twilio-Signature julkisesta osoitteesta laskettuna."""
    allekirjoitus = RequestValidator(token).compute_signature(f"{JULKINEN}{polku}", kentat)
    return client.post(polku, data=kentat, headers={"X-Twilio-Signature": allekirjoitus})


def polku(url: str) -> str:
    return url.removeprefix(JULKINEN)


# --- Puhtaat funktiot ---


def test_puhelu_alkaa_ai_ilmoituksella():
    kysymykset = [Kysymys(tunniste="vointi", teksti="Miten voit?")]

    repliikit = puhelun_repliikit(kysymykset)

    assert repliikit[0] == AVAUS
    assert "tekoäly" in AVAUS
    assert repliikit[1] == "Miten voit?"
    assert "hoitajalle" in AVAUS


def test_muutoksen_kuvaus_ilman_ryhmaa_ja_eilen():
    muutos = Muutoskonteksti(tyyppi=MuutosTyyppi.UUSI_LAAKE, laakeryhma=None, paivia_sitten=1)

    assert muutoksen_kuvaus(muutos) == "Uusi lääke, eilen."


def test_testipuhelu_kertoo_kontekstin_ja_numeroi_kysymykset():
    konteksti = Puhelukonteksti(
        ika=82,
        aktiivisia_laakkeita=5,
        muutokset=[
            Muutoskonteksti(
                tyyppi=MuutosTyyppi.ANNOSMUUTOS, laakeryhma="verenohennus", paivia_sitten=3
            )
        ],
    )
    kysymykset = [
        Kysymys(tunniste="a", teksti="Eka?"),
        Kysymys(tunniste="b", teksti="Toka?"),
    ]

    repliikit = luo_testipuhelun_repliikit(kysymykset, konteksti)

    assert "Potilas on 82-vuotias." in repliikit
    assert "Annosmuutos, ryhmä verenohennus, 3 päivää sitten." in repliikit
    assert "Kysymys 1. Eka?" in repliikit
    assert "Kysymys 2. Toka?" in repliikit


def test_twiml_suomeksi_ja_merkit_escapattu():
    twiml = luo_twiml(["Onko kipua & huimausta?"], "Polly.Suvi-Neural")

    assert "language=" not in twiml  # aani maaraa kielen; Polly + language antaa varoituksen 13512
    assert 'voice="Polly.Suvi-Neural"' in twiml
    assert "kipua &amp; huimausta" in twiml


def test_relayn_lopetus_kayttaa_samaa_elevenlabs_miesaanta():
    aani, kieli = relay_lopetusaani(
        "ElevenLabs",
        "3OArekHEkHv5XvmZirVD-flash_v2_5-0.8_0.75_0.8",
        "Polly.Suvi-Neural",
    )

    twiml = lopetus_twiml(["Kiitos ja hei."], aani, kieli)

    assert 'voice="ElevenLabs.3OArekHEkHv5XvmZirVD"' in twiml
    assert 'language="fi-FI"' in twiml
    assert "Polly.Suvi" not in twiml


def test_liian_pitka_twiml_hylataan():
    with pytest.raises(TwimlLiianPitka):
        luo_twiml(["a" * MAX_TWIML_PITUUS], "Polly.Suvi-Neural")


# --- Reitit ---


@pytest.fixture
def soittaja() -> FeikkiSoittaja:
    return FeikkiSoittaja()


@pytest.fixture
def istunnot() -> Istuntovarasto:
    return Istuntovarasto()


@pytest.fixture
def client(sessiotehdas, soittaja, istunnot):
    lahde = FeikkiLahde()

    def sessio_override():
        with sessiotehdas() as sessio:
            yield sessio

    app.dependency_overrides[hae_datalahde] = lambda: lahde
    app.dependency_overrides[hae_sessio] = sessio_override
    app.dependency_overrides[hae_soittaja] = lambda: soittaja
    app.dependency_overrides[hae_istuntovarasto] = lambda: istunnot
    app.dependency_overrides[hae_asetukset] = lambda: asetukset()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_testipuhelu_soittaa_demonumeroon_eika_muuta_jonoa(client, soittaja):
    client.post("/puhelutehtavat/tunnista", json={})

    vastaus = client.post("/dev/puhelu/testi")

    assert vastaus.status_code == 200
    tulos = vastaus.json()
    assert tulos["puhelu_sid"] == "CA-feikki-1"
    assert any(repliikki.startswith("Kysymys 1.") for repliikki in tulos["repliikit"])
    numero, twiml = soittaja.soitot[0]
    assert numero == DEMONUMERO
    assert "<Say" in twiml
    tilat = {tehtava["tila"] for tehtava in client.get("/puhelutehtavat").json()}
    assert tilat == {"ODOTTAA"}


def test_testipuhelu_tyhjalla_jonolla_409(client, soittaja):
    vastaus = client.post("/dev/puhelu/testi")

    assert vastaus.status_code == 409
    assert soittaja.soitot == []


def test_aloita_vaatii_varatun_tehtavan(client, soittaja):
    client.post("/puhelutehtavat/tunnista", json={})
    odottava_id = client.get("/puhelutehtavat").json()[0]["id"]

    ennen_varausta = client.post(f"/puhelu/aloita/{odottava_id}")
    varattu_id = client.post("/puhelutehtavat/seuraava").json()["id"]
    varattu = client.post(f"/puhelu/aloita/{varattu_id}")

    assert ennen_varausta.status_code == 409
    assert varattu.status_code == 200
    assert varattu.json()["repliikit"][0] == AVAUS
    numero, url, tila_url = soittaja.webhooksoitot[0]
    assert numero == DEMONUMERO
    assert url.startswith(f"{JULKINEN}/puhelu/vuoro/")
    assert tila_url.startswith(f"{JULKINEN}/puhelu/tila/")


def _aloita_keskustelu(client, soittaja) -> tuple[str, str]:
    """Varaa tehtavan, soittaa ja palauttaa (tehtava_id, vuoron polku)."""
    client.post("/puhelutehtavat/tunnista", json={})
    tehtava_id = client.post("/puhelutehtavat/seuraava").json()["id"]
    client.post(f"/puhelu/aloita/{tehtava_id}")
    _, url, _ = soittaja.webhooksoitot[-1]
    return tehtava_id, polku(url)


def test_koko_keskustelu_webhookeilla_merkitsee_tehtavan_valmiiksi(client, soittaja):
    tehtava_id, vuoro = _aloita_keskustelu(client, soittaja)

    avaus = twilio_post(client, vuoro, {"CallSid": "CA1"})
    assert avaus.status_code == 200
    assert "tekoäly" in avaus.text
    assert "hoitajalle" in avaus.text
    assert "<Gather" in avaus.text

    vastaus = twilio_post(client, vuoro, {"CallSid": "CA1", "SpeechResult": "Kyllä on."})
    for _ in range(10):
        if "<Hangup" in vastaus.text:
            break
        vastaus = twilio_post(
            client, vuoro, {"CallSid": "CA1", "SpeechResult": "Hyvin menee.", "Confidence": "0.9"}
        )

    assert "<Hangup" in vastaus.text
    assert "hoitajalle" in vastaus.text
    assert client.get(f"/puhelutehtavat/{tehtava_id}").json()["tila"] == "VALMIS"
    tulos = client.get("/dev/puhelu/tulokset").json()[0]
    assert tulos["loppu"] == "VALMIS"
    assert tulos["vastaukset"][1]["vastaus"] == "Hyvin menee."
    assert tulos["vastaukset"][1]["varmuus"] == 0.9


def test_hatasana_webhookissa_ohjaa_112_ja_lopettaa(client, soittaja):
    _, vuoro = _aloita_keskustelu(client, soittaja)
    twilio_post(client, vuoro, {"CallSid": "CA1"})
    twilio_post(client, vuoro, {"CallSid": "CA1", "SpeechResult": "Joo."})

    vastaus = twilio_post(
        client, vuoro, {"CallSid": "CA1", "SpeechResult": "Rintaan sattuu kovasti."}
    )

    assert "yksi, yksi, kaksi" in vastaus.text  # 112 numero kerrallaan
    assert "<Hangup" in vastaus.text
    assert client.get("/dev/puhelu/tulokset").json()[0]["hatamerkki"] == "rintakipu"


def test_ei_vastattu_status_callback_palauttaa_tehtavan_jonoon(client, soittaja):
    tehtava_id, _ = _aloita_keskustelu(client, soittaja)
    _, _, tila_url = soittaja.webhooksoitot[-1]

    vastaus = twilio_post(client, polku(tila_url), {"CallSid": "CA1", "CallStatus": "no-answer"})

    assert vastaus.status_code == 204
    assert client.get(f"/puhelutehtavat/{tehtava_id}").json()["tila"] == "EI_VASTANNUT"
    assert client.get("/dev/puhelu/tulokset").json()[0]["loppu"] == "EI_VASTANNUT"


def test_vaara_allekirjoitus_hylataan(client, soittaja):
    _, vuoro = _aloita_keskustelu(client, soittaja)

    vastaus = twilio_post(client, vuoro, {"CallSid": "CA1"}, token="vaara")

    assert vastaus.status_code == 403


def test_tuntematon_istunto_lopettaa_kohteliaasti(client):
    vastaus = twilio_post(client, "/puhelu/vuoro/ei-ole", {"CallSid": "CA1"})

    assert vastaus.status_code == 200
    assert "tekninen vika" in vastaus.text
    assert "<Hangup" in vastaus.text


def test_dev_keskustelu_ei_muuta_jonoa(client, soittaja):
    client.post("/puhelutehtavat/tunnista", json={})

    vastaus = client.post("/dev/puhelu/keskustelu")
    _, url, _ = soittaja.webhooksoitot[0]
    twilio_post(client, polku(url), {"CallSid": "CA1"})
    twilio_post(client, polku(url), {"CallSid": "CA1", "SpeechResult": "En ehdi nyt."})
    twilio_post(client, polku(url), {"CallSid": "CA1", "SpeechResult": "Joo kaikki hyvin."})

    assert vastaus.status_code == 200
    tilat = {tehtava["tila"] for tehtava in client.get("/puhelutehtavat").json()}
    assert tilat == {"ODOTTAA"}
    assert client.get("/dev/puhelu/tulokset").json()[0]["loppu"] == "LOPETTI"


def test_dev_seuraava_puhelu_on_lyhyt_vointiseuranta_eika_toista_laakekysymyksia(
    client, soittaja, sessiotehdas
):
    client.post("/puhelutehtavat/tunnista", json={})
    tehtava_id = client.get("/puhelutehtavat").json()[0]["id"]
    with sessiotehdas() as sessio:
        sessio.add(
            PuhelunTulosRivi(
                istunto_id="aiempi-puhelu",
                tehtava_id=tehtava_id,
                loppu="VALMIS",
                paattyi=datetime.now(UTC),
                yhteenveto={
                    "taso": "vihreä",
                    "saanto": {"taso": "vihreä"},
                    "yleisvointi": "ihan hyvä",
                    "oireet": [],
                    "laakkeen_kaytto": "kyllä",
                    "laakkeen_huomiot": "lääkkeen aloitus sujui hyvin",
                },
            )
        )
        sessio.commit()

    aloitus = client.post(f"/dev/puhelu/keskustelu?tehtava_id={tehtava_id}")
    _, url, _ = soittaja.webhooksoitot[-1]
    avaus = twilio_post(client, polku(url), {"CallSid": "CA-seuranta"})
    lopetus = twilio_post(
        client,
        polku(url),
        {"CallSid": "CA-seuranta", "SpeechResult": "Kyllä, kaikki on edelleen hyvin."},
    )

    assert aloitus.status_code == 200
    assert len(aloitus.json()["repliikit"]) == 1
    assert "Viime puhelussa" in avaus.text
    assert "huimausta, pahoinvointia tai muita oireita" in avaus.text
    assert "uusi lääke" not in avaus.text
    assert "lääkkeen käyttö" not in lopetus.text
    assert "<Hangup" in lopetus.text


def test_dev_keskustelu_ei_soita_kylmalla_mallilla(client, soittaja):
    client.post("/puhelutehtavat/tunnista", json={})

    async def kylma() -> float:
        return 52.7

    vahti = Lammitysvahti(kylma, lambda: False, vali_s=60)
    asyncio.run(vahti.kierros())
    app.dependency_overrides[hae_lammitysvahti] = lambda: vahti

    vastaus = client.post("/dev/puhelu/keskustelu")

    assert vastaus.status_code == 503
    assert "lämpenee" in vastaus.json()["detail"]
    assert "53 s" in vastaus.json()["detail"]
    assert soittaja.webhooksoitot == []


def test_kaikutesti_toistaa_kuullun(client, soittaja):
    client.post("/dev/puhelu/kaiku")
    _, url, tila_url = soittaja.webhooksoitot[0]
    assert tila_url is None

    avaus = twilio_post(client, polku(url), {"CallSid": "CA1"})
    kaiku = twilio_post(
        client,
        "/dev/puhelu/kaiku/vuoro/1",
        {"CallSid": "CA1", "SpeechResult": "Minua huimaa", "Confidence": "0.87"},
    )

    assert "Sano jotain" in avaus.text
    assert "Kuulin: Minua huimaa." in kaiku.text
    assert "87 prosenttia" in kaiku.text
    assert "/dev/puhelu/kaiku/vuoro/2" in kaiku.text
    assert client.get("/dev/puhelu/kaiut").json()[0]["kuultu"] == "Minua huimaa"


def test_keskustelu_ilman_julkista_urlia_503(client):
    app.dependency_overrides[hae_asetukset] = lambda: asetukset(julkinen_url=None)
    client.post("/puhelutehtavat/tunnista", json={})

    vastaus = client.post("/dev/puhelu/keskustelu")

    assert vastaus.status_code == 503
    assert "JULKINEN_URL" in vastaus.json()["detail"]


def test_puuttuvat_twilio_asetukset_503(sessiotehdas):
    def sessio_override():
        with sessiotehdas() as sessio:
            yield sessio

    app.dependency_overrides[hae_sessio] = sessio_override
    app.dependency_overrides[hae_asetukset] = lambda: asetukset()
    try:
        vastaus = TestClient(app).post("/dev/puhelu/testi")
    finally:
        app.dependency_overrides.clear()

    assert vastaus.status_code == 503
    assert "TWILIO_ACCOUNT_SID" in vastaus.json()["detail"]


# --- Puheentunnistuksen asetukset ---


def test_kuuntelu_kayttaa_sekuntitaukoa_mallia_ja_vihjeita():
    kuuntelu = Kuuntelu(
        aani="Polly.Suvi-Neural",
        tauko_s=3,
        odotus_s=7,
        malli="googlev2_telephony",
        vihjeet=("huimaa", "huimaa", "pilkku, ei kelpaa", " kyllä ", "x" * 101),
    )

    twiml = vuoro_twiml(["Miten voit?"], True, "https://x/v", kuuntelu)

    assert 'speechTimeout="3"' in twiml
    assert 'timeout="7"' in twiml
    assert 'speechModel="googlev2_telephony"' in twiml
    assert 'hints="huimaa,kyllä"' in twiml
    assert 'language="fi-FI"' in twiml


def test_kuuntelu_ilman_mallia_jattaa_oletuksen():
    kuuntelu = Kuuntelu(aani="Polly.Suvi-Neural", tauko_s=2, odotus_s=6)

    twiml = vuoro_twiml(["Hei"], True, "https://x/v", kuuntelu)

    assert "speechModel" not in twiml
    assert "hints" not in twiml
    assert 'speechTimeout="2"' in twiml


def test_puhelun_vihjeissa_perussanasto_lisavihjeet_ja_laakeryhma():
    konteksti = Puhelukonteksti(
        ika=80,
        aktiivisia_laakkeita=2,
        muutokset=[
            Muutoskonteksti(
                tyyppi=MuutosTyyppi.UUSI_LAAKE, laakeryhma="verenohennus", paivia_sitten=2
            )
        ],
    )

    vihjeet = puhelun_vihjeet(konteksti, ("savolaenen",))

    assert "huimaa" in vihjeet
    assert "rintakipu" in vihjeet
    assert "savolaenen" in vihjeet
    assert "verenohennus" in vihjeet


def test_asetukset_kulkevat_webhookin_twimliin(client, soittaja):
    app.dependency_overrides[hae_asetukset] = lambda: asetukset(
        stt_malli="experimental_conversations", puheen_tauko_s=4, stt_lisavihjeet="kuopio, savo"
    )
    client.post("/dev/puhelu/kaiku")
    _, url, _ = soittaja.webhooksoitot[0]

    avaus = twilio_post(client, polku(url), {"CallSid": "CA1"})
    kaiku = twilio_post(
        client, "/dev/puhelu/kaiku/vuoro/1", {"CallSid": "CA1", "SpeechResult": "Kuopio"}
    )

    assert 'speechModel="experimental_conversations"' in avaus.text
    assert 'speechTimeout="4"' in avaus.text
    assert "kuopio,savo" in avaus.text
    assert kaiku.status_code == 200
    assert client.get("/dev/puhelu/kaiut").json()[0]["stt_malli"] == "experimental_conversations"


# --- Dev-puheluloki ---


def test_puheluloki_kirjaa_vuoron_ja_syyn(client, soittaja, tmp_path):
    loki = Puheluloki(tmp_path)
    app.dependency_overrides[hae_puheluloki] = lambda: loki
    _, vuoro = _aloita_keskustelu(client, soittaja)
    istunto_id = vuoro.rsplit("/", 1)[-1]

    twilio_post(client, vuoro, {"CallSid": "CA1"})
    twilio_post(client, vuoro, {"CallSid": "CA1", "SpeechResult": "Kuka soittaa?"})

    kaikki = [json.loads(rivi) for rivi in loki.tiedosto(istunto_id).read_text().splitlines()]
    rivit = [rivi for rivi in kaikki if rivi["tapahtuma"] == "vuoro"]
    assert [rivi["kuultu"] for rivi in kaikki if rivi["tapahtuma"] == "kuultu"] == [
        None,
        "Kuka soittaa?",
    ]
    assert [rivi["syyt"] for rivi in rivit] == [["avaus"], ["usein_kysytty"]]
    assert rivit[1]["kuultu"] == "Kuka soittaa?"
    assert rivit[1]["kuuntele"] is True


def test_puheluloki_oletuksena_pois():
    asetukset_ilman = Settings(_env_file=None, database_url="sqlite://", dev_tila=True)
    asetukset_tuotanto = Settings(
        _env_file=None, database_url="sqlite://", dev_tila=False, puhelu_loki=True
    )

    assert hae_puheluloki(asetukset_ilman) is None
    assert hae_puheluloki(asetukset_tuotanto) is None


def test_rajat_ja_kesto_asetuksista(monkeypatch):
    luodut = {}

    class FeikkiClient:
        def __init__(self, sid, token):
            self.calls = self

        def create(self, **kwargs):
            luodut.update(kwargs)
            return type("Puhelu", (), {"sid": "CA1"})()

    monkeypatch.setattr("app.puhelu.soittaja.Client", FeikkiClient)
    maaritys = asetukset(
        twilio_account_sid="AC",
        twilio_numero="+1",
        puhelu_max_kesto_s=1200,
        matala_varmuus=0.4,
    )

    hae_soittaja(maaritys).soita_webhookilla(DEMONUMERO, "https://x/v", None)
    rajat = rajat_asetuksista(maaritys)

    assert luodut["time_limit"] == 1200
    assert rajat.varmuusraja == 0.4
