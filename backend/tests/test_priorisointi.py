"""Soittojarjestyksen pisteytys: puhdas funktio, ei triage."""

from datetime import UTC, datetime

from app.mallit import Laake, Laakemuutos, MuutosTyyppi
from app.puhelutehtavat.priorisointi import (
    IAKAS_RAJA,
    KORKEA_RISKI_PISTEET,
    MONILAAKITYS_RAJA,
    laske_prioriteetti,
)

AIKA = datetime(2020, 4, 1, tzinfo=UTC)


def muutos(tyyppi: MuutosTyyppi, nimi: str = "Tuntematon 1 MG") -> Laakemuutos:
    laake = Laake(koodi="1", nimi=nimi, alkoi=AIKA, paattyi=None, syy=None, kaytossa=True)
    return Laakemuutos(tyyppi=tyyppi, laake=laake, tapahtui=AIKA, paivia_sitten=1, kuvaus="x")


def test_muutostyyppien_pisteet(sisalto):
    uusi = laske_prioriteetti(
        [muutos(MuutosTyyppi.UUSI_LAAKE)], ika=70, aktiivisia=1, sisalto=sisalto
    )
    lopetettu = laske_prioriteetti(
        [muutos(MuutosTyyppi.LOPETETTU_LAAKE)], ika=70, aktiivisia=1, sisalto=sisalto
    )
    kuuri = laske_prioriteetti(
        [muutos(MuutosTyyppi.LYHYT_KUURI)], ika=70, aktiivisia=1, sisalto=sisalto
    )

    assert uusi.pisteet > lopetettu.pisteet > kuuri.pisteet
    assert uusi.perusteet == []


def test_korkean_riskin_laake_nostaa_ja_perustellaan(sisalto):
    tavallinen = laske_prioriteetti(
        [muutos(MuutosTyyppi.UUSI_LAAKE, "Simvastatin 10 MG")],
        ika=70,
        aktiivisia=1,
        sisalto=sisalto,
    )
    riski = laske_prioriteetti(
        [muutos(MuutosTyyppi.UUSI_LAAKE, "Warfarin Sodium 5 MG")],
        ika=70,
        aktiivisia=1,
        sisalto=sisalto,
    )

    assert riski.pisteet == tavallinen.pisteet + KORKEA_RISKI_PISTEET
    assert any("verenohennus" in peruste for peruste in riski.perusteet)


def test_korkean_riskin_lopetus_ei_nosta(sisalto):
    tulos = laske_prioriteetti(
        [muutos(MuutosTyyppi.LOPETETTU_LAAKE, "Warfarin Sodium 5 MG")],
        ika=70,
        aktiivisia=1,
        sisalto=sisalto,
    )

    assert tulos.perusteet == []


def test_ika_ja_monilaakitys(sisalto):
    perus = laske_prioriteetti(
        [muutos(MuutosTyyppi.UUSI_LAAKE)], ika=70, aktiivisia=1, sisalto=sisalto
    )
    iakas = laske_prioriteetti(
        [muutos(MuutosTyyppi.UUSI_LAAKE)], ika=IAKAS_RAJA, aktiivisia=1, sisalto=sisalto
    )
    moni = laske_prioriteetti(
        [muutos(MuutosTyyppi.UUSI_LAAKE)], ika=70, aktiivisia=MONILAAKITYS_RAJA, sisalto=sisalto
    )
    tuntematon_ika = laske_prioriteetti(
        [muutos(MuutosTyyppi.UUSI_LAAKE)], ika=None, aktiivisia=1, sisalto=sisalto
    )

    assert iakas.pisteet == perus.pisteet + 1
    assert moni.pisteet == perus.pisteet + 1
    assert tuntematon_ika.pisteet == perus.pisteet
    assert len(iakas.perusteet) == 1
    assert len(moni.perusteet) == 1
