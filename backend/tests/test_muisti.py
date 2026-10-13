"""Muisti edellisestä puhelusta: tiivistys, haku kannasta, agentin ohje ja triage."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.mallit import (
    EdellinenPuhelu,
    Kiireellisyys,
    Kysymys,
    OireYhteenvedossa,
    Puhelukonteksti,
    PuhelunLoppu,
    Saantoloydokset,
    Yhteenveto,
)
from app.muisti.muisti import hae_edellinen_puhelu, tiivista
from app.puhelu.keskustelu import Puheluistunto
from app.puhelu.tietopohja import Tietopohjahavainto, jarjestelmaohje
from app.puhelutehtavat.palvelu import hae_soittotiedot
from app.tietokanta import PuhelunTulosRivi, PuhelutehtavaRivi, hae_sessio
from app.triage.saannot import saantoloydokset

NYT = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def _yhteenveto(
    oireet: tuple[str, ...] = ("huimaus",),
    voimakkuus: str = "kohtalainen",
    vakavat: tuple[str, ...] = (),
    taso: Kiireellisyys = Kiireellisyys.KELTAINEN,
) -> Yhteenveto:
    return Yhteenveto(
        taso=taso,
        saanto=Saantoloydokset(
            taso=taso, tunnistetut_oireet=list(oireet), vakavat_huolet=list(vakavat)
        ),
        oireet=[OireYhteenvedossa(oire=oire, voimakkuus=voimakkuus) for oire in oireet],
    )


def _lisaa_puhelu(
    sessio,
    istunto_id: str,
    vanhus_id: str,
    paattyi: datetime,
    yhteenveto: Yhteenveto | None,
    loppu: PuhelunLoppu = PuhelunLoppu.VALMIS,
) -> None:
    tehtava_id = f"tehtava-{istunto_id}"
    sessio.add(
        PuhelutehtavaRivi(
            id=tehtava_id,
            vanhus_id=vanhus_id,
            tila="VALMIS",
            prioriteetti=0,
            perusteet=[],
            ika=82,
            aktiivisia_laakkeita=1,
            viitepaiva=paattyi,
            yritykset=1,
            kysymykset=[],
            konteksti={"ika": 82, "aktiivisia_laakkeita": 1, "muutokset": []},
        )
    )
    sessio.add(
        PuhelunTulosRivi(
            istunto_id=istunto_id,
            tehtava_id=tehtava_id,
            loppu=loppu.value,
            paattyi=paattyi,
            yhteenveto=yhteenveto.model_dump(mode="json") if yhteenveto else None,
        )
    )
    sessio.commit()


# --- tiivistys (puhdas funktio) ---


def test_tiivistys_oireesta_ja_ajasta():
    edellinen = tiivista(_yhteenveto(), NYT - timedelta(days=7, hours=1), NYT)

    assert edellinen == EdellinenPuhelu(
        paivia_sitten=7, taso="keltainen", oireet=["huimaus"], kuvaukset=["huimaus (kohtalainen)"]
    )


def test_tiivistys_ilman_oireita_ei_muistettavaa():
    assert tiivista(_yhteenveto(oireet=()), NYT, NYT) is None


def test_vakava_huoli_ei_mene_agentille():
    yhteenveto = _yhteenveto(vakavat=("itsetuhoisuus",), taso=Kiireellisyys.PUNAINEN)

    assert tiivista(yhteenveto, NYT, NYT) is None


def test_tuntematon_voimakkuus_ei_nay_kuvauksessa():
    edellinen = tiivista(_yhteenveto(voimakkuus="ei tiedossa"), NYT, NYT)

    assert edellinen is not None
    assert edellinen.kuvaukset == ["huimaus"]


# --- haku kannasta ---


def test_uusin_saman_henkilon_puhelu(sessio):
    _lisaa_puhelu(sessio, "vanha", "v1", NYT - timedelta(days=20), _yhteenveto(("vatsavaivat",)))
    _lisaa_puhelu(sessio, "uusi", "v1", NYT - timedelta(days=3), _yhteenveto(("huimaus",)))
    _lisaa_puhelu(sessio, "toinen", "v2", NYT - timedelta(days=1), _yhteenveto(("yskä",)))

    edellinen = hae_edellinen_puhelu(sessio, "v1", nyt=NYT, paivat=30)

    assert edellinen is not None
    assert edellinen.oireet == ["huimaus"]
    assert edellinen.paivia_sitten == 3


def test_vastaamaton_puhelu_ohitetaan(sessio):
    _lisaa_puhelu(sessio, "keskustelu", "v1", NYT - timedelta(days=5), _yhteenveto())
    _lisaa_puhelu(
        sessio, "ei-vastannut", "v1", NYT - timedelta(days=1), None, PuhelunLoppu.EI_VASTANNUT
    )
    _lisaa_puhelu(
        sessio,
        "hiljainen",
        "v1",
        NYT - timedelta(hours=2),
        _yhteenveto(oireet=()),
        PuhelunLoppu.EI_VASTAUSTA,
    )

    edellinen = hae_edellinen_puhelu(sessio, "v1", nyt=NYT, paivat=30)

    assert edellinen is not None
    assert edellinen.paivia_sitten == 5


def test_viimeisin_keskustelu_ilman_oireita_ei_kaiva_vanhempaa(sessio):
    _lisaa_puhelu(sessio, "oire", "v1", NYT - timedelta(days=10), _yhteenveto())
    _lisaa_puhelu(sessio, "hyvin", "v1", NYT - timedelta(days=2), _yhteenveto(oireet=()))

    assert hae_edellinen_puhelu(sessio, "v1", nyt=NYT, paivat=30) is None


def test_sailytysraja(sessio):
    _lisaa_puhelu(sessio, "vanha", "v1", NYT - timedelta(days=40), _yhteenveto())

    assert hae_edellinen_puhelu(sessio, "v1", nyt=NYT, paivat=30) is None
    assert hae_edellinen_puhelu(sessio, "v1", nyt=NYT, paivat=0) is None


def test_ennen_rajaa_tehtavan_oman_puhelun(sessio):
    _lisaa_puhelu(sessio, "eka", "v1", NYT - timedelta(days=7), _yhteenveto(("huimaus",)))
    _lisaa_puhelu(sessio, "toka", "v1", NYT - timedelta(days=1), _yhteenveto(("yskä",)))

    edellinen = hae_edellinen_puhelu(
        sessio, "v1", nyt=NYT, ennen=NYT - timedelta(days=1), paivat=30
    )

    assert edellinen is not None
    assert edellinen.oireet == ["huimaus"]


# --- agentin ohje ---

_EDELLINEN = EdellinenPuhelu(
    paivia_sitten=7, taso="keltainen", oireet=["huimaus"], kuvaukset=["huimaus (kohtalainen)"]
)


def test_ohjeen_lopussa_edellinen_puhelu():
    ohje = jarjestelmaohje(82, ["uusi lääke"], ["Miten lääke on sopinut?"], _EDELLINEN)
    ilman = jarjestelmaohje(82, ["uusi lääke"], ["Miten lääke on sopinut?"])

    assert "EDELLINEN PUHELU" not in ilman
    # Yhteinen alku säilyy (mallin välimuisti), muisti on aivan lopussa.
    assert ohje.startswith(ilman)
    loppu = ohje[len(ilman) :]
    assert "7 päivää sitten" in loppu
    assert "huimaus (kohtalainen)" in loppu


def test_ohjeessa_ei_vakavia_tietoja_eika_tasoa():
    ohje = jarjestelmaohje(82, [], [], _EDELLINEN)

    assert "keltainen" not in ohje.split("EDELLINEN PUHELU")[1]


def _istunto(edellinen: EdellinenPuhelu | None) -> Puheluistunto:
    return Puheluistunto(
        istunto_id="i",
        tehtava_id="t",
        kysymykset=[Kysymys(tunniste="vointi", teksti="Miten voit?")],
        konteksti=Puhelukonteksti(
            ika=82, aktiivisia_laakkeita=1, muutokset=[], edellinen_puhelu=edellinen
        ),
        paivita_jono=False,
    )


def test_istunnon_ohje_kayttaa_muistia():
    assert "EDELLINEN PUHELU" in _istunto(_EDELLINEN).ohje()
    assert "EDELLINEN PUHELU" not in _istunto(None).ohje()


# --- triage ---


def test_sama_oire_jatkuu_nostaa_keltaiseksi():
    havainnot = [Tietopohjahavainto(oireet=["huimaus"])]

    loydokset = saantoloydokset(havainnot, [], PuhelunLoppu.VALMIS, None, None, ["huimaus"])

    assert loydokset.taso == Kiireellisyys.KELTAINEN
    assert loydokset.toistuvat_oireet == ["huimaus"]
    assert any("edellisestä puhelusta" in peruste for peruste in loydokset.perusteet)


def test_uusi_oire_ei_ole_toistuva():
    havainnot = [Tietopohjahavainto(oireet=["yskä"])]

    loydokset = saantoloydokset(havainnot, [], PuhelunLoppu.VALMIS, None, None, ["huimaus"])

    assert loydokset.toistuvat_oireet == []


def test_istunnon_tulos_vertaa_edelliseen():
    istunto = _istunto(_EDELLINEN)
    istunto.tietohavainnot.append(Tietopohjahavainto(oireet=["huimaus"]))
    istunto.loppu = PuhelunLoppu.VALMIS

    tulos = istunto.tulos()

    assert tulos.yhteenveto is not None
    assert tulos.yhteenveto.saanto.toistuvat_oireet == ["huimaus"]
    assert tulos.yhteenveto.taso == Kiireellisyys.KELTAINEN


# --- kytkennät: soittotiedot ja hoitajan näkymä ---


def _odottava_soitto(sessio, tehtava_id: str, vanhus_id: str) -> None:
    sessio.add(
        PuhelutehtavaRivi(
            id=tehtava_id,
            vanhus_id=vanhus_id,
            tila="SOITETAAN",
            prioriteetti=0,
            perusteet=[],
            ika=82,
            aktiivisia_laakkeita=1,
            viitepaiva=datetime.now(UTC),
            yritykset=1,
            kysymykset=[],
            konteksti={"ika": 82, "aktiivisia_laakkeita": 1, "muutokset": []},
        )
    )
    sessio.commit()


def test_soittotiedot_sisaltavat_edellisen_puhelun(sessio):
    nyt = datetime.now(UTC)
    _lisaa_puhelu(sessio, "eka", "v1", nyt - timedelta(days=4), _yhteenveto())
    _odottava_soitto(sessio, "uusi-soitto", "v1")

    tiedot = hae_soittotiedot(sessio, "uusi-soitto", oletusnumero="+358401111111")

    assert tiedot.konteksti.edellinen_puhelu is not None
    assert tiedot.konteksti.edellinen_puhelu.oireet == ["huimaus"]
    # Muistia ei tallenneta tehtävään: se haetaan aina soiton alkaessa.
    assert "edellinen_puhelu" not in sessio.get(PuhelutehtavaRivi, "uusi-soitto").konteksti


def test_hoitajan_tapaussivu_nayttaa_edellisen_puhelun(sessiotehdas):
    nyt = datetime.now(UTC)
    with sessiotehdas() as sessio:
        _lisaa_puhelu(sessio, "eka", "v1", nyt - timedelta(days=4), _yhteenveto())
        _lisaa_puhelu(sessio, "toka", "v1", nyt - timedelta(hours=1), _yhteenveto())

    def sessio_override():
        with sessiotehdas() as sessio:
            yield sessio

    app.dependency_overrides[hae_sessio] = sessio_override
    try:
        sivu = TestClient(app).get("/hoitaja/tapaukset/tehtava-toka")
    finally:
        app.dependency_overrides.clear()

    assert sivu.status_code == 200
    assert "Edellinen puhelu" in sivu.text
    assert "4 päivää sitten" in sivu.text


def test_ensimmaisen_puhelun_sivulla_ei_edellista(sessiotehdas):
    with sessiotehdas() as sessio:
        _lisaa_puhelu(sessio, "eka", "v1", datetime.now(UTC), _yhteenveto())

    def sessio_override():
        with sessiotehdas() as sessio:
            yield sessio

    app.dependency_overrides[hae_sessio] = sessio_override
    try:
        sivu = TestClient(app).get("/hoitaja/tapaukset/tehtava-eka")
    finally:
        app.dependency_overrides.clear()

    assert sivu.status_code == 200
    assert "Edellinen puhelu" not in sivu.text
