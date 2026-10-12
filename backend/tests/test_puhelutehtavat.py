"""Puhelutehtavien jono: tunnistus -> jono, duplikaattien esto, tilasiirtymat ja reitit."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, hae_asetukset
from app.data.adapteri import hae_datalahde
from app.main import app
from app.mallit import (
    Kiireellisyys,
    Laakejakso,
    MuutosTyyppi,
    PuhelutehtavanTila,
    Saantoloydokset,
    Vanhus,
    Yhteenveto,
)
from app.puhelutehtavat import varasto
from app.puhelutehtavat.palvelu import (
    PuhelinnumeroPuuttuu,
    SoittotiedotEivatSaatavilla,
    TehtavaaEiLoydy,
    TilasiirtymaVirhe,
    aseta_yhteystieto,
    hae_soittotiedot,
    muutosavain,
    ota_seuraava,
    siirtyma_sallittu,
    tunnista_ja_jonouta,
    vaihda_tila,
)
from app.tietokanta import KatseluLokiRivi, PuhelunTulosRivi, hae_sessio

VIITE = datetime(2020, 4, 1, tzinfo=UTC)
T = PuhelutehtavanTila


def jakso(koodi: str, nimi: str, paivia_sitten: int) -> Laakejakso:
    return Laakejakso(
        koodi=koodi,
        nimi=nimi,
        alkoi=VIITE - timedelta(days=paivia_sitten),
        paattyi=None,
        syy=None,
    )


class FeikkiLahde:
    def __init__(self) -> None:
        self.vanhukset = {
            vanhus_id: Vanhus(
                vanhus_id=vanhus_id,
                syntymapaiva=date(1940, 1, 1),
                kuolinpaiva=None,
                sukupuoli="F",
            )
            for vanhus_id in ("v-1", "v-2", "v-ei-muutoksia")
        }
        self.jaksot: dict[str, list[Laakejakso]] = {
            "v-1": [jakso("1", "lisinopril 10 MG Oral Tablet", 10)],
            "v-2": [jakso("2", "Furosemide 40 MG Oral Tablet", 20)],
            "v-ei-muutoksia": [jakso("3", "Simvastatin 10 MG Oral Tablet", 400)],
        }

    def uusin_paiva(self) -> datetime:
        return VIITE

    def hae_vanhukset(self) -> list[Vanhus]:
        return list(self.vanhukset.values())

    def hae_vanhus(self, vanhus_id: str) -> Vanhus | None:
        return self.vanhukset.get(vanhus_id)

    def hae_laakejaksot(self, vanhus_id: str) -> list[Laakejakso]:
        return list(self.jaksot.get(vanhus_id, []))

    def hae_laakejaksot_monelle(self, vanhus_idt: list[str]) -> dict[str, list[Laakejakso]]:
        return {vanhus_id: self.hae_laakejaksot(vanhus_id) for vanhus_id in vanhus_idt}


def tunnista(lahde: FeikkiLahde, sessio):
    return tunnista_ja_jonouta(lahde, sessio, VIITE, 90, 65)


# --- Puhtaat saannot ---


def test_muutosavain_on_vakaa_ja_yksiloiva():
    lahde = FeikkiLahde()
    from app.muutokset.laakkeet import tunnista_laakemuutokset

    muutos = tunnista_laakemuutokset(lahde.jaksot["v-1"], VIITE, 90)[0]

    assert muutosavain("v-1", muutos) == muutosavain("v-1", muutos)
    assert muutosavain("v-1", muutos) != muutosavain("v-2", muutos)


@pytest.mark.parametrize(
    ("nykyinen", "uusi", "yritykset", "sallittu"),
    [
        (T.ODOTTAA, T.SOITETAAN, 0, True),
        (T.ODOTTAA, T.PERUTTU, 0, True),
        (T.ODOTTAA, T.VALMIS, 0, False),
        (T.SOITETAAN, T.VALMIS, 1, True),
        (T.SOITETAAN, T.EI_VASTANNUT, 1, True),
        (T.SOITETAAN, T.ODOTTAA, 1, False),
        (T.EI_VASTANNUT, T.ODOTTAA, 1, True),
        (T.EI_VASTANNUT, T.ODOTTAA, 3, False),  # yritykset kaytetty
        (T.EI_VASTANNUT, T.PERUTTU, 3, True),
        (T.VALMIS, T.ODOTTAA, 1, False),
        (T.PERUTTU, T.ODOTTAA, 0, False),
    ],
)
def test_tilasiirtymat(nykyinen, uusi, yritykset, sallittu):
    assert siirtyma_sallittu(nykyinen, uusi, yritykset) is sallittu


# --- Tunnistus -> jono ---


def test_tunnistus_luo_tehtavan_vain_muutoksille(sessio):
    tulos = tunnista(FeikkiLahde(), sessio)

    tehtavat = varasto.listaa(sessio, tila=None, raja=100)
    assert tulos.luotuja == 2
    assert tulos.uusia_muutoksia == 2
    assert sorted(t.vanhus_id for t in tehtavat) == ["v-1", "v-2"]
    assert all(t.tila == T.ODOTTAA for t in tehtavat)
    # Kannan sisalto: uusi laake + verenpaineryhman 2 tarkennusta (vointi kysytaan avauksessa).
    assert all(
        [k.tunniste for k in t.kysymykset]
        == ["UUSI_LAAKE", "tarkennus.huimaus", "tarkennus.kaatuminen"]
        for t in tehtavat
    )


def test_tehtavan_rivilla_kaikki_soittoon_tarvittava(sessio):
    tunnista(FeikkiLahde(), sessio)

    tehtava = next(t for t in varasto.listaa(sessio, tila=None, raja=100) if t.vanhus_id == "v-1")

    assert tehtava.ika == 80
    assert tehtava.aktiivisia_laakkeita == 1
    assert tehtava.viitepaiva == VIITE
    assert tehtava.prioriteetti == 3  # uusi laake 2 + ika 80 1
    assert tehtava.perusteet == ["Ikä 80 vuotta"]
    assert tehtava.konteksti.ika == 80
    assert tehtava.konteksti.aktiivisia_laakkeita == 1
    assert [m.paivia_sitten for m in tehtava.konteksti.muutokset] == [10]
    assert "puhelinnumero" not in tehtava.model_dump()


def test_toinen_ajo_ei_luo_duplikaatteja(sessio):
    lahde = FeikkiLahde()
    tunnista(lahde, sessio)

    tulos = tunnista(lahde, sessio)

    assert tulos.luotuja == 0
    assert tulos.paivitettyja == 0
    assert tulos.uusia_muutoksia == 0
    assert len(varasto.listaa(sessio, tila=None, raja=100)) == 2


def test_uusi_muutos_liitetaan_odottavaan_tehtavaan(sessio):
    lahde = FeikkiLahde()
    tunnista(lahde, sessio)
    lahde.jaksot["v-1"].append(jakso("9", "Metformin 500 MG Oral Tablet", 2))

    tulos = tunnista(lahde, sessio)

    tehtavat = varasto.listaa(sessio, tila=None, raja=100)
    v1 = [t for t in tehtavat if t.vanhus_id == "v-1"]
    assert tulos.paivitettyja == 1
    assert tulos.luotuja == 0
    assert len(v1) == 1
    assert [m.laake.koodi for m in v1[0].muutokset] == ["9", "1"]  # uusin ensin
    # Kysymykset generoitu uudelleen: uusi laake kerran, tarkennukset molemmista ryhmista.
    assert [k.tunniste for k in v1[0].kysymykset][0] == "UUSI_LAAKE"
    assert "tarkennus.vatsa" in [k.tunniste for k in v1[0].kysymykset]
    assert v1[0].prioriteetti == 5  # prioriteetti laskettu uudelleen
    assert v1[0].aktiivisia_laakkeita == 2
    assert len(v1[0].konteksti.muutokset) == 2  # konteksti paivitetty


def test_kesken_olevaan_puheluun_ei_liiteta(sessio):
    lahde = FeikkiLahde()
    tunnista(lahde, sessio)
    kaynnissa = ota_seuraava(sessio)
    lahde.jaksot[kaynnissa.vanhus_id].append(jakso("9", "Metformin 500 MG Oral Tablet", 2))

    tulos = tunnista(lahde, sessio)

    assert tulos.luotuja == 1
    vanhuksen = [
        t for t in varasto.listaa(sessio, tila=None, raja=100) if t.vanhus_id == kaynnissa.vanhus_id
    ]
    assert sorted(t.tila for t in vanhuksen) == sorted([T.SOITETAAN, T.ODOTTAA])


# --- Soittojonon kasittely ---


def test_ota_seuraava_korkein_prioriteetti_ensin(sessio):
    lahde = FeikkiLahde()
    lahde.jaksot["v-2"].append(jakso("8", "Warfarin Sodium 5 MG Oral Tablet", 3))
    tunnista(lahde, sessio)

    otettu = ota_seuraava(sessio)

    assert otettu.vanhus_id == "v-2"
    assert any("verenohennus" in peruste for peruste in otettu.perusteet)


def test_ota_seuraava_sama_prioriteetti_vanhin_ensin(sessio):
    tunnista(FeikkiLahde(), sessio)
    tehtavat = varasto.listaa(sessio, tila=None, raja=100)
    vanhin = min(tehtavat, key=lambda t: t.luotu)

    otettu = ota_seuraava(sessio)

    assert otettu.id == vanhin.id
    assert otettu.tila == T.SOITETAAN
    assert otettu.yritykset == 1


def test_ota_seuraava_tyhjasta_jonosta(sessio):
    assert ota_seuraava(sessio) is None


def test_vaihda_tila_validoi(sessio):
    tunnista(FeikkiLahde(), sessio)
    tehtava = ota_seuraava(sessio)

    valmis = vaihda_tila(sessio, tehtava.id, T.VALMIS)

    assert valmis.tila == T.VALMIS
    with pytest.raises(TilasiirtymaVirhe):
        vaihda_tila(sessio, tehtava.id, T.ODOTTAA)
    with pytest.raises(TehtavaaEiLoydy):
        vaihda_tila(sessio, "ei-ole", T.VALMIS)


# --- Reitit ---


@pytest.fixture
def client(sessiotehdas):
    lahde = FeikkiLahde()

    def sessio_override():
        with sessiotehdas() as sessio:
            yield sessio

    app.dependency_overrides[hae_datalahde] = lambda: lahde
    app.dependency_overrides[hae_sessio] = sessio_override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_reitit_koko_kulku(client):
    tunnistus = client.post("/puhelutehtavat/tunnista", json={})
    assert tunnistus.status_code == 200
    assert tunnistus.json()["luotuja"] == 2

    odottavat = client.get("/puhelutehtavat", params={"tila": "ODOTTAA"}).json()
    assert len(odottavat) == 2
    assert odottavat[0]["kysymykset"]
    assert odottavat[0]["muutokset"][0]["tyyppi"] == MuutosTyyppi.UUSI_LAAKE

    seuraava = client.post("/puhelutehtavat/seuraava")
    assert seuraava.status_code == 200
    tehtava_id = seuraava.json()["id"]

    valmis = client.patch(f"/puhelutehtavat/{tehtava_id}", json={"tila": "VALMIS"})
    assert valmis.status_code == 200
    assert client.get(f"/puhelutehtavat/{tehtava_id}").json()["tila"] == "VALMIS"


def test_reitit_virheet(client):
    assert client.get("/puhelutehtavat/ei-ole").status_code == 404
    assert client.patch("/puhelutehtavat/ei-ole", json={"tila": "VALMIS"}).status_code == 404
    assert client.post("/puhelutehtavat/seuraava").status_code == 204

    client.post("/puhelutehtavat/tunnista", json={})
    tehtava_id = client.get("/puhelutehtavat").json()[0]["id"]
    vastaus = client.patch(f"/puhelutehtavat/{tehtava_id}", json={"tila": "VALMIS"})
    assert vastaus.status_code == 409


def test_hoitajan_nakyma_nayttaa_tietokannan_soittotehtavat(client):
    tunnistus = client.post("/puhelutehtavat/tunnista", json={})
    assert tunnistus.status_code == 200

    jono = client.get("/hoitaja/")

    assert jono.status_code == 200
    assert "Soittotehtävät" in jono.text
    assert "Pseudonyymi v-1" in jono.text
    assert "Soittoprioriteetti" in jono.text
    assert "triage- tai kiireellisyysluokitus" in jono.text
    assert "+358" not in jono.text
    assert client.get("/static/hoitaja.css").status_code == 200
    logo = client.get("/static/logo.png")
    assert logo.status_code == 200
    assert logo.headers["content-type"].startswith("image/png")

    tehtava = client.get("/puhelutehtavat").json()[0]
    yksittainen = client.get(f"/hoitaja/tapaukset/{tehtava['id']}")

    assert yksittainen.status_code == 200
    assert "TUNNISTETUT TERVEYSTIETOJEN MUUTOKSET" in yksittainen.text
    assert "PUHELUN KYSYMYSRUNKO" in yksittainen.text
    assert "Puhelun tulosta ei ole vielä tallennettu." in yksittainen.text
    assert "Sanatarkkoja puheluvastauksia tai litterointia ei tallenneta" in yksittainen.text


def test_hoitajan_tehtavassa_naytetaan_tallennettu_puheluyhteenveto_ja_kirjataan_katselu(
    client, sessiotehdas
):
    client.post("/puhelutehtavat/tunnista", json={})
    tehtava_id = client.get("/puhelutehtavat").json()[0]["id"]
    yhteenveto = Yhteenveto(
        taso=Kiireellisyys.KELTAINEN,
        saanto=Saantoloydokset(
            taso=Kiireellisyys.KELTAINEN,
            perusteet=["Hoitajan on hyvä soittaa tänään."],
        ),
        analyysi_valmis=True,
        yleisvointi="Vointi on hieman huono.",
        perustelu="Hoitajan kannattaa soittaa tänään.",
    )
    with sessiotehdas() as sessio:
        sessio.add(
            PuhelunTulosRivi(
                istunto_id="istunto-ui-testi",
                tehtava_id=tehtava_id,
                loppu="VALMIS",
                paattyi=datetime.now(UTC),
                yhteenveto=yhteenveto.model_dump(mode="json"),
            )
        )
        sessio.commit()

    sivu = client.get(f"/hoitaja/tapaukset/{tehtava_id}")

    assert sivu.status_code == 200
    assert "PUHELUN YHTEENVETO" in sivu.text
    assert "Keltainen" in sivu.text
    assert "Vointi on hieman huono." in sivu.text
    assert "Hoitajan kannattaa soittaa tänään." in sivu.text
    with sessiotehdas() as sessio:
        lokimerkinta = sessio.query(KatseluLokiRivi).one()
        assert lokimerkinta.tehtava_id == tehtava_id
        assert lokimerkinta.toiminto == "katselu"
        assert lokimerkinta.katsoja == "hackathon-demo (kirjautumaton)"


def test_hoitajan_nakymasta_paivitetaan_tila_backendin_saannoilla(client):
    client.post("/puhelutehtavat/tunnista", json={})
    tehtava_id = client.post("/puhelutehtavat/seuraava").json()["id"]

    valmis = client.patch(
        f"/hoitaja/tapaukset/{tehtava_id}/tila",
        json={"tila": "VALMIS"},
    )

    assert valmis.status_code == 200
    assert valmis.json()["tila"] == "VALMIS"

    virheellinen = client.patch(
        f"/hoitaja/tapaukset/{tehtava_id}/tila",
        json={"tila": "ODOTTAA"},
    )
    puuttuva = client.patch(
        "/hoitaja/tapaukset/ei-ole/tila",
        json={"tila": "VALMIS"},
    )

    assert virheellinen.status_code == 409
    assert puuttuva.status_code == 404


# --- Soittotiedot: numero vain soiton ajaksi ---


def test_soittotiedot_vain_kesken_olevalle_soitolle(sessio):
    tunnista(FeikkiLahde(), sessio)
    odottava = varasto.listaa(sessio, tila=T.ODOTTAA, raja=1)[0]

    with pytest.raises(SoittotiedotEivatSaatavilla):
        hae_soittotiedot(sessio, odottava.id, oletusnumero="+358401111111")


def test_soittotiedot_oletusnumerolla_ja_omalla_numerolla(sessio):
    tunnista(FeikkiLahde(), sessio)
    tehtava = ota_seuraava(sessio)

    oletus = hae_soittotiedot(sessio, tehtava.id, oletusnumero="+358401111111")
    aseta_yhteystieto(sessio, tehtava.vanhus_id, "+358402222222")
    oma = hae_soittotiedot(sessio, tehtava.id, oletusnumero="+358401111111")

    assert oletus.puhelinnumero == "+358401111111"
    assert oma.puhelinnumero == "+358402222222"
    assert oma.kysymykset == tehtava.kysymykset
    assert oma.konteksti == tehtava.konteksti


def test_soittotiedot_ilman_numeroa(sessio):
    tunnista(FeikkiLahde(), sessio)
    tehtava = ota_seuraava(sessio)

    with pytest.raises(PuhelinnumeroPuuttuu):
        hae_soittotiedot(sessio, tehtava.id, oletusnumero=None)


def test_soittotiedot_reitti(client):
    app.dependency_overrides[hae_asetukset] = lambda: Settings(
        database_url="sqlite://", dev_tila=True, demo_puhelinnumero="+358401111111"
    )
    client.post("/puhelutehtavat/tunnista", json={})
    tehtava_id = client.post("/puhelutehtavat/seuraava").json()["id"]

    vastaus = client.get(f"/puhelutehtavat/{tehtava_id}/soittotiedot")

    assert vastaus.status_code == 200
    assert vastaus.json()["puhelinnumero"] == "+358401111111"
    assert "puhelinnumero" not in client.get(f"/puhelutehtavat/{tehtava_id}").json()


def test_dev_yhteystiedon_asetus_validoi_numeron(client):
    app.dependency_overrides[hae_asetukset] = lambda: Settings(
        database_url="sqlite://", dev_tila=True
    )

    ok = client.put("/dev/yhteystiedot/v-1", json={"puhelinnumero": "+358401234567"})
    huono = client.put("/dev/yhteystiedot/v-1", json={"puhelinnumero": "040 123"})

    assert ok.status_code == 200
    assert huono.status_code == 422


# --- Katselmoinnin korjaukset ---


def test_tuplarivit_datassa_eivat_kaada_tunnistusta(sessio):
    """Demodatassa sama (potilas, koodi, alku) voi olla useana rivina -> sama muutosavain."""
    lahde = FeikkiLahde()
    lahde.jaksot["v-1"].append(jakso("1", "lisinopril 10 MG Oral Tablet", 10))  # tupla

    tulos = tunnista(lahde, sessio)

    assert tulos.luotuja == 2
    v1 = [t for t in varasto.listaa(sessio, tila=None, raja=100) if t.vanhus_id == "v-1"]
    assert len(v1[0].muutokset) == 1


def test_rinnakkainen_ajo_on_tunnistus_kaynnissa_eika_kaatuminen(sessiotehdas):
    """Toisen ajon tuplat-avain kaatuu jo flushissa kesken silmukan: pitaa nakya 409:na."""
    from app.puhelutehtavat.palvelu import TunnistusKaynnissa

    lahde = FeikkiLahde()
    with sessiotehdas() as eka, sessiotehdas() as toka:
        tunnista(lahde, eka)
        # Toinen sessio ei nae ensimmaisen avaimia, jos ne lisataan "samaan aikaan":
        # simuloidaan tyhjentamalla olemassa olevien avainten haku.
        import app.puhelutehtavat.varasto as v

        alkuperainen = v.olemassa_olevat_avaimet
        v.olemassa_olevat_avaimet = lambda sessio, avaimet: set()
        try:
            with pytest.raises(TunnistusKaynnissa):
                tunnista(lahde, toka)
        finally:
            v.olemassa_olevat_avaimet = alkuperainen


def test_yritykset_kayttanyt_tehtava_ei_nielaise_uutta_muutosta(sessio):
    lahde = FeikkiLahde()
    tunnista(lahde, sessio)
    tehtava = next(t for t in varasto.listaa(sessio, None, 100) if t.vanhus_id == "v-1")
    rivi = varasto.hae_rivi(sessio, tehtava.id)
    rivi.tila = T.EI_VASTANNUT.value
    rivi.yritykset = 3  # MAX_YRITYKSET kaytetty
    sessio.commit()
    lahde.jaksot["v-1"].append(jakso("8", "Warfarin Sodium 5 MG Oral Tablet", 2))

    tulos = tunnista(lahde, sessio)

    assert tulos.luotuja == 1  # uusi tehtava, ei liitetty loppuunkaytettyyn
    odottavat = varasto.listaa(sessio, tila=T.ODOTTAA, raja=100)
    assert any(t.vanhus_id == "v-1" for t in odottavat)


def test_ei_vastannut_palaa_jonoon_uuden_muutoksen_myota(sessio):
    lahde = FeikkiLahde()
    tunnista(lahde, sessio)
    tehtava = next(t for t in varasto.listaa(sessio, None, 100) if t.vanhus_id == "v-1")
    rivi = varasto.hae_rivi(sessio, tehtava.id)
    rivi.tila = T.EI_VASTANNUT.value
    rivi.yritykset = 1
    sessio.commit()
    lahde.jaksot["v-1"].append(jakso("8", "Warfarin Sodium 5 MG Oral Tablet", 2))

    tulos = tunnista(lahde, sessio)

    assert tulos.paivitettyja == 1
    assert varasto.hae_rivi(sessio, tehtava.id).tila == T.ODOTTAA.value
