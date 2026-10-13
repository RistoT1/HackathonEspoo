"""Tapaussivun oirehistoria (hoitaja/historia.py) puhtaina funktioina ja tapaussivun reitti.
Kaavion piirto on static/oirehistoria.js:ssä; tässä testataan sen saama data."""

import json
import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.hoitaja import historia
from app.main import app
from app.mallit import (
    Kiireellisyys,
    OireenSeurannanTila,
    Oireseuranta,
    OireYhteenvedossa,
    Puhelukonteksti,
    PuhelunLoppu,
    Saantoloydokset,
    TallennettuTulos,
    Yhteenveto,
)
from app.tietokanta import PuhelunTulosRivi, PuhelutehtavaRivi, hae_sessio

K = Kiireellisyys


def _yhteenveto(
    taso: Kiireellisyys,
    oireet: tuple[tuple[str, str], ...] = (),
    tunnistetut: tuple[str, ...] = (),
    seurannat: tuple[Oireseuranta, ...] = (),
) -> Yhteenveto:
    saanto = Saantoloydokset(
        taso=taso, tunnistetut_oireet=list(tunnistetut), oireseurannat=list(seurannat)
    )
    return Yhteenveto(
        taso=taso,
        saanto=saanto,
        oireet=[OireYhteenvedossa(oire=o, voimakkuus=v) for o, v in oireet],
    )


def _tulos(
    tehtava_id: str,
    paattyi: datetime | None,
    yhteenveto: Yhteenveto | None,
    loppu: PuhelunLoppu = PuhelunLoppu.VALMIS,
) -> TallennettuTulos:
    return TallennettuTulos(
        istunto_id=f"i-{paattyi}",
        tehtava_id=tehtava_id,
        loppu=loppu,
        paattyi=paattyi,
        yhteenveto=yhteenveto,
    )


def _aika(paiva: int, tunti: int = 10) -> datetime:
    return datetime(2026, 10, paiva, tunti, 0, tzinfo=UTC)


# --- Yhtenäistys ---


@pytest.mark.parametrize(
    ("mallin_sanat", "oire"),
    [
        ("tosi vaikeat päänkiput", "päänsärky"),
        ("pyörryttää", "huimaus"),
        ("ahdistaa", "alakulo ja yksinäisyys"),
        ("Hirvittävä  darra", "hirvittävä darra"),  # ei tietopohjassa: mallin sanoin
    ],
)
def test_mallin_oirenimi_yhtenaistetaan_tietopohjan_oireeksi(mallin_sanat, oire):
    assert historia.yhtenaista(mallin_sanat) == oire


def test_saman_puhelun_samat_oireet_yhdistetaan_voimakkaimpaan():
    yhteenveto = _yhteenveto(
        K.KELTAINEN, (("päänsärky", "lievä"), ("tosi vaikeat päänkiput", "voimakas"))
    )

    merkinnat = historia.oiremerkinnat(yhteenveto)

    assert len(merkinnat) == 1
    assert merkinnat[0].oire == "päänsärky"
    assert merkinnat[0].voimakkuus == "voimakas"
    assert merkinnat[0].kuvaukset == ["päänsärky", "tosi vaikeat päänkiput"]


def test_ilman_analyysia_saantojen_oireet_ja_poistunut_seurannasta():
    seuranta = Oireseuranta(oire="huimaus", tila=OireenSeurannanTila.POISTUNUT)
    yhteenveto = _yhteenveto(K.VIHREA, tunnistetut=("väsymys",), seurannat=(seuranta,))

    merkinnat = historia.oiremerkinnat(yhteenveto)

    assert [(m.oire, m.voimakkuus) for m in merkinnat] == [
        ("väsymys", "ei tiedossa"),
        ("huimaus", "poistunut"),
    ]


# --- Historia ---


def test_oireen_tila_kertoo_viimeisimman_tiedon():
    tulokset = [
        _tulos("t1", _aika(5), _yhteenveto(K.KELTAINEN, (("huimaus", "kohtalainen"),))),
        _tulos("t1", _aika(9), _yhteenveto(K.VIHREA, (("väsymys", "ei tiedossa"),))),
    ]
    puhelut = historia.oirehistoria(tulokset, "t1").puhelut

    assert historia.oireen_tila("huimaus", puhelut) == "viimeksi 5.10., kohtalainen"
    assert historia.oireen_tila("väsymys", puhelut) == "viimeksi 9.10."
    assert historia.oireen_tila("yskä", puhelut) == ""


def test_ei_puheluita_ei_historiaa():
    assert historia.oirehistoria([], "t1") is None
    assert historia.oirehistoria([_tulos("t1", None, _yhteenveto(K.VIHREA))], "t1") is None


def test_historia_vanhimmasta_ja_rivit_useimmin_mainitusta():
    seuranta = Oireseuranta(oire="pyörryttää", tila=OireenSeurannanTila.POISTUNUT)
    tulokset = [
        _tulos(
            "t2", _aika(12, 14), _yhteenveto(K.VIHREA, (("väsymys", "lievä"),), (), (seuranta,))
        ),
        _tulos("t1", _aika(5), _yhteenveto(K.KELTAINEN, (("huimaus", "kohtalainen"),))),
        _tulos("t1", _aika(7), None, loppu=PuhelunLoppu.EI_VASTANNUT),
        _tulos(
            "t1",
            _aika(9),
            _yhteenveto(K.KELTAINEN, (("väsynyt", "kohtalainen"), ("huimausta", "lievä"))),
        ),
    ]

    tulos = historia.oirehistoria(tulokset, tehtava_id="t2")

    # Suomen aikaan (kesäaika, UTC+3): kanta on UTC:ssä, muunnos vasta näytettäessä.
    assert [p.nimi for p in tulos.puhelut] == [
        "5.10. klo 13.00",
        "7.10. klo 13.00",
        "9.10. klo 13.00",
        "12.10. klo 17.00",
    ]
    assert [p.tavoitettu for p in tulos.puhelut] == [True, False, True, True]
    assert [p.tama_tehtava for p in tulos.puhelut] == [False, False, False, True]
    # huimaus ja väsymys kahdesti; poistunut ei ole maininta. Tasapelissä aiemmin mainittu ensin.
    assert tulos.oireet == ["huimaus", "väsymys"]
    assert tulos.mainintoja == {"huimaus": 2, "väsymys": 2}
    viimeinen = {m.oire: m.voimakkuus for m in tulos.puhelut[-1].oireet}
    assert viimeinen == {"väsymys": "lievä", "huimaus": "poistunut"}
    # Tunnuslukuruudut: tavoitetut, viimeisin arvio ja toistuvimman oireen tilanne.
    assert tulos.tavoitettu == 3
    assert (tulos.viimeisin_taso, tulos.viimeisin_aika) == (K.VIHREA, _aika(12, 14))
    assert tulos.toistuvin_tila == "poistunut 12.10."


# --- Tapaussivu ---


def _tehtava(sessio: Session, tehtava_id: str, vanhus_id: str) -> None:
    konteksti = Puhelukonteksti(ika=81, aktiivisia_laakkeita=2, muutokset=[])
    sessio.add(
        PuhelutehtavaRivi(
            id=tehtava_id,
            vanhus_id=vanhus_id,
            tila="VALMIS",
            prioriteetti=10,
            perusteet=[],
            ika=81,
            aktiivisia_laakkeita=2,
            viitepaiva=datetime(2020, 4, 1, tzinfo=UTC),
            yritykset=1,
            kysymykset=[],
            konteksti=konteksti.model_dump(mode="json"),
        )
    )


def _tallenna(sessio: Session, istunto: str, tehtava: str, paattyi: datetime, y: Yhteenveto):
    sessio.add(
        PuhelunTulosRivi(
            istunto_id=istunto,
            tehtava_id=tehtava,
            loppu="VALMIS",
            paattyi=paattyi,
            yhteenveto=y.model_dump(mode="json"),
        )
    )


@pytest.fixture
def asiakas(sessiotehdas: sessionmaker[Session]):
    with sessiotehdas() as sessio:
        _tehtava(sessio, "t-vanha", "vanhus-1")
        _tehtava(sessio, "t-uusi", "vanhus-1")
        _tehtava(sessio, "t-muu", "vanhus-2")
        _tehtava(sessio, "t-ei-puheluita", "vanhus-3")
        _tallenna(
            sessio,
            "i1",
            "t-vanha",
            _aika(5),
            _yhteenveto(K.KELTAINEN, (("ahdistaa", "kohtalainen"),)),
        )
        _tallenna(
            sessio,
            "i2",
            "t-uusi",
            _aika(12),
            _yhteenveto(K.PUNAINEN, (("alakulo", "voimakas"), ("rapula <b>", "ei tiedossa"))),
        )
        _tallenna(sessio, "i3", "t-muu", _aika(6), _yhteenveto(K.VIHREA, (("yskä", "lievä"),)))
        sessio.commit()

    def sessio_override():
        with sessiotehdas() as sessio:
            yield sessio

    app.dependency_overrides[hae_sessio] = sessio_override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _upotettu(html: str) -> dict:
    osuma = re.search(
        r'<script type="application/json" id="oirehistoria-data">(.*?)</script>', html
    )
    assert osuma is not None
    return json.loads(osuma.group(1))


def test_tapaussivu_nayttaa_henkilon_kaikkien_puhelujen_oireet(asiakas):
    vastaus = asiakas.get("/hoitaja/tapaukset/t-uusi")

    assert vastaus.status_code == 200
    assert "Mistä henkilö on kertonut" in vastaus.text
    assert "/static/oirehistoria.js" in vastaus.text
    data = _upotettu(vastaus.text)
    assert [p["tama_tehtava"] for p in data["puhelut"]] == [False, True]  # toisen henkilön ei
    assert data["oireet"] == ["alakulo ja yksinäisyys", "rapula <b>"]
    assert data["mainintoja"]["alakulo ja yksinäisyys"] == 2
    kuvaukset = [m["kuvaukset"] for m in data["puhelut"][1]["oireet"]]
    assert ["rapula <b>"] in kuvaukset
    upotettu = vastaus.text.split('id="oirehistoria-data">')[1].split("</script>")[0]
    assert "<b>" not in upotettu  # tojson escapettaa: data ei voi rikkoa sivua


def test_tapaussivu_ilman_puheluita_ei_nayta_osiota(asiakas):
    vastaus = asiakas.get("/hoitaja/tapaukset/t-ei-puheluita")

    assert vastaus.status_code == 200
    assert "Mistä henkilö on kertonut" not in vastaus.text
    assert "oirehistoria.js" not in vastaus.text


def test_kuukausisivut_vanhimmasta_ja_rivit_kuukauden_oireista():
    tulokset = [
        _tulos(
            "t1",
            datetime(2026, 9, 24, 10, 5, tzinfo=UTC),
            _yhteenveto(K.VIHREA, (("väsymys", "lievä"),)),
        ),
        _tulos(
            "t1",
            datetime(2026, 9, 28, 10, 0, tzinfo=UTC),
            _yhteenveto(K.KELTAINEN, (("huimaus", "lievä"),)),
        ),
        _tulos(
            "t1", datetime(2026, 10, 1, 9, 0, tzinfo=UTC), None, loppu=PuhelunLoppu.EI_VASTANNUT
        ),
        _tulos(
            "t1",
            datetime(2026, 10, 3, 9, 0, tzinfo=UTC),
            _yhteenveto(K.KELTAINEN, (("pyörryttää", "kohtalainen"),)),
        ),
    ]

    kuukaudet = historia.oirehistoria(tulokset).kuukaudet

    assert [(k.avain, k.nimi) for k in kuukaudet] == [
        ("2026-09", "Syyskuu 2026"),
        ("2026-10", "Lokakuu 2026"),
    ]
    assert [len(k.puhelut) for k in kuukaudet] == [2, 2]  # tavoittamaton mukana sarakkeena
    # Rivit koko historian järjestyksessä (huimaus 2 mainintaa), vain kuukaudessa mainitut.
    assert [k.oireet for k in kuukaudet] == [["huimaus", "väsymys"], ["huimaus"]]


def test_kuun_vaihde_suomen_ajassa():
    # 31.10. klo 22.30 UTC on jo 1.11. klo 0.30 Suomessa (talviaika, UTC+2): marraskuu.
    tulokset = [
        _tulos(
            "t1",
            datetime(2026, 10, 31, 22, 30, tzinfo=UTC),
            _yhteenveto(K.VIHREA, (("väsymys", "lievä"),)),
        ),
    ]

    historia_ = historia.oirehistoria(tulokset)

    assert [k.nimi for k in historia_.kuukaudet] == ["Marraskuu 2026"]
    assert historia_.puhelut[0].nimi == "1.11. klo 00.30"


def test_yli_kymmenen_puhelun_kuukausi_jaetaan_sivuihin_uusimmat_lopussa():
    tulokset = [
        _tulos(
            "t1",
            datetime(2026, 10, 1 + i // 3, 8 + i % 3, 0, tzinfo=UTC),
            _yhteenveto(K.VIHREA, (("väsymys", "lievä"),) if i == 0 else ()),
        )
        for i in range(25)
    ]

    lokakuu = historia.oirehistoria(tulokset).kuukaudet[0]

    assert [(s.alku, s.loppu) for s in lokakuu.sivut] == [(1, 5), (6, 15), (16, 25)]
    assert lokakuu.sivut[-1].puhelut == lokakuu.puhelut[-10:]
    # Rivit sivukohtaisesti: väsymys vain ensimmäisen sivun puhelussa.
    assert [s.oireet for s in lokakuu.sivut] == [["väsymys"], [], []]


def test_enintaan_kymmenen_puhelua_yksi_sivu():
    tulokset = [
        _tulos("t1", datetime(2026, 10, 1 + i, 9, 0, tzinfo=UTC), _yhteenveto(K.VIHREA))
        for i in range(10)
    ]

    sivut = historia.oirehistoria(tulokset).kuukaudet[0].sivut

    assert [(s.alku, s.loppu) for s in sivut] == [(1, 10)]
