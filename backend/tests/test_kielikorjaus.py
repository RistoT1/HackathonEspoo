"""Mallin suomen virheiden korjaus (kielikorjaukset.json). Tapaukset ovat oikeista mallin
vastauksista (puhelulokit ja testipankki 11.-12.10.)."""

import pytest

from app.puhelu.kielentarkistus import korjaa_kieli, sinuttelee


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
        # Testipankki 12.10. iltapäivä (Voikko, 611 vastausta).
        ("Pääsitkö itse ylös kaatuasi?", "Pääsitkö itse ylös kaatumisen jälkeen?"),
        (
            "Jos satutit itsesi tai et päässi itse ylös",
            "Jos satutit itsesi tai et päässyt itse ylös",
        ),
        (
            "Onko sinua pahoinvoinut enemmän ruokailun jälkeen?",
            "Onko sinulla ollut pahoinvointia enemmän ruokailun jälkeen?",
        ),
        ("ettei huimaus ole häIRjännyt sinua.", "ettei huimaus ole häirinnyt sinua."),
        ("Haittaaako tämä väsymys sinua?", "Haittaako tämä väsymys sinua?"),
        # 13.10. puhelu.
        ("On hyvä olla, että se ei haittaa.", "On hyvä kuulla, että se ei haittaa."),
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
        "Sinun on hyvä olla kotona.",
    ],
)
def test_oikeaa_suomea_ei_muuteta(oikea):
    assert korjaa_kieli(oikea) == (oikea, [])


@pytest.mark.parametrize(
    ("teksti", "odotettu"),
    [
        ("Oho! Onpa huolestuttava tilanne. Satutitko itsesi kaatuessa?", True),
        ("Miten vointinne on?", False),
        ("Voitteko kertoa lisää?", False),
    ],
)
def test_sinuttelu(teksti, odotettu):
    assert sinuttelee(teksti) is odotettu
