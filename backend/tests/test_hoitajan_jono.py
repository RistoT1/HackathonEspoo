from datetime import UTC, datetime, timedelta

from app.hoitaja import jono, palvelu
from app.mallit import Kiireellisyys, PuhelunLoppu, Puhelutehtava, Saantoloydokset, Yhteenveto
from app.tietokanta import PuhelunTulosRivi

K = Kiireellisyys
NYT = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def tehtava(tehtava_id: str) -> Puhelutehtava:
    return Puhelutehtava.model_construct(id=tehtava_id)


def arvio(taso: Kiireellisyys, analyysi_valmis: bool = True) -> Yhteenveto:
    return Yhteenveto(
        taso=taso,
        saanto=Saantoloydokset(taso=taso),
        analyysi_valmis=analyysi_valmis,
    )


def yhteenveto(taso: Kiireellisyys, analyysi_valmis: bool = True) -> dict:
    return arvio(taso, analyysi_valmis).model_dump(mode="json")


def test_jono_jarjestyy_kiireellisyyden_mukaan_ja_sailyttaa_soittojarjestyksen():
    tapaukset = [tehtava(t) for t in ("vihrea", "ei-1", "keltainen", "ei-2", "punainen")]
    arviot = {
        "vihrea": arvio(K.VIHREA),
        "keltainen": arvio(K.KELTAINEN, analyysi_valmis=False),
        "punainen": arvio(K.PUNAINEN),
    }

    jarjestetty = [t.id for t in jono.jarjesta(tapaukset, arviot)]

    assert jarjestetty == ["punainen", "keltainen", "vihrea", "ei-1", "ei-2"]


def test_hoitaja_soittaa_laskee_punaiset_ja_keltaiset():
    arviot = {
        "a": arvio(K.PUNAINEN),
        "b": arvio(K.KELTAINEN, analyysi_valmis=False),
        "c": arvio(K.VIHREA),
    }

    assert jono.hoitaja_soittaa(arviot) == 2


def test_tasoksi_uusimman_puhelun_yhteenveto(sessio):
    sessio.add_all(
        [
            PuhelunTulosRivi(
                istunto_id="vanha",
                tehtava_id="t-1",
                loppu="VALMIS",
                paattyi=NYT - timedelta(days=1),
                yhteenveto=yhteenveto(K.PUNAINEN),
            ),
            PuhelunTulosRivi(
                istunto_id="uusi",
                tehtava_id="t-1",
                loppu="VALMIS",
                paattyi=NYT,
                yhteenveto=yhteenveto(K.KELTAINEN, analyysi_valmis=False),
            ),
            PuhelunTulosRivi(
                istunto_id="ei-yhteenvetoa",
                tehtava_id="t-2",
                loppu="EI_VASTANNUT",
                paattyi=NYT,
                yhteenveto=None,
            ),
        ]
    )
    sessio.commit()

    arviot = jono.hae_arviot(sessio, ["t-1", "t-2", "t-3"])

    assert arviot == {"t-1": arvio(K.KELTAINEN, analyysi_valmis=False)}
    assert jono.hae_arviot(sessio, []) == {}


def test_uusin_tulos_paattymisaika_puuttuu_on_vanhin(sessio):
    """Jono ja tapaussivu lukevat saman tuloksen: Postgres järjestäisi NULLin ensimmäiseksi."""
    sessio.add_all(
        [
            PuhelunTulosRivi(
                istunto_id="ilman-aikaa",
                tehtava_id="t-1",
                loppu=None,
                paattyi=None,
                yhteenveto=yhteenveto(K.PUNAINEN),
            ),
            PuhelunTulosRivi(
                istunto_id="ajallinen",
                tehtava_id="t-1",
                loppu="KESKEYTYI",
                paattyi=NYT,
                yhteenveto=yhteenveto(K.VIHREA),
            ),
            PuhelunTulosRivi(
                istunto_id="vain-ilman-aikaa",
                tehtava_id="t-2",
                loppu=None,
                paattyi=None,
                yhteenveto=None,
            ),
        ]
    )
    sessio.commit()

    tulokset = palvelu.uusimmat_tulokset(sessio, ["t-1", "t-2"])

    assert tulokset["t-1"].istunto_id == "ajallinen"
    assert tulokset["t-1"].loppu is PuhelunLoppu.KESKEYTYI
    assert tulokset["t-1"].paattyi == NYT  # SQLite palauttaa ilman vyöhykettä: UTC
    assert tulokset["t-1"].yhteenveto == arvio(K.VIHREA)
    # JSON-null: tulos on olemassa, yhteenvetoa ei (jonossa "Ei arviota").
    assert tulokset["t-2"].yhteenveto is None
    assert tulokset["t-2"].loppu is None
    assert jono.hae_arviot(sessio, ["t-1", "t-2"]) == {"t-1": arvio(K.VIHREA)}
