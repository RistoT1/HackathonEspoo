"""Karakterisointi: hoitajan jonon ja tapaussivun HTML sekä tallennetut rivit.

Tilannekuvat (tests/tilannekuvat/hoitajan_nakyma/) otettiin ennen tulosten luvun
yhtenäistämistä (13.10.): refaktorointi ei saa muuttaa hoitajan näkymää. Tarkoituksellinen
muutos näkymään: PAIVITA_TILANNEKUVAT=1 uv run pytest tests/test_hoitajan_nakyma.py
ja tarkista ero gitistä.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.mallit import (
    Kiireellisyys,
    Kysymys,
    Laake,
    Laakemuutos,
    Muutoskonteksti,
    MuutosTyyppi,
    OireYhteenvedossa,
    Puhelukonteksti,
    PuhelunLoppu,
    PuhelunTulos,
    Saantoloydokset,
    Yhteenveto,
)
from app.tietokanta import (
    PuhelunTulosRivi,
    PuhelutehtavanMuutosRivi,
    PuhelutehtavaRivi,
    hae_sessio,
    tallenna_puhelun_tulos,
)

TILANNEKUVAT = Path(__file__).resolve().parent / "tilannekuvat" / "hoitajan_nakyma"
PAIVITA = os.environ.get("PAIVITA_TILANNEKUVAT") == "1"
K = Kiireellisyys
VIITE = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
PAATTYI = datetime(2026, 10, 12, 14, 5, tzinfo=UTC)
KATSOJA = "hackathon-demo (kirjautumaton)"

TEHTAVAT = (
    "t1-ei-tulosta",
    "t2-saantoarvio",
    "t3-analyysi-valmis",
    "t4-varayhteenveto",
    "t5-kaksi-tulosta",
    "t6-json-null",
    "t7-paattyi-null",
    "t8-aika-tuntematon",
    "t9-peruttu",
)


def _laakemuutos(tyyppi: MuutosTyyppi, nimi: str, paivia_sitten: int) -> Laakemuutos:
    tapahtui = VIITE - timedelta(days=paivia_sitten)
    laake = Laake(
        koodi=f"koodi-{nimi}",
        nimi=nimi,
        alkoi=tapahtui,
        paattyi=None,
        syy=None,
        atc=None,
        kaytossa=tyyppi != MuutosTyyppi.LOPETETTU_LAAKE,
    )
    return Laakemuutos(
        tyyppi=tyyppi,
        laake=laake,
        tapahtui=tapahtui,
        paivia_sitten=paivia_sitten,
        kuvaus=f"Synteettinen muutos: {nimi}",
    )


def _tehtava(
    sessio: Session,
    tehtava_id: str,
    tila: str,
    prioriteetti: int,
    yritykset: int = 0,
    muutokset: tuple[Laakemuutos, ...] = (),
    kysymykset: tuple[Kysymys, ...] = (),
    perusteet: tuple[str, ...] = (),
) -> None:
    luotu = VIITE + timedelta(minutes=prioriteetti)
    konteksti = Puhelukonteksti(
        ika=81,
        aktiivisia_laakkeita=len(muutokset) + 2,
        muutokset=[
            Muutoskonteksti(tyyppi=m.tyyppi, laakeryhma=None, paivia_sitten=m.paivia_sitten)
            for m in muutokset
        ],
    )
    rivi = PuhelutehtavaRivi(
        id=tehtava_id,
        vanhus_id=f"vanhus-{tehtava_id}",
        tila=tila,
        prioriteetti=prioriteetti,
        perusteet=list(perusteet),
        ika=81,
        aktiivisia_laakkeita=len(muutokset) + 2,
        viitepaiva=VIITE,
        yritykset=yritykset,
        kysymykset=[k.model_dump(mode="json") for k in kysymykset],
        konteksti=konteksti.model_dump(mode="json"),
        luotu=luotu,
        paivitetty=luotu + timedelta(hours=1),
    )
    for indeksi, muutos in enumerate(muutokset):
        rivi.muutokset.append(
            PuhelutehtavanMuutosRivi(
                muutosavain=f"{tehtava_id}-{indeksi}",
                tyyppi=muutos.tyyppi.value,
                tapahtui=muutos.tapahtui,
                tiedot=muutos.model_dump(mode="json"),
            )
        )
    sessio.add(rivi)


def _tulos(
    sessio: Session,
    istunto_id: str,
    tehtava_id: str,
    loppu: str | None,
    paattyi: datetime | None,
    yhteenveto: Yhteenveto | None,
) -> None:
    sessio.add(
        PuhelunTulosRivi(
            istunto_id=istunto_id,
            tehtava_id=tehtava_id,
            loppu=loppu,
            paattyi=paattyi,
            yhteenveto=yhteenveto.model_dump(mode="json") if yhteenveto is not None else None,
        )
    )


def _saantoarvio(taso: Kiireellisyys) -> Yhteenveto:
    """Sääntöjen yhteenveto heti puhelun jälkeen (analyysi kesken)."""
    saanto = Saantoloydokset(
        taso=taso,
        perusteet=["Henkilö kertoi huimauksesta uuden lääkkeen jälkeen."],
        tunnistetut_oireet=["huimaus"],
        huolen_ilmaukset=["huolestuttaa"],
    )
    return Yhteenveto(
        taso=taso,
        saanto=saanto,
        oireet=[OireYhteenvedossa(oire="huimaus")],
        tunnetila="huolestunut",
        huolet=["henkilö ilmaisi huolta"],
        perustelu="Henkilö kertoi huimauksesta uuden lääkkeen jälkeen.",
    )


def _valmis_analyysi() -> Yhteenveto:
    saanto = Saantoloydokset(
        taso=K.PUNAINEN,
        perusteet=["Hätämerkki: rintakipu."],
        hatamerkit=["rintakipu"],
        vakavat_huolet=["yksin kotona"],
    )
    return Yhteenveto(
        taso=K.PUNAINEN,
        saanto=saanto,
        mallin_taso=K.KELTAINEN,
        analyysi_valmis=True,
        yleisvointi="Väsynyt, rintaa puristaa ajoittain.",
        oireet=[
            OireYhteenvedossa(
                oire="rintakipu",
                voimakkuus="kohtalainen",
                alkoi_laakkeen_jalkeen="kyllä",
                tilanne="Rasituksessa, menee levossa ohi.",
                varmuus="selvä",
            ),
            OireYhteenvedossa(oire="väsymys", voimakkuus="lievä"),
        ],
        turvallisuushuolet=["rintakipu", "yksin kotona"],
        tunnetila="huolestunut",
        annetut_ohjeet=["Ohjattiin soittamaan 112, jos kipu pahenee."],
        laakkeen_kaytto="ei",
        laakkeen_huomiot="Unohtanut iltalääkkeen kahdesti.",
        huolet=["Asuu yksin", "Ei jaksa käydä kaupassa"],
        muuta_hoitajalle="Tytär & naapuri <käyvät> viikonloppuisin.",
        perustelu="Rintakipu on hätämerkki; sääntö nosti punaiseksi.",
    )


def _varayhteenveto() -> Yhteenveto:
    saanto = Saantoloydokset(taso=K.VIHREA)
    return Yhteenveto(
        taso=K.KELTAINEN,
        saanto=saanto,
        analyysi_valmis=True,
        varayhteenveto=True,
        laakkeen_kaytto="kyllä",
        muuta_hoitajalle="Automaattinen analyysi ei onnistunut. Soita ja tarkista tilanne.",
        perustelu="Säännöt eivät löytäneet huolestuttavaa.",
    )


def _analyysi(taso: Kiireellisyys, yleisvointi: str) -> Yhteenveto:
    return Yhteenveto(
        taso=taso,
        saanto=Saantoloydokset(taso=taso),
        mallin_taso=taso,
        analyysi_valmis=True,
        yleisvointi=yleisvointi,
    )


@pytest.fixture
def aineisto(sessiotehdas: sessionmaker[Session]) -> None:
    kysymykset = (
        Kysymys(tunniste="UUSI_LAAKE", teksti="Oletko aloittanut uuden lääkkeen?"),
        Kysymys(tunniste="tarkennus.huimaus", teksti="Onko huimannut?"),
    )
    with sessiotehdas() as sessio:
        _tehtava(
            sessio,
            "t1-ei-tulosta",
            "ODOTTAA",
            90,
            muutokset=(
                _laakemuutos(MuutosTyyppi.UUSI_LAAKE, "Esimerkkivalmiste A", 3),
                _laakemuutos(MuutosTyyppi.LOPETETTU_LAAKE, "Esimerkkivalmiste B", 10),
                _laakemuutos(MuutosTyyppi.LYHYT_KUURI, "Esimerkkivalmiste C", 20),
            ),
            kysymykset=kysymykset,
            perusteet=("Uusi lääke 3 pv sitten", "Ikä yli 80"),
        )
        _tehtava(
            sessio,
            "t2-saantoarvio",
            "VALMIS",
            80,
            yritykset=1,
            muutokset=(_laakemuutos(MuutosTyyppi.ANNOSMUUTOS, "Esimerkkivalmiste D", 5),),
            kysymykset=kysymykset,
        )
        _tehtava(sessio, "t3-analyysi-valmis", "VALMIS", 70, yritykset=1)
        _tehtava(sessio, "t4-varayhteenveto", "VALMIS", 60, yritykset=1)
        _tehtava(sessio, "t5-kaksi-tulosta", "EI_VASTANNUT", 50, yritykset=3)
        _tehtava(sessio, "t6-json-null", "EI_VASTANNUT", 40, yritykset=1)
        _tehtava(sessio, "t7-paattyi-null", "VALMIS", 30, yritykset=2)
        _tehtava(sessio, "t8-aika-tuntematon", "SOITETAAN", 20, yritykset=1)
        _tehtava(sessio, "t9-peruttu", "PERUTTU", 10)
        sessio.flush()

        _tulos(sessio, "i2", "t2-saantoarvio", "VALMIS", PAATTYI, _saantoarvio(K.KELTAINEN))
        _tulos(sessio, "i3", "t3-analyysi-valmis", "HALUAA_HOITAJAN", PAATTYI, _valmis_analyysi())
        _tulos(sessio, "i4", "t4-varayhteenveto", "EI_VASTAUSTA", PAATTYI, _varayhteenveto())
        _tulos(
            sessio,
            "i5-vanha",
            "t5-kaksi-tulosta",
            "VALMIS",
            PAATTYI - timedelta(days=1),
            _analyysi(K.PUNAINEN, "Vanha puhelu: rintakipua."),
        )
        _tulos(
            sessio,
            "i5-uusi",
            "t5-kaksi-tulosta",
            "KESKEYTYI",
            PAATTYI,
            _saantoarvio(K.KELTAINEN),
        )
        _tulos(sessio, "i6", "t6-json-null", "EI_VASTANNUT", PAATTYI, None)
        _tulos(
            sessio,
            "i7-ilman-aikaa",
            "t7-paattyi-null",
            "VALMIS",
            None,
            _analyysi(K.PUNAINEN, "Päättymisaika puuttuu: vanhin."),
        )
        _tulos(
            sessio,
            "i7-ajallinen",
            "t7-paattyi-null",
            "VALMIS",
            PAATTYI - timedelta(hours=2),
            _analyysi(K.VIHREA, "Vointi hyvä."),
        )
        _tulos(sessio, "i8", "t8-aika-tuntematon", None, None, _saantoarvio(K.KELTAINEN))
        sessio.commit()


@pytest.fixture
def client(sessiotehdas: sessionmaker[Session], aineisto: None):
    def sessio_override():
        with sessiotehdas() as sessio:
            yield sessio

    app.dependency_overrides[hae_sessio] = sessio_override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _vertaa(nimi: str, html: str) -> None:
    tiedosto = TILANNEKUVAT / f"{nimi}.html"
    if PAIVITA:
        tiedosto.parent.mkdir(parents=True, exist_ok=True)
        tiedosto.write_text(html, encoding="utf-8")
        return
    assert html == tiedosto.read_text(encoding="utf-8"), f"Näkymä muuttui: {tiedosto.name}"


@pytest.mark.parametrize("tila", ["", "VALMIS", "EI_VASTANNUT"])
def test_jono_ennallaan(client: TestClient, tila: str) -> None:
    vastaus = client.get("/hoitaja/", params={"tila": tila} if tila else None)

    assert vastaus.status_code == 200
    _vertaa(f"jono{'-' + tila.lower() if tila else ''}", vastaus.text)


@pytest.mark.parametrize("tehtava_id", TEHTAVAT)
def test_tapaussivu_ennallaan(client: TestClient, tehtava_id: str) -> None:
    vastaus = client.get(f"/hoitaja/tapaukset/{tehtava_id}")

    assert vastaus.status_code == 200
    _vertaa(f"tapaus-{tehtava_id}", vastaus.text)


def test_tapaussivun_katselu_kirjataan_lokiin(
    client: TestClient, sessiotehdas: sessionmaker[Session]
) -> None:
    for tehtava_id in ("t3-analyysi-valmis", "t1-ei-tulosta", "t3-analyysi-valmis"):
        assert client.get(f"/hoitaja/tapaukset/{tehtava_id}").status_code == 200
    assert client.get("/hoitaja/tapaukset/ei-ole").status_code == 404
    assert client.get("/hoitaja/").status_code == 200

    with sessiotehdas() as sessio:
        rivit = sessio.execute(
            text("SELECT id, tehtava_id, katsoja, toiminto, aika FROM katseluloki ORDER BY id")
        ).all()
    assert [tuple(rivi[:4]) for rivi in rivit] == [
        (1, "t3-analyysi-valmis", KATSOJA, "katselu"),
        (2, "t1-ei-tulosta", KATSOJA, "katselu"),
        (3, "t3-analyysi-valmis", KATSOJA, "katselu"),
    ]
    assert all(rivi.aika is not None for rivi in rivit)


def _json_auki(rivi) -> dict:
    return {**rivi, "yhteenveto": json.loads(rivi["yhteenveto"])}


def test_tallennettu_tulos_rivina(sessiotehdas: sessionmaker[Session]) -> None:
    """Kantaan tallentuva muoto: lisäys, päivitys (triage-analyysi) ja JSON-null."""
    with sessiotehdas() as sessio:
        _tehtava(sessio, "t-tallennus", "SOITETAAN", 1)
        sessio.commit()
    saanto = _saantoarvio(K.KELTAINEN)
    tulos = PuhelunTulos(
        istunto_id="i-tallennus",
        tehtava_id="t-tallennus",
        loppu=PuhelunLoppu.HATAMERKKI,
        hatamerkki="rintakipu",
        paattyi=PAATTYI,
        yhteenveto=saanto,
    )

    with sessiotehdas() as sessio:
        tallenna_puhelun_tulos(sessio, tulos)
    with sessiotehdas() as sessio:
        lisatty = sessio.execute(text("SELECT * FROM puhelun_tulos")).mappings().all()
    paivitetty_tulos = tulos.model_copy(update={"yhteenveto": _valmis_analyysi()})
    with sessiotehdas() as sessio:
        tallenna_puhelun_tulos(sessio, paivitetty_tulos)
    with sessiotehdas() as sessio:
        paivitetty = sessio.execute(text("SELECT * FROM puhelun_tulos")).mappings().all()
    ilman_yhteenvetoa = tulos.model_copy(update={"istunto_id": "i-null", "yhteenveto": None})
    with sessiotehdas() as sessio:
        tallenna_puhelun_tulos(sessio, ilman_yhteenvetoa)
    with sessiotehdas() as sessio:
        null_rivi = (
            sessio.execute(text("SELECT * FROM puhelun_tulos WHERE istunto_id = 'i-null'"))
            .mappings()
            .one()
        )

    assert [_json_auki(rivi) for rivi in lisatty] == [
        {
            "istunto_id": "i-tallennus",
            "tehtava_id": "t-tallennus",
            "loppu": "HATAMERKKI",
            "paattyi": "2026-10-12 14:05:00.000000",
            "yhteenveto": saanto.model_dump(mode="json"),
        }
    ]
    assert [_json_auki(rivi) for rivi in paivitetty] == [
        {
            "istunto_id": "i-tallennus",
            "tehtava_id": "t-tallennus",
            "loppu": "HATAMERKKI",
            "paattyi": "2026-10-12 14:05:00.000000",
            "yhteenveto": _valmis_analyysi().model_dump(mode="json"),
        }
    ]
    # Python None tallentuu JSON-nulliksi, ei SQL NULLiksi (hoitajan näkymä käsittelee molemmat).
    assert null_rivi["yhteenveto"] == "null"
