"""Reitit feikkidatalahteella: ei kantaa. Feikki toteuttaa Datalahde-rajapinnan."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.data.adapteri import hae_datalahde
from app.main import app
from app.mallit import Laakejakso, Vanhus

UUSIN = datetime(2020, 4, 1, tzinfo=UTC)

VANHUKSET = {
    "v-vanha": Vanhus(
        vanhus_id="v-vanha", syntymapaiva=date(1940, 1, 1), kuolinpaiva=None, sukupuoli="F"
    ),
    "v-nuori": Vanhus(
        vanhus_id="v-nuori", syntymapaiva=date(1990, 1, 1), kuolinpaiva=None, sukupuoli="M"
    ),
    "v-kuollut": Vanhus(
        vanhus_id="v-kuollut",
        syntymapaiva=date(1930, 1, 1),
        kuolinpaiva=date(2019, 1, 1),
        sukupuoli="M",
    ),
}

JAKSOT = {
    "v-vanha": [
        Laakejakso(
            koodi="1",
            nimi="lisinopril 10 MG Oral Tablet",
            alkoi=datetime(2020, 3, 1, tzinfo=UTC),
            paattyi=None,
            syy="Hypertension",
        )
    ],
    "v-nuori": [
        Laakejakso(
            koodi="2",
            nimi="Acetaminophen 325 MG Oral Tablet",
            alkoi=datetime(2020, 3, 1, tzinfo=UTC),
            paattyi=None,
            syy=None,
        )
    ],
}


class FeikkiDatalahde:
    def uusin_paiva(self) -> datetime:
        return UUSIN

    def hae_vanhukset(self) -> list[Vanhus]:
        return list(VANHUKSET.values())

    def hae_vanhus(self, vanhus_id: str) -> Vanhus | None:
        return VANHUKSET.get(vanhus_id)

    def hae_laakejaksot(self, vanhus_id: str) -> list[Laakejakso]:
        return JAKSOT.get(vanhus_id, [])

    def hae_laakejaksot_monelle(self, vanhus_idt: list[str]) -> dict[str, list[Laakejakso]]:
        return {vanhus_id: JAKSOT.get(vanhus_id, []) for vanhus_id in vanhus_idt}


@pytest.fixture
def client():
    app.dependency_overrides[hae_datalahde] = FeikkiDatalahde
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_vanhukset_suodattaa_ian_ja_kuolleet(client):
    vastaus = client.get("/potilaat", params={"min_ika": 65})

    assert vastaus.status_code == 200
    assert [v["vanhus_id"] for v in vastaus.json()] == ["v-vanha"]


def test_vanhukset_ei_palauta_henkilotietoja(client):
    vanhus = client.get("/potilaat").json()[0]

    assert set(vanhus) == {"vanhus_id", "ika", "sukupuoli"}


def test_potilaan_laakkeet(client):
    vastaus = client.get("/potilaat/v-vanha/laakkeet")

    assert vastaus.status_code == 200
    assert vastaus.json()[0]["kaytossa"] is True


def test_potilaan_laakemuutokset_oletusviitepaiva(client):
    vastaus = client.get("/potilaat/v-vanha/laakemuutokset")

    data = vastaus.json()
    assert vastaus.status_code == 200
    assert data["vanhus_id"] == "v-vanha"
    assert data["ikkuna_paivia"] == 90
    assert data["viitepaiva"].startswith("2020-04-01")
    assert [m["tyyppi"] for m in data["muutokset"]] == ["UUSI_LAAKE"]
    assert len(data["aktiiviset_laakkeet"]) == 1


def test_potilaan_laakemuutokset_annettu_viitepaiva(client):
    vastaus = client.get(
        "/potilaat/v-vanha/laakemuutokset", params={"viitepaiva": "2019-01-01T00:00:00"}
    )

    assert vastaus.json()["muutokset"] == []


def test_tuntematon_vanhus_on_404(client):
    assert client.get("/potilaat/ei-ole/laakemuutokset").status_code == 404
    assert client.get("/potilaat/ei-ole/laakkeet").status_code == 404


def test_kaikki_laakemuutokset_vain_vanhukset_joilla_muutoksia(client):
    vastaus = client.get("/laakemuutokset", params={"min_ika": 65})

    assert vastaus.status_code == 200
    assert [p["vanhus_id"] for p in vastaus.json()] == ["v-vanha"]


def test_synthean_atc_tiedosto_on_validi():
    import json
    import re
    from pathlib import Path

    tiedosto = Path(__file__).resolve().parents[1] / "app" / "data" / "synthea_atc.json"
    vastaavuus = json.loads(tiedosto.read_text(encoding="utf-8"))["atc"]

    muoto = re.compile(r"[A-Z]\d{2}[A-Z]{2}(\d{2})?")
    assert vastaavuus
    assert all(atc is None or muoto.fullmatch(atc) for atc in vastaavuus.values())


def test_tyhja_data_on_selkea_503():
    from app.data.adapteri import DataEiSaatavilla

    class TyhjaLahde(FeikkiDatalahde):
        def uusin_paiva(self) -> datetime:
            raise DataEiSaatavilla("Lääkedataa ei ole")

    app.dependency_overrides[hae_datalahde] = TyhjaLahde
    try:
        vastaus = TestClient(app).get("/laakemuutokset")
    finally:
        app.dependency_overrides.clear()

    assert vastaus.status_code == 503
    assert "Lääkedataa ei ole" in vastaus.json()["detail"]


def test_env_polku_ei_riipu_tyohakemistosta():
    from pathlib import Path

    from app.config import Settings

    polut = Settings.model_config["env_file"]
    assert all(Path(polku).is_absolute() for polku in polut)
