"""Mallin suomen virheiden korjaus (kielikorjaukset.json). Tapaukset ovat oikeista mallin
vastauksista (puhelulokit ja testipankki 11.-12.10.)."""

import pytest

from app.puhelu.kielentarkistus import korjaa_kieli


@pytest.mark.parametrize(
    ("raaka", "odotettu"),
    [
        (
            "Onnea siitä, että olet turvassa omassa asunnossasi.",
            "Hyvä kuulla, että olet turvassa omassa asunnossasi.",
        ),
        (
            "Pystytkö antamaan ne jollekohun?",
            "Pystytkö antamaan ne jollekulle?",
        ),
        ("Mikä sinua huolestaa?", "Mikä sinua huolettaa?"),
        ("Olen pahoillinen, että väsyttää.", "Olen pahoillani, että väsyttää."),
        ("Onko sinulla ketäiä, jolle jutella?", "Onko sinulla ketään, jolle jutella?"),
        ("On todella tärkee, että olet turvassa.", "On todella tärkeää, että olet turvassa."),
        ("istu hetki ennenenen ennen kuin lähdet", "istu hetki ennen kuin lähdet"),
        ("Haullaako väsymys sinua?", "Haittaako väsymys sinua?"),
        ("Myötäeleen, että väsymys tuntuu iltaisin.", "Ymmärrän, että väsymys tuntuu iltaisin."),
        ("Sopi, jatketaan. Miten voit tänään?", "Sopii, jatketaan. Miten voit tänään?"),
    ],
)
def test_toistuvat_virheet_korjataan(raaka, odotettu):
    korjattu, korjatut = korjaa_kieli(raaka)

    assert korjattu == odotettu
    assert korjatut


@pytest.mark.parametrize(
    "oikea",
    [
        "Mikä sinua huolettaa?",
        "Olen pahoillani, että sinulla on ollut vaikeaa.",
        "Onko tämä alkanut hiljattain?",
        "On tärkeää, että juot riittävästi.",
        "Istu hetki ennen kuin nouset.",
        "Onko lääke sopinut sinulle?",
        "Sopiiko, että jatketaan?",
    ],
)
def test_oikeaa_suomea_ei_muuteta(oikea):
    assert korjaa_kieli(oikea) == (oikea, [])
