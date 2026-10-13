"""Potilassivu (/hoitaja/potilaat/{vanhus_id}): perustiedot, lääkitys, tehtävät ja oireet.
Datalähde feikataan; ei Postgresia."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.data.adapteri import DataEiSaatavilla, hae_datalahde
from app.dev.henkilotiedot import tayta_puuttuvat
from app.hoitaja.potilas import potilasrivit, potilastiedot
from app.main import app
from app.mallit import (
    Kiireellisyys,
    Laakejakso,
    OireYhteenvedossa,
    Puhelukonteksti,
    PuhelunLoppu,
    Puhelutehtava,
    Saantoloydokset,
    TallennettuTulos,
    Vanhus,
    Yhteenveto,
)
from app.puhelutehtavat import varasto
from app.tietokanta import KatseluLokiRivi, PuhelunTulosRivi, PuhelutehtavaRivi, hae_sessio

VIITE = datetime(2020, 4, 1, tzinfo=UTC)
VANHUS = Vanhus(
    vanhus_id="vanhus-1", syntymapaiva=date(1940, 6, 1), kuolinpaiva=None, sukupuoli="F"
)


def _jakso(koodi: str, nimi: str, alkoi: datetime, paattyi: datetime | None) -> Laakejakso:
    return Laakejakso(koodi=koodi, nimi=nimi, alkoi=alkoi, paattyi=paattyi, syy=None)


JAKSOT = [
    _jakso(
        "A", "Simvastatin 10 MG", datetime(2018, 3, 1, tzinfo=UTC), datetime(2019, 3, 1, tzinfo=UTC)
    ),
    _jakso("A", "Simvastatin 10 MG", datetime(2019, 3, 1, tzinfo=UTC), None),  # uusinta
    _jakso("B", "Amlodipine 5 MG", datetime(2020, 3, 20, tzinfo=UTC), None),
    _jakso(
        "C",
        "Amoxicillin 500 MG",
        datetime(2019, 1, 1, tzinfo=UTC),
        datetime(2019, 1, 10, tzinfo=UTC),
    ),
]


def test_potilastiedot_kayttaa_uusinta_jaksoa_ja_lopetettu_jaa_pois():
    tiedot = potilastiedot("vanhus-1", VANHUS, JAKSOT, VIITE)

    assert (tiedot.ika, tiedot.sukupuoli) == (79, "nainen")
    nimet = [(laake.nimi, laake.alkoi.year) for laake in tiedot.aktiiviset_laakkeet]
    assert nimet == [("Amlodipine 5 MG", 2020), ("Simvastatin 10 MG", 2019)]


def test_potilastiedot_ilman_terveysdataa():
    tiedot = potilastiedot("vanhus-1", None, None, None)

    assert tiedot.terveysdata_saatavilla is False
    assert tiedot.aktiiviset_laakkeet == []


class FeikkiLahde:
    def __init__(self) -> None:
        self.virhe: Exception | None = None

    def hae_vanhus(self, vanhus_id: str) -> Vanhus | None:
        if self.virhe is not None:
            raise self.virhe
        return VANHUS if vanhus_id == "vanhus-1" else None

    def hae_laakejaksot(self, vanhus_id: str) -> list[Laakejakso]:
        return JAKSOT

    def uusin_paiva(self) -> datetime:
        return VIITE


@pytest.fixture
def asiakas(sessiotehdas: sessionmaker[Session]):
    with sessiotehdas() as sessio:
        konteksti = Puhelukonteksti(ika=79, aktiivisia_laakkeita=2, muutokset=[])
        sessio.add(
            PuhelutehtavaRivi(
                id="t-1",
                vanhus_id="vanhus-1",
                tila="VALMIS",
                prioriteetti=10,
                perusteet=[],
                ika=79,
                aktiivisia_laakkeita=2,
                viitepaiva=VIITE,
                yritykset=1,
                kysymykset=[],
                konteksti=konteksti.model_dump(mode="json"),
            )
        )
        yhteenveto = Yhteenveto(
            taso=Kiireellisyys.KELTAINEN,
            saanto=Saantoloydokset(taso=Kiireellisyys.KELTAINEN),
            oireet=[OireYhteenvedossa(oire="pyörryttää", voimakkuus="lievä")],
        )
        sessio.add(
            PuhelunTulosRivi(
                istunto_id="i-1",
                tehtava_id="t-1",
                loppu="VALMIS",
                paattyi=datetime(2026, 10, 12, 14, 5, tzinfo=UTC),
                yhteenveto=yhteenveto.model_dump(mode="json"),
            )
        )
        sessio.commit()

    def sessio_override():
        with sessiotehdas() as sessio:
            yield sessio

    lahde = FeikkiLahde()
    app.dependency_overrides[hae_sessio] = sessio_override
    app.dependency_overrides[hae_datalahde] = lambda: lahde
    yield TestClient(app), lahde
    app.dependency_overrides.clear()


def test_potilassivu_nayttaa_tiedot_tehtavat_ja_oireet(asiakas, sessiotehdas):
    client, _ = asiakas

    vastaus = client.get("/hoitaja/potilaat/vanhus-1")

    assert vastaus.status_code == 200
    teksti = vastaus.text
    assert "79 vuotta, nainen" in teksti
    assert "Amlodipine 5 MG" in teksti
    assert "Amoxicillin" not in teksti  # lopetettu ennen viitepäivää
    assert 'href="/hoitaja/tapaukset/t-1"' in teksti
    assert "Mistä henkilö on kertonut" in teksti
    assert "+358" not in teksti  # puhelinnumero ei näy (pseudonymisointi)
    with sessiotehdas() as sessio:
        loki = sessio.scalars(select(KatseluLokiRivi)).all()
    assert [(r.vanhus_id, r.tehtava_id, r.toiminto) for r in loki] == [
        ("vanhus-1", None, "potilassivu")
    ]


def test_potilassivu_ilman_terveysdataa_nayttaa_tehtavat(asiakas):
    client, lahde = asiakas
    lahde.virhe = DataEiSaatavilla("demo-skeema tyhjä")

    vastaus = client.get("/hoitaja/potilaat/vanhus-1")

    assert vastaus.status_code == 200
    assert "Terveysdataa ei saatu luettua" in vastaus.text
    assert 'href="/hoitaja/tapaukset/t-1"' in vastaus.text


def test_tuntematon_potilas_404_eika_lokimerkintaa(asiakas, sessiotehdas):
    client, _ = asiakas

    assert client.get("/hoitaja/potilaat/ei-ole").status_code == 404
    with sessiotehdas() as sessio:
        assert sessio.scalars(select(KatseluLokiRivi)).all() == []


def test_tapaussivulta_linkki_potilaan_sivulle(asiakas):
    client, _ = asiakas

    vastaus = client.get("/hoitaja/tapaukset/t-1")

    assert 'href="/hoitaja/potilaat/vanhus-1"' in vastaus.text


# --- Potilaslista ---


def _tehtava_dto(tehtava_id: str, vanhus_id: str, tila: str, luotu: datetime) -> Puhelutehtava:
    return Puhelutehtava(
        id=tehtava_id,
        vanhus_id=vanhus_id,
        tila=tila,
        prioriteetti=1,
        perusteet=[],
        ika=80,
        aktiivisia_laakkeita=1,
        viitepaiva=VIITE,
        yritykset=0,
        muutokset=[],
        kysymykset=[],
        konteksti=Puhelukonteksti(ika=80, aktiivisia_laakkeita=1, muutokset=[]),
        luotu=luotu,
        paivitetty=luotu,
    )


def test_potilasrivit_henkiloittain_tuorein_ensin():
    tehtavat = [
        _tehtava_dto("t-a1", "a", "VALMIS", datetime(2026, 10, 1, tzinfo=UTC)),
        _tehtava_dto("t-a2", "a", "ODOTTAA", datetime(2026, 10, 2, tzinfo=UTC)),
        _tehtava_dto("t-b1", "b", "ODOTTAA", datetime(2026, 10, 5, tzinfo=UTC)),
    ]
    arvio = Yhteenveto(taso=Kiireellisyys.KELTAINEN, saanto=Saantoloydokset(taso="keltainen"))
    tulokset = [
        TallennettuTulos(
            istunto_id="i1",
            tehtava_id="t-a1",
            loppu=PuhelunLoppu.VALMIS,
            paattyi=datetime(2026, 10, 6, tzinfo=UTC),
            yhteenveto=arvio,
        ),
        TallennettuTulos(
            istunto_id="i2",
            tehtava_id="t-a1",
            loppu=PuhelunLoppu.EI_VASTANNUT,
            paattyi=datetime(2026, 10, 7, tzinfo=UTC),
        ),
    ]

    rivit = potilasrivit(tehtavat, tulokset)

    assert [rivi.vanhus_id for rivi in rivit] == ["a", "b"]  # a:n puhelu 7.10. on tuorein
    a, b = rivit
    assert (a.tehtavia, a.avoimia, a.puheluita) == (2, 1, 2)
    assert a.viimeisin_puhelu == datetime(2026, 10, 7, tzinfo=UTC)
    assert a.viimeisin_taso == Kiireellisyys.KELTAINEN  # tavoittamaton ei kumoa arviota
    assert (b.puheluita, b.viimeisin_taso) == (0, None)


def test_potilaslista_ja_valikko(asiakas):
    client, _ = asiakas

    vastaus = client.get("/hoitaja/potilaat/")

    assert vastaus.status_code == 200
    assert 'href="/hoitaja/potilaat/vanhus-1"' in vastaus.text
    assert (
        'class="valikko-linkki valikko-linkki-aktiivinen" href="/hoitaja/potilaat/" '
        'aria-current="page"' in vastaus.text
    )
    jono = client.get("/hoitaja/")
    assert 'class="valikko-linkki" href="/hoitaja/potilaat/"' in jono.text


def test_potilassivulla_soittohistoria(asiakas):
    client, _ = asiakas

    vastaus = client.get("/hoitaja/potilaat/vanhus-1")

    assert "SOITTOHISTORIA" in vastaus.text
    assert "huimaus · lievä" in vastaus.text  # "pyörryttää" yhtenäistetty
    assert 'href="/hoitaja/tapaukset/t-1">Puhelun tiedot' in vastaus.text
    assert 'aria-label="Potilaan tietojen osiot"' in vastaus.text
    assert 'href="#perustiedot"' in vastaus.text
    assert 'href="#oirehistoria"' in vastaus.text
    assert 'href="#puhelut"' in vastaus.text
    assert 'href="#soittotehtavat"' in vastaus.text


# --- Nimet (vanhuksen_henkilotieto) ---


def test_nimi_korvaa_pseudonyymin_potilaslistalla_ja_sivulla(asiakas, sessiotehdas):
    client, _ = asiakas
    with sessiotehdas() as sessio:
        varasto.tallenna_henkilotieto(sessio, "vanhus-1", "Aino", "Virtanen", "010150-999X")

    lista = client.get("/hoitaja/potilaat/").text
    sivu = client.get("/hoitaja/potilaat/vanhus-1").text

    assert "Aino Virtanen" in lista
    assert "<h1>Aino Virtanen</h1>" in sivu
    assert "Henkilötunnus: 010150-999X" in sivu
    assert "Pseudonyymi" not in lista + sivu


def test_demotaytto_tayttaa_vain_puuttuvat(sessiotehdas):
    with sessiotehdas() as sessio:
        for tehtava_id, vanhus_id in (("t-a", "vanhus-a"), ("t-b", "vanhus-b")):
            sessio.add(
                PuhelutehtavaRivi(
                    id=tehtava_id,
                    vanhus_id=vanhus_id,
                    tila="ODOTTAA",
                    prioriteetti=1,
                    perusteet=[],
                    ika=80,
                    aktiivisia_laakkeita=1,
                    viitepaiva=VIITE,
                    yritykset=0,
                    kysymykset=[],
                    konteksti=Puhelukonteksti(
                        ika=80, aktiivisia_laakkeita=1, muutokset=[]
                    ).model_dump(mode="json"),
                )
            )
        sessio.commit()
        varasto.tallenna_henkilotieto(sessio, "vanhus-a", "Käsin", "Asetettu", None)

        taytetty = tayta_puuttuvat(sessio)
        tiedot = varasto.hae_henkilotiedot(sessio, ["vanhus-a", "vanhus-b"])

    assert taytetty == 1
    assert tiedot["vanhus-a"].etunimi == "Käsin"  # käsin asetettu säilyy
    assert tiedot["vanhus-b"].etunimi  # synteettinen nimi täytetty
