"""Muisti edellisestä puhelusta: tiivistys, haku, oireseuranta, agentin ohje ja triage."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mallit import (
    EdellinenPuhelu,
    Kiireellisyys,
    Kysymys,
    OireenSeurannanTila,
    Oireseuranta,
    OireYhteenvedossa,
    Puhelukonteksti,
    PuhelunLoppu,
    Saantoloydokset,
    Yhteenveto,
)
from app.muisti.muisti import hae_edellinen_puhelu, tiivista
from app.muisti.seuranta import luo_seurantakysymys, luokittele_seuranta
from app.puhelu.agentti import KiinteaAgentti
from app.puhelu.istunto import Vaihe, huolestuttava
from app.puhelu.keskustelu import Puheluistunto, kasittele
from app.puhelu.kysymyslista import varakysymys
from app.puhelu.tietopohja import Tietopohjahavainto, jarjestelmaohje
from app.puhelutehtavat.palvelu import hae_soittotiedot
from app.tietokanta import PuhelunTulosRivi, PuhelutehtavaRivi, hae_sessio
from app.triage.analyysi import analyysin_viestit
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
        paivia_sitten=7,
        taso="keltainen",
        oireet=["huimaus"],
        kuvaukset=["huimaus (kohtalainen)"],
        voimakkuudet={"huimaus": "kohtalainen"},
    )


def test_tiivistys_ilman_oireita_ei_muistettavaa():
    assert tiivista(_yhteenveto(oireet=()), NYT, NYT) is None


def test_tiivistys_muistaa_hyvan_laakekokemuksen_ilman_oireita():
    yhteenveto = _yhteenveto(oireet=(), taso=Kiireellisyys.VIHREA)
    yhteenveto.yleisvointi = "ihan hyvä"
    yhteenveto.laakkeen_kaytto = "kyllä"

    edellinen = tiivista(yhteenveto, NYT, NYT)

    assert edellinen is not None
    assert edellinen.oireet == []
    assert edellinen.yleisvointi == "ihan hyvä"
    assert edellinen.laakkeen_kaytto == "kyllä"


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
    paivia_sitten=7,
    taso="keltainen",
    oireet=["huimaus"],
    kuvaukset=["huimaus (kohtalainen)"],
    voimakkuudet={"huimaus": "kohtalainen"},
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


def test_istunto_lisaa_muistin_ensimmaiseksi_listakysymykseksi():
    istunto = _istunto(_EDELLINEN)

    assert [kysymys.tunniste for kysymys in istunto.kysymykset] == ["muisti:huimaus", "vointi"]
    assert "huimaus (kohtalainen)" in istunto.kysymykset[0].teksti


def test_hyvan_vain_lääkemuisti_muuttuu_vointikysymykseksi():
    edellinen = EdellinenPuhelu(
        paivia_sitten=1,
        taso="vihreä",
        yleisvointi="ihan hyvä",
        laakkeen_kaytto="kyllä",
    )

    kysymys = luo_seurantakysymys(edellinen)

    assert kysymys is not None
    assert kysymys.tunniste == "muisti:vointi"
    assert "lääkkeen käyttö oli sujunut hyvin" in kysymys.teksti
    assert "huimausta, pahoinvointia tai muita oireita" in kysymys.teksti


def test_lyhyt_seurantapuhelu_kysyy_muistin_avauksessa_ja_lopettaa_vastaukseen():
    istunto = Puheluistunto(
        istunto_id="seuranta",
        tehtava_id="t",
        kysymykset=[],
        konteksti=Puhelukonteksti(
            ika=82,
            aktiivisia_laakkeita=1,
            muutokset=[],
            edellinen_puhelu=_EDELLINEN,
        ),
        paivita_jono=False,
        seurantapuhelu=True,
    )

    avaus = asyncio.run(kasittele(istunto, None, None, KiinteaAgentti()))
    lopetus = asyncio.run(kasittele(istunto, "Se on nyt parempi", 0.9, KiinteaAgentti()))

    assert "Viime puhelussa" in " ".join(avaus.sanottavat)
    assert "huimaus" in " ".join(avaus.sanottavat)
    assert lopetus.kuuntele is False
    assert istunto.loppu == PuhelunLoppu.VALMIS
    assert istunto.oireseurannat[0].tila == OireenSeurannanTila.LIEVITTYNYT


def test_seurantakysymys_kysytaan_vain_kerran_ja_vastaus_liitetaan_oireeseen():
    istunto = _istunto(_EDELLINEN)
    istunto.vaihe = Vaihe.KESKUSTELU

    ensimmainen = varakysymys(istunto)
    seuraava = asyncio.run(kasittele(istunto, "Se on vähän parempi", 0.9, KiinteaAgentti()))

    assert "Viime puhelussa" in " ".join(ensimmainen.sanottavat)
    assert "Miten voit?" in " ".join(seuraava.sanottavat)
    assert sum("Viime puhelussa" in viesti["content"] for viesti in istunto.historia) == 1
    assert istunto.oireseurannat[0].tila == OireenSeurannanTila.LIEVITTYNYT


def test_mallin_uudelleen_muotoilema_seurantakysymys_tunnistetaan():
    class Malli:
        async def lammita(self, viestit):
            return None

        async def vastaa(self, viestit):
            return "Kiitos. Miten huimauksesi on nyt?"

    istunto = _istunto(_EDELLINEN)
    istunto.vaihe = Vaihe.KESKUSTELU

    kysymys = asyncio.run(kasittele(istunto, "Ihan hyvin", 0.9, Malli()))
    seuraava = asyncio.run(kasittele(istunto, "Se on ennallaan", 0.9, KiinteaAgentti()))

    assert "Miten huimauksesi on nyt?" in " ".join(kysymys.sanottavat)
    assert "Miten voit?" in " ".join(seuraava.sanottavat)
    assert istunto.oireseurannat[0].tila == OireenSeurannanTila.ENNALLAAN


def test_seurantakysymys_ei_sisalla_toisen_henkilon_tietoa(sessio):
    _lisaa_puhelu(sessio, "oma", "v1", NYT - timedelta(days=3), _yhteenveto(("huimaus",)))
    _lisaa_puhelu(sessio, "toinen", "v2", NYT - timedelta(days=1), _yhteenveto(("yskä",)))

    edellinen = hae_edellinen_puhelu(sessio, "v1", nyt=NYT, paivat=30)
    kysymys = luo_seurantakysymys(edellinen)

    assert kysymys is not None
    assert "huimaus" in kysymys.teksti
    assert "yskä" not in kysymys.teksti


@pytest.mark.parametrize(
    ("vastaus", "tila", "voimakkuus"),
    [
        ("Ei enää huimaa", OireenSeurannanTila.POISTUNUT, "ei tiedossa"),
        ("Se on vähän parempi", OireenSeurannanTila.LIEVITTYNYT, "lievä"),
        ("Se on ihan samanlaista", OireenSeurannanTila.ENNALLAAN, "ei tiedossa"),
        ("Nyt se on paljon pahempaa", OireenSeurannanTila.PAHENTUNUT, "ei tiedossa"),
        ("Ei se ole yhtään parempi", OireenSeurannanTila.ENNALLAAN, "ei tiedossa"),
        ("Ei se ole pahempi", OireenSeurannanTila.ENNALLAAN, "ei tiedossa"),
        ("En oikein osaa sanoa", OireenSeurannanTila.EPASELVA, "ei tiedossa"),
    ],
)
def test_oireseurannan_luokittelu(vastaus, tila, voimakkuus):
    kysymys = luo_seurantakysymys(_EDELLINEN)

    assert kysymys is not None
    seuranta = luokittele_seuranta(kysymys, vastaus, _EDELLINEN)

    assert seuranta.tila == tila
    assert seuranta.aiempi_voimakkuus == "kohtalainen"
    assert seuranta.nykyinen_voimakkuus == voimakkuus


# --- triage ---


def test_sama_oire_jatkuu_nostaa_keltaiseksi():
    havainnot = [Tietopohjahavainto(oireet=["huimaus"])]

    loydokset = saantoloydokset(
        havainnot, [], PuhelunLoppu.VALMIS, None, None, {}, edelliset_oireet=["huimaus"]
    )

    assert loydokset.taso == Kiireellisyys.KELTAINEN
    assert loydokset.toistuvat_oireet == ["huimaus"]
    assert any("edellisestä puhelusta" in peruste for peruste in loydokset.perusteet)


def test_uusi_oire_ei_ole_toistuva():
    havainnot = [Tietopohjahavainto(oireet=["yskä"])]

    loydokset = saantoloydokset(
        havainnot, [], PuhelunLoppu.VALMIS, None, None, {}, edelliset_oireet=["huimaus"]
    )

    assert loydokset.toistuvat_oireet == []


def test_istunnon_tulos_vertaa_edelliseen():
    istunto = _istunto(_EDELLINEN)
    istunto.tietohavainnot.append(Tietopohjahavainto(oireet=["huimaus"]))
    istunto.loppu = PuhelunLoppu.VALMIS

    tulos = istunto.tulos()

    assert tulos.yhteenveto is not None
    assert tulos.yhteenveto.saanto.toistuvat_oireet == ["huimaus"]
    assert tulos.yhteenveto.taso == Kiireellisyys.KELTAINEN


@pytest.mark.parametrize(
    ("tila", "odotettu_taso", "toistuva"),
    [
        (OireenSeurannanTila.POISTUNUT, Kiireellisyys.VIHREA, False),
        (OireenSeurannanTila.LIEVITTYNYT, Kiireellisyys.VIHREA, False),
        (OireenSeurannanTila.ENNALLAAN, Kiireellisyys.KELTAINEN, True),
        (OireenSeurannanTila.PAHENTUNUT, Kiireellisyys.KELTAINEN, True),
        (OireenSeurannanTila.EPASELVA, Kiireellisyys.KELTAINEN, False),
    ],
)
def test_oireseuranta_ohjaa_triagea(tila, odotettu_taso, toistuva):
    seuranta = Oireseuranta(oire="huimaus", tila=tila)

    loydokset = saantoloydokset(
        [],
        [],
        PuhelunLoppu.VALMIS,
        None,
        None,
        {},
        edelliset_oireet=["huimaus"],
        oireseurannat=[seuranta],
    )

    assert loydokset.taso == odotettu_taso
    assert ("huimaus" in loydokset.toistuvat_oireet) is toistuva
    assert loydokset.oireseurannat == [seuranta]


def test_lievittynyt_tunnistettu_oire_ei_yksin_nosta_triagea():
    seuranta = Oireseuranta(oire="huimaus", tila=OireenSeurannanTila.LIEVITTYNYT)

    loydokset = saantoloydokset(
        [Tietopohjahavainto(oireet=["huimaus"])],
        [],
        PuhelunLoppu.VALMIS,
        None,
        None,
        {},
        edelliset_oireet=["huimaus"],
        oireseurannat=[seuranta],
    )

    assert loydokset.taso == Kiireellisyys.VIHREA
    assert loydokset.toistuvat_oireet == []


def test_jatkuva_oire_valitsee_hoitajan_soiton_lopetusviestin():
    istunto = _istunto(_EDELLINEN)
    istunto.oireseurannat.append(Oireseuranta(oire="huimaus", tila=OireenSeurannanTila.ENNALLAAN))

    assert huolestuttava(istunto)


def test_jalkianalyysi_saa_rakenteisen_oireseurannan():
    seuranta = Oireseuranta(
        oire="huimaus",
        aiempi_voimakkuus="kohtalainen",
        nykyinen_voimakkuus="lievä",
        tila=OireenSeurannanTila.LIEVITTYNYT,
    )
    saanto = Saantoloydokset(taso=Kiireellisyys.VIHREA, oireseurannat=[seuranta])

    viestit = analyysin_viestit("Avustaja: Miten se on nyt?\nHenkilö: Vähän parempi.", saanto)

    assert '"oireseurannat"' in viestit[1]["content"]
    assert '"tila": "lievittynyt"' in viestit[1]["content"]


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
    nykyinen = _yhteenveto()
    nykyinen.saanto.oireseurannat = [
        Oireseuranta(
            oire="huimaus",
            aiempi_voimakkuus="kohtalainen",
            nykyinen_voimakkuus="lievä",
            tila=OireenSeurannanTila.LIEVITTYNYT,
        )
    ]
    with sessiotehdas() as sessio:
        _lisaa_puhelu(sessio, "eka", "v1", nyt - timedelta(days=4), _yhteenveto())
        _lisaa_puhelu(sessio, "toka", "v1", nyt - timedelta(hours=1), nykyinen)

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
    sisalto = " ".join(sivu.text.split())
    assert "kohtalainen → lievä" in sisalto
    assert "tila lievittynyt" in sisalto


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
