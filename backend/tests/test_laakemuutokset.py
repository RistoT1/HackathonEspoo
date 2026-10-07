"""Laakemuutosten tunnistus puhtaina funktioina: ei kantaa, ei verkkoa."""

from datetime import UTC, datetime, timedelta

from app.mallit import Laakejakso, MuutosTyyppi
from app.muutokset.laakkeet import (
    ainesosa,
    laakkeet_viitepaivana,
    tunnista_laakemuutokset,
)

VIITE = datetime(2020, 4, 1, tzinfo=UTC)
IKKUNA = 90


def paivaa_sitten(paivia: int) -> datetime:
    return VIITE - timedelta(days=paivia)


def jakso(
    koodi: str,
    nimi: str,
    alkoi: datetime,
    paattyi: datetime | None = None,
    atc: str | None = None,
) -> Laakejakso:
    return Laakejakso(koodi=koodi, nimi=nimi, alkoi=alkoi, paattyi=paattyi, syy=None, atc=atc)


def tyypit(muutokset) -> list[MuutosTyyppi]:
    return [m.tyyppi for m in muutokset]


# --- UUSI_LAAKE ---


def test_ikkunassa_aloitettu_laake_on_uusi():
    jaksot = [jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(30))]

    muutokset = tunnista_laakemuutokset(jaksot, VIITE, IKKUNA)

    assert tyypit(muutokset) == [MuutosTyyppi.UUSI_LAAKE]
    assert muutokset[0].paivia_sitten == 30
    assert muutokset[0].laake.kaytossa is True
    assert "lisinopril" in muutokset[0].kuvaus


def test_ikkunan_ulkopuolella_aloitettu_ei_nay():
    jaksot = [jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(200))]

    assert tunnista_laakemuutokset(jaksot, VIITE, IKKUNA) == []


def test_viitepaivan_jalkeen_alkava_ei_nay():
    jaksot = [jakso("1", "lisinopril 10 MG Oral Tablet", VIITE + timedelta(days=5))]

    assert tunnista_laakemuutokset(jaksot, VIITE, IKKUNA) == []


# --- Uusinta: Synthea kirjaa pitkaaikaislaakkeen uudeksi riviksi samalla koodilla ---


def test_uusinta_samalla_koodilla_ei_ole_muutos():
    jaksot = [
        jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(400), paivaa_sitten(30)),
        jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(30)),
    ]

    assert tunnista_laakemuutokset(jaksot, VIITE, IKKUNA) == []


def test_uusinta_pienella_tauolla_ei_ole_muutos():
    jaksot = [
        jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(400), paivaa_sitten(35)),
        jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(30)),
    ]

    assert tunnista_laakemuutokset(jaksot, VIITE, IKKUNA) == []


def test_pitkan_tauon_jalkeen_aloitettu_on_uusi():
    jaksot = [
        jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(400), paivaa_sitten(300)),
        jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(30)),
    ]

    assert tyypit(tunnista_laakemuutokset(jaksot, VIITE, IKKUNA)) == [MuutosTyyppi.UUSI_LAAKE]


# --- LOPETETTU_LAAKE ---


def test_ikkunassa_paattynyt_laake_on_lopetettu():
    jaksot = [
        jakso("1", "simvastatin 20 MG Oral Tablet", paivaa_sitten(400), paivaa_sitten(10)),
    ]

    muutokset = tunnista_laakemuutokset(jaksot, VIITE, IKKUNA)

    assert tyypit(muutokset) == [MuutosTyyppi.LOPETETTU_LAAKE]
    assert muutokset[0].laake.kaytossa is False
    assert muutokset[0].paivia_sitten == 10


def test_lyhyt_kuuri_ikkunassa_on_yksi_muutos():
    jaksot = [
        jakso("1", "Acetaminophen 325 MG Oral Tablet", paivaa_sitten(40), paivaa_sitten(26)),
    ]

    muutokset = tunnista_laakemuutokset(jaksot, VIITE, IKKUNA)

    # Yksi muutos, ei erikseen uusi + lopetettu: muuten samasta laakkeesta kysyttaisiin kahdesti.
    assert tyypit(muutokset) == [MuutosTyyppi.LYHYT_KUURI]
    assert muutokset[0].paivia_sitten == 40
    assert muutokset[0].laake.kaytossa is False


def test_viitepaivan_jalkeen_paattyva_on_viela_kaytossa():
    jaksot = [
        jakso("1", "simvastatin 20 MG Oral Tablet", paivaa_sitten(400), VIITE + timedelta(days=3)),
    ]

    assert tunnista_laakemuutokset(jaksot, VIITE, IKKUNA) == []


# --- ANNOSMUUTOS ---


def test_sama_ainesosa_eri_vahvuus_on_annosmuutos():
    vanha = jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(400), paivaa_sitten(20))
    uusi = jakso("2", "lisinopril 20 MG Oral Tablet", paivaa_sitten(20))

    muutokset = tunnista_laakemuutokset([vanha, uusi], VIITE, IKKUNA)

    assert tyypit(muutokset) == [MuutosTyyppi.ANNOSMUUTOS]
    assert muutokset[0].laake.koodi == "2"
    assert muutokset[0].aiempi_laake is not None
    assert muutokset[0].aiempi_laake.koodi == "1"


def test_sama_atc_eri_nimi_on_annosmuutos():
    """Tuotannossa ATC ratkaisee: brandi- ja geneerinen nimi voivat erota."""
    vanha = jakso("1", "Marevan 3 MG", paivaa_sitten(400), paivaa_sitten(20), atc="B01AA03")
    uusi = jakso("2", "Warfarin Sodium 5 MG", paivaa_sitten(20), atc="B01AA03")

    assert tyypit(tunnista_laakemuutokset([vanha, uusi], VIITE, IKKUNA)) == [
        MuutosTyyppi.ANNOSMUUTOS
    ]


def test_eri_atc_ei_ole_annosmuutos_vaikka_nimi_tasmaa():
    vanha = jakso("1", "Ibuprofen 200 MG", paivaa_sitten(400), paivaa_sitten(20), atc="M01AE01")
    uusi = jakso("2", "Ibuprofen 5 % Gel", paivaa_sitten(20), atc="M02AA13")

    assert sorted(tyypit(tunnista_laakemuutokset([vanha, uusi], VIITE, IKKUNA))) == sorted(
        [MuutosTyyppi.UUSI_LAAKE, MuutosTyyppi.LOPETETTU_LAAKE]
    )


def test_ryhmatason_atc_kayttaa_nimea():
    """5-merkkinen ATC (esim. N02AJ) ei yksiloi ainetta: verrataan nimen ainesosaa."""
    vanha = jakso("1", "Oxycodone 5 MG / X", paivaa_sitten(400), paivaa_sitten(20), atc="N02AJ")
    uusi = jakso("2", "Oxycodone 10 MG / X", paivaa_sitten(20), atc="N02AJ")

    assert tyypit(tunnista_laakemuutokset([vanha, uusi], VIITE, IKKUNA)) == [
        MuutosTyyppi.ANNOSMUUTOS
    ]


def test_eri_ainesosa_ei_ole_annosmuutos():
    vanha = jakso("1", "lisinopril 10 MG Oral Tablet", paivaa_sitten(400), paivaa_sitten(20))
    uusi = jakso("2", "amlodipine 5 MG Oral Tablet", paivaa_sitten(20))

    muutokset = tunnista_laakemuutokset([vanha, uusi], VIITE, IKKUNA)

    assert sorted(tyypit(muutokset)) == sorted(
        [MuutosTyyppi.UUSI_LAAKE, MuutosTyyppi.LOPETETTU_LAAKE]
    )


# --- Apufunktiot ---


def test_ainesosa_ohittaa_maara_etuliitteen():
    assert ainesosa("lisinopril 10 MG Oral Tablet") == "lisinopril"
    assert ainesosa("24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet") == (
        "metformin hydrochloride"
    )
    assert ainesosa("10 ML Furosemide 10 MG/ML Injection") == "furosemide"
    assert ainesosa("0.25 ML Leuprolide Acetate 30 MG/ML Prefilled Syringe") == (
        "leuprolide acetate"
    )


def test_ainesosa_ohittaa_synthean_erikoismuodot():
    assert ainesosa("NDA020800 0.3 ML Epinephrine 1 MG/ML Auto-Injector") == "epinephrine"
    assert (
        ainesosa("Abuse-Deterrent 12 HR Oxycodone Hydrochloride 15 MG Extended Release Oral Tablet")
        == "oxycodone hydrochloride"
    )
    assert ainesosa("1 ML Epoetin Alfa 4000 UNT/ML Injection [Epogen]") == "epoetin alfa"
    assert ainesosa("Carbamazepine[Tegretol]") == "carbamazepine"
    assert ainesosa("insulin human  isophane 70 UNT/ML / Regular Insulin") == (
        "insulin human isophane"
    )


def test_laakkeet_viitepaivana_merkitsee_kaytossa_ja_jattaa_tulevat_pois():
    jaksot = [
        jakso("1", "A 1 MG", paivaa_sitten(100)),
        jakso("2", "B 1 MG", paivaa_sitten(100), paivaa_sitten(50)),
        jakso("3", "C 1 MG", VIITE + timedelta(days=1)),
    ]

    laakkeet = laakkeet_viitepaivana(jaksot, VIITE)

    kaytossa = {laake.koodi: laake.kaytossa for laake in laakkeet}
    assert kaytossa == {"1": True, "2": False}
