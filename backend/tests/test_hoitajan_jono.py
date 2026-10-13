from datetime import UTC, datetime, timedelta

from app.hoitaja import jono
from app.hoitaja.jono import JononTaso
from app.mallit import Kiireellisyys, Puhelutehtava, Saantoloydokset, Yhteenveto
from app.tietokanta import PuhelunTulosRivi

K = Kiireellisyys
NYT = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def tehtava(tehtava_id: str) -> Puhelutehtava:
    return Puhelutehtava.model_construct(id=tehtava_id)


def yhteenveto(taso: Kiireellisyys, analyysi_valmis: bool = True) -> dict:
    malli = Yhteenveto(
        taso=taso,
        saanto=Saantoloydokset(taso=taso),
        analyysi_valmis=analyysi_valmis,
    )
    return malli.model_dump(mode="json")


def test_jono_jarjestyy_kiireellisyyden_mukaan_ja_sailyttaa_soittojarjestyksen():
    tapaukset = [tehtava(t) for t in ("vihrea", "ei-1", "keltainen", "ei-2", "punainen")]
    tasot = {
        "vihrea": JononTaso(K.VIHREA, True),
        "keltainen": JononTaso(K.KELTAINEN, False),
        "punainen": JononTaso(K.PUNAINEN, True),
    }

    jarjestetty = [t.id for t in jono.jarjesta(tapaukset, tasot)]

    assert jarjestetty == ["punainen", "keltainen", "vihrea", "ei-1", "ei-2"]


def test_hoitaja_soittaa_laskee_punaiset_ja_keltaiset():
    tasot = {
        "a": JononTaso(K.PUNAINEN, True),
        "b": JononTaso(K.KELTAINEN, False),
        "c": JononTaso(K.VIHREA, True),
    }

    assert jono.hoitaja_soittaa(tasot) == 2


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

    tasot = jono.hae_tasot(sessio, ["t-1", "t-2", "t-3"])

    assert tasot == {"t-1": JononTaso(K.KELTAINEN, analyysi_valmis=False)}
    assert jono.hae_tasot(sessio, []) == {}
