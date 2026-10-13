"""Mallin LOPETUKSEN esto puhtaana funktiona: syyt, niiden järjestys ja jatko."""

import pytest

from app.puhelu.lopetus import (
    HUONON_VOINNIN_JATKO,
    OIREEN_TARKENNUS,
    Esto,
    ilman_kiitoksia,
    lopetuksen_esto,
    yhtenaista_lopetusmerkki,
)

KYSYMYS = "Miten uusi lääke on sopinut?"


def esto(
    henkilon_teksti: str = "Ihan hyvin.",
    henkilo_kysyi: bool = False,
    oire_kesken: bool = False,
    lista_kesken: bool = False,
    saa_lopettaa_kesken: bool = False,
) -> Esto | None:
    return lopetuksen_esto(
        henkilon_teksti=henkilon_teksti,
        henkilo_kysyi=henkilo_kysyi,
        oire_kesken=oire_kesken,
        listan_kysymys=KYSYMYS,
        lista_kesken=lista_kesken,
        saa_lopettaa_kesken=saa_lopettaa_kesken,
    )


def test_valmis_puhelu_saa_paattya():
    assert esto() is None


def test_henkilon_kysymys_estaa_ja_toistaa_listan_kysymyksen():
    # 11.10. arviointi: "Kuka sinä olet?" -> vastaus ja lopetus kesken listan.
    tulos = esto(henkilon_teksti="No huonosti, mitä kello?", henkilo_kysyi=True, oire_kesken=True)

    assert tulos == Esto("malli:lopetus_ohitettu_kysymys", jatko=KYSYMYS)


def test_kesken_oleva_oire_menee_huonon_voinnin_edelle():
    tulos = esto(henkilon_teksti="huonosti, on ollut vapinaa", oire_kesken=True)

    assert tulos == Esto("malli:lopetus_ohitettu_oire", jatko=OIREEN_TARKENNUS)


@pytest.mark.parametrize("teksti", ["No huonosti", "Ei kovin hyvin.", "Paha olo on."])
def test_huono_vointi_estaa(teksti):
    assert esto(henkilon_teksti=teksti) == Esto(
        "malli:lopetus_ohitettu_huono_vointi", jatko=HUONON_VOINNIN_JATKO
    )


def test_kesken_jaanyt_lista_estaa_kerran_ilman_jatkoa():
    # Seuraava kysymys lisätään kysymyslistan kirjanpidosta, ei tästä.
    tulos = esto(lista_kesken=True)

    assert tulos == Esto("malli:lopetus_ohitettu_lista", jatko="", vain_kerran=True)


def test_lista_saa_jaada_kesken_lopuillaan_tai_toisella_kerralla():
    assert esto(lista_kesken=True, saa_lopettaa_kesken=True) is None


def test_pienella_kirjoitettu_lopetus_lopussa_on_merkki():
    # 12.10. läpikäynti: malli kirjoitti "Lopetus." ja agentti luki sen ääneen.
    assert yhtenaista_lopetusmerkki("Hyvä kuulla. Lopetus.") == "Hyvä kuulla. LOPETUS"
    assert yhtenaista_lopetusmerkki("Hyvä kuulla.\nlopetus") == "Hyvä kuulla.\nLOPETUS"


def test_lopetus_sanana_lauseen_keskella_ei_ole_merkki():
    teksti = "Kerroit lääkkeen lopetus"

    assert yhtenaista_lopetusmerkki(teksti) == teksti


def test_lopetuksen_edelta_kiitokset_ja_kysymykset_pois():
    puhuttava = "Hyvä kuulla. Kiitos, että kerroit. Välitän tämän hoitajalle. Onko muuta?"

    assert ilman_kiitoksia(puhuttava) == "Hyvä kuulla."
