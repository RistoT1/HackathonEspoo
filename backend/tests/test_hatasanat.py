"""Hatasanojen testipankki. Turvallisuuskriittinen: jokainen hatasanat.json-muutos tanne."""

import pytest

from app.puhelu.hatasanat import tunnista_hatamerkki

HATA = [
    ("Rintaan sattuu kovasti", "rintakipu"),
    ("minulla on rintakipua", "rintakipu"),
    ("Rintaa puristaa.", "rintakipu"),
    ("kipua rinnassa aamusta asti", "rintakipu"),
    ("En saa henkeä kunnolla", "hengenahdistus"),
    ("on ollut hengenahdistusta", "hengenahdistus"),
    ("vaikea hengittää", "hengenahdistus"),
    ("Puhe puuroutuu", "halvausoire"),
    ("suupieli roikkuu", "halvausoire"),
    ("käsi ei toimi oikein", "halvausoire"),
    ("pyörryin eilen", "tajunta"),
    ("Soitanko 112?", "hatapyynto"),
    ("pitäisikö soittaa yksi yksi kaksi", "hatapyynto"),
    ("tarvitsen ambulanssin", "hatapyynto"),
]

EI_HATAA = [
    "Hyvin menee, kiitos.",
    "Ei mitään hätää.",
    "Vähän huimaa aamuisin.",
    "Kävin lääkärissä viime viikolla.",
    "Nukuin huonosti.",
    "Vatsa on ollut ihan normaali.",
    "Toinen puoli vuotta sitten aloitin lenkkeilyn.",
    "",
]


@pytest.mark.parametrize(("teksti", "luokka"), HATA)
def test_hatamerkki_tunnistetaan(teksti, luokka):
    assert tunnista_hatamerkki(teksti) == luokka


@pytest.mark.parametrize("teksti", EI_HATAA)
def test_tavallinen_vastaus_ei_ole_hatamerkki(teksti):
    assert tunnista_hatamerkki(teksti) is None


def test_kieltoa_ei_tulkita_tarkoituksella():
    # Saanto voi vain nostaa: "ei rintakipua" laukaisee mieluummin kuin ohittaa.
    assert tunnista_hatamerkki("ei ole rintakipua") == "rintakipu"
