"""Dev-testimuutokset muistissa toimivalla feikilla: muutos kantaan -> tunnistus huomaa sen."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, hae_asetukset
from app.data.adapteri import hae_datalahde, hae_testikirjoittaja
from app.dev.palvelu import TestimuutosVirhe, tee_testimuutos
from app.main import app
from app.mallit import Laakejakso, MuutosTyyppi, Testimuutos, Vanhus

VIITE = datetime(2020, 4, 1, tzinfo=UTC)
VANHUS_ID = "v-1"


class MuistiLahde:
    """Toteuttaa seka Datalahde- etta Testikirjoittaja-rajapinnan samaan muistiin."""

    def __init__(self) -> None:
        self.vanhus = Vanhus(
            vanhus_id=VANHUS_ID, syntymapaiva=date(1940, 1, 1), kuolinpaiva=None, sukupuoli="F"
        )
        self.jaksot = [
            Laakejakso(
                koodi="1",
                nimi="lisinopril 10 MG Oral Tablet",
                alkoi=VIITE - timedelta(days=400),
                paattyi=None,
                syy=None,
            )
        ]

    # Datalahde
    def uusin_paiva(self) -> datetime:
        return VIITE

    def hae_vanhukset(self) -> list[Vanhus]:
        return [self.vanhus]

    def hae_vanhus(self, vanhus_id: str) -> Vanhus | None:
        return self.vanhus if vanhus_id == VANHUS_ID else None

    def hae_laakejaksot(self, vanhus_id: str) -> list[Laakejakso]:
        return list(self.jaksot)

    def hae_laakejaksot_monelle(self, vanhus_idt: list[str]) -> dict[str, list[Laakejakso]]:
        return {vanhus_id: self.hae_laakejaksot(vanhus_id) for vanhus_id in vanhus_idt}

    # Testikirjoittaja
    def lisaa_laake(self, vanhus_id: str, koodi: str, nimi: str, alkoi: datetime) -> None:
        self.jaksot.append(Laakejakso(koodi=koodi, nimi=nimi, alkoi=alkoi, paattyi=None, syy=None))

    def lopeta_laake(
        self, vanhus_id: str, koodi: str, paattyi: datetime, viitepaiva: datetime
    ) -> bool:
        for i, jakso in enumerate(self.jaksot):
            kaytossa = jakso.paattyi is None or jakso.paattyi > viitepaiva
            if jakso.koodi == koodi and kaytossa:
                self.jaksot[i] = jakso.model_copy(update={"paattyi": paattyi})
                return True
        return False

    def poista_testimuutokset(self) -> tuple[int, int]:
        return (0, 0)


def tyypit(tulos) -> list[MuutosTyyppi]:
    return [muutos.tyyppi for muutos in tulos.muutokset]


# --- Palvelu ---


def test_uusi_laake_tunnistetaan():
    lahde = MuistiLahde()
    pyynto = Testimuutos(
        vanhus_id=VANHUS_ID, tyyppi=MuutosTyyppi.UUSI_LAAKE, nimi="Furosemide 40 MG Oral Tablet"
    )

    tulos = tee_testimuutos(lahde, lahde, pyynto, VIITE, 90)

    assert tyypit(tulos) == [MuutosTyyppi.UUSI_LAAKE]
    assert tulos.muutokset[0].paivia_sitten == 3


def test_lopetus_tunnistetaan():
    lahde = MuistiLahde()
    pyynto = Testimuutos(
        vanhus_id=VANHUS_ID, tyyppi=MuutosTyyppi.LOPETETTU_LAAKE, lopetettava_koodi="1"
    )

    tulos = tee_testimuutos(lahde, lahde, pyynto, VIITE, 90)

    assert tyypit(tulos) == [MuutosTyyppi.LOPETETTU_LAAKE]
    assert tulos.aktiiviset_laakkeet == []


def test_annosmuutos_tunnistetaan():
    lahde = MuistiLahde()
    pyynto = Testimuutos(
        vanhus_id=VANHUS_ID,
        tyyppi=MuutosTyyppi.ANNOSMUUTOS,
        lopetettava_koodi="1",
        nimi="lisinopril 20 MG Oral Tablet",
    )

    tulos = tee_testimuutos(lahde, lahde, pyynto, VIITE, 90)

    assert tyypit(tulos) == [MuutosTyyppi.ANNOSMUUTOS]
    assert tulos.muutokset[0].aiempi_laake.koodi == "1"


def test_kaytossa_olematonta_ei_voi_lopettaa():
    lahde = MuistiLahde()
    pyynto = Testimuutos(
        vanhus_id=VANHUS_ID, tyyppi=MuutosTyyppi.LOPETETTU_LAAKE, lopetettava_koodi="999"
    )

    with pytest.raises(TestimuutosVirhe):
        tee_testimuutos(lahde, lahde, pyynto, VIITE, 90)


def test_tuntematon_vanhus_palauttaa_none():
    lahde = MuistiLahde()
    pyynto = Testimuutos(vanhus_id="ei-ole", tyyppi=MuutosTyyppi.UUSI_LAAKE, nimi="X 1 MG")

    assert tee_testimuutos(lahde, lahde, pyynto, VIITE, 90) is None


def test_lyhytta_kuuria_ei_voi_tehda_testimuutoksena():
    with pytest.raises(ValueError):
        Testimuutos(vanhus_id=VANHUS_ID, tyyppi=MuutosTyyppi.LYHYT_KUURI, nimi="X 1 MG")


def test_pyynto_vaatii_tyypin_kentat():
    with pytest.raises(ValueError):
        Testimuutos(vanhus_id=VANHUS_ID, tyyppi=MuutosTyyppi.UUSI_LAAKE)
    with pytest.raises(ValueError):
        Testimuutos(vanhus_id=VANHUS_ID, tyyppi=MuutosTyyppi.LOPETETTU_LAAKE)
    with pytest.raises(ValueError):
        Testimuutos(vanhus_id=VANHUS_ID, tyyppi=MuutosTyyppi.ANNOSMUUTOS, nimi="X 1 MG")


# --- Reitit ---


def _client(dev_tila: bool) -> TestClient:
    lahde = MuistiLahde()
    app.dependency_overrides[hae_datalahde] = lambda: lahde
    app.dependency_overrides[hae_testikirjoittaja] = lambda: lahde
    app.dependency_overrides[hae_asetukset] = lambda: Settings(
        database_url="postgresql+psycopg://ei-kaytossa", dev_tila=dev_tila
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def tyhjenna_overridet():
    yield
    app.dependency_overrides.clear()


def test_dev_reitit_pois_paalta_oletuksena():
    client = _client(dev_tila=False)

    vastaus = client.post(
        "/dev/testimuutos",
        json={"vanhus_id": VANHUS_ID, "tyyppi": "UUSI_LAAKE", "nimi": "X 1 MG"},
    )

    assert vastaus.status_code == 404


def test_dev_reitti_tekee_muutoksen_ja_nayttaa_tunnistuksen():
    client = _client(dev_tila=True)

    vastaus = client.post(
        "/dev/testimuutos",
        json={"vanhus_id": VANHUS_ID, "tyyppi": "UUSI_LAAKE", "nimi": "X 1 MG"},
    )

    assert vastaus.status_code == 200
    assert [m["tyyppi"] for m in vastaus.json()["muutokset"]] == ["UUSI_LAAKE"]


def test_dev_reitti_virheellinen_lopetus_on_400():
    client = _client(dev_tila=True)

    vastaus = client.post(
        "/dev/testimuutos",
        json={"vanhus_id": VANHUS_ID, "tyyppi": "LOPETETTU_LAAKE", "lopetettava_koodi": "999"},
    )

    assert vastaus.status_code == 400


def test_dev_nollaus():
    client = _client(dev_tila=True)

    vastaus = client.delete("/dev/testimuutokset")

    assert vastaus.status_code == 200
