"""Kysymysten generointi: pelkat kysymykset muutoksista. Keskustelun kulku on LLM:n ohjeissa.

Puhtaat funktiot testataan testien omalla sisallolla (conftest.sisalto); kannan alkusisalto
ja sen lataus testataan erikseen alempana.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.kysymykset.kysymykset import (
    MAX_MUUTOKSIA_PUHELUSSA,
    MAX_TARKENNUKSIA,
    luo_konteksti,
    luo_kysymykset,
)
from app.kysymykset.varasto import lataa_sisalto
from app.mallit import Laake, Laakemuutos, MuutosTyyppi
from app.muutokset.laakkeet import ainesosa
from app.tietokanta import LaakeryhmaRivi, TarkennuskysymysRivi

VIITE = datetime(2020, 4, 1, tzinfo=UTC)


def muutos(tyyppi: MuutosTyyppi, nimi: str, paivia_sitten: int = 5) -> Laakemuutos:
    tapahtui = VIITE - timedelta(days=paivia_sitten)
    laake = Laake(
        koodi=nimi, nimi=nimi, alkoi=tapahtui, paattyi=None, syy="Diagnoosi", kaytossa=True
    )
    return Laakemuutos(
        tyyppi=tyyppi, laake=laake, tapahtui=tapahtui, paivia_sitten=paivia_sitten, kuvaus=nimi
    )


def tunnisteet(kysymykset) -> list[str]:
    return [k.tunniste for k in kysymykset]


# --- Puhtaat funktiot ---


def test_jarjestys_muutokset_tarkennukset_yleiset(sisalto):
    kysymykset = luo_kysymykset(
        [muutos(MuutosTyyppi.UUSI_LAAKE, "Simvastatin 20 MG Oral Tablet")], sisalto
    )

    assert tunnisteet(kysymykset) == ["UUSI_LAAKE", "tarkennus.lihaskivut", "vointi"]
    assert kysymykset[0].muutos_tyyppi == MuutosTyyppi.UUSI_LAAKE
    assert kysymykset[0].teksti == sisalto.muutoskysymykset[MuutosTyyppi.UUSI_LAAKE]
    assert set(kysymykset[0].model_dump()) == {"tunniste", "teksti", "muutos_tyyppi"}


def test_ilman_muutoksia_vain_yleiskysymykset(sisalto):
    assert tunnisteet(luo_kysymykset([], sisalto)) == ["vointi"]


def test_enintaan_kaksi_muutosta_uusin_ensin_tyyppi_kerran(sisalto):
    muutokset = [
        muutos(MuutosTyyppi.UUSI_LAAKE, "A 1 MG", paivia_sitten=1),
        muutos(MuutosTyyppi.UUSI_LAAKE, "B 1 MG", paivia_sitten=2),
        muutos(MuutosTyyppi.ANNOSMUUTOS, "C 1 MG", paivia_sitten=3),
        muutos(MuutosTyyppi.LOPETETTU_LAAKE, "D 1 MG", paivia_sitten=4),
    ]

    tyypit = [k.muutos_tyyppi for k in luo_kysymykset(muutokset, sisalto) if k.muutos_tyyppi]

    assert len(tyypit) == MAX_MUUTOKSIA_PUHELUSSA
    assert tyypit == [MuutosTyyppi.UUSI_LAAKE, MuutosTyyppi.ANNOSMUUTOS]


def test_muutostyyppi_ilman_kysymysta_ohitetaan(sisalto):
    ilman = {t: x for t, x in sisalto.muutoskysymykset.items() if t != MuutosTyyppi.UUSI_LAAKE}
    vajaa = replace(sisalto, muutoskysymykset=ilman)

    kysymykset = luo_kysymykset([muutos(MuutosTyyppi.UUSI_LAAKE, "A 1 MG")], vajaa)

    assert tunnisteet(kysymykset) == ["vointi"]


def test_tarkennuksia_enintaan_raja_eika_toistoja(sisalto):
    muutokset = [
        muutos(MuutosTyyppi.UUSI_LAAKE, "Oxycodone Hydrochloride 5 MG", paivia_sitten=1),
        muutos(MuutosTyyppi.ANNOSMUUTOS, "Warfarin Sodium 5 MG", paivia_sitten=2),
    ]

    tarkennukset = [
        t for t in tunnisteet(luo_kysymykset(muutokset, sisalto)) if t.startswith("tarkennus")
    ]

    assert len(tarkennukset) == MAX_TARKENNUKSIA
    assert len(set(tarkennukset)) == len(tarkennukset)


def test_lopetetusta_laakkeesta_ei_haittavaikutustarkennusta(sisalto):
    kysymykset = luo_kysymykset(
        [muutos(MuutosTyyppi.LOPETETTU_LAAKE, "Simvastatin 20 MG")], sisalto
    )

    assert not any(t.startswith("tarkennus") for t in tunnisteet(kysymykset))


def test_tuntematon_laake_ei_tarkennuksia(sisalto):
    kysymykset = luo_kysymykset(
        [muutos(MuutosTyyppi.UUSI_LAAKE, "Epoetin Alfa 4000 UNT/ML")], sisalto
    )

    assert not any(t.startswith("tarkennus") for t in tunnisteet(kysymykset))


def test_konteksti_llm_lle_ilman_nimia_ja_diagnooseja(sisalto):
    muutokset = [
        muutos(MuutosTyyppi.UUSI_LAAKE, "Warfarin Sodium 5 MG", paivia_sitten=3),
        muutos(MuutosTyyppi.LOPETETTU_LAAKE, "Epoetin Alfa 4000 UNT/ML", paivia_sitten=9),
    ]

    konteksti = luo_konteksti(muutokset, ika=82, aktiivisia=6, sisalto=sisalto)

    assert konteksti.ika == 82
    assert konteksti.aktiivisia_laakkeita == 6
    assert [(m.tyyppi, m.laakeryhma, m.paivia_sitten) for m in konteksti.muutokset] == [
        (MuutosTyyppi.UUSI_LAAKE, "verenohennus", 3),
        (MuutosTyyppi.LOPETETTU_LAAKE, None, 9),
    ]
    sarjallistettu = konteksti.model_dump_json().lower()
    assert "warfarin" not in sarjallistettu
    assert "diagnoosi" not in sarjallistettu


def test_ryhman_tunnistus_ainesosasta(sisalto):
    assert sisalto.tunnista_ryhma("Abuse-Deterrent 12 HR Oxycodone Hydrochloride 15 MG").nimi == (
        "opioidit"
    )
    assert sisalto.tunnista_ryhma("Epoetin Alfa 4000 UNT/ML") is None


# --- Laakeryhma ATC-koodista (tuotannon tapa) ---


def test_ryhma_atc_koodista(sisalto):
    assert sisalto.tunnista_ryhma("Mikä tahansa nimi", "B01AA03").nimi == "verenohennus"


def test_pisin_atc_etuliite_voittaa(sisalto):
    assert sisalto.tunnista_ryhma("Digoxin 0.125 MG", "C01AA05").nimi == "sydänglykosidit"
    assert sisalto.tunnista_ryhma("Nitroglycerin", "C01DA02").nimi == "sydänlääkkeet"


def test_tunnettu_atc_ilman_ryhmaa_ei_kayta_nimea(sisalto):
    """ATC on luotettavampi kuin nimi: jos se ei osu ryhmaan, nimeen ei turvauduta."""
    assert sisalto.tunnista_ryhma("Warfarin Sodium 5 MG", "N02BE01") is None


def test_ilman_atc_kaytetaan_nimea(sisalto):
    assert sisalto.tunnista_ryhma("Warfarin Sodium 5 MG", None).nimi == "verenohennus"


def test_kysymykset_kayttavat_atc_koodia(sisalto):
    laake_muutos = muutos(MuutosTyyppi.UUSI_LAAKE, "Brandinimi 5 MG")
    laake_muutos.laake.atc = "B01AA03"

    tunnisteet_ = tunnisteet(luo_kysymykset([laake_muutos], sisalto))

    assert "tarkennus.verenvuoto" in tunnisteet_


# --- Kannan alkusisalto (migraatio) ja lataus ---


def test_alkusisalto_kattaa_kaikki_muutostyypit(sessio):
    sisalto = lataa_sisalto(sessio)

    assert set(sisalto.muutoskysymykset) == set(MuutosTyyppi)
    # Vointi kysytään avauksessa, ei listalla (migraatio a3c7e1d9f204, 12.10.).
    assert not sisalto.yleiskysymykset
    assert all(teksti.rstrip().endswith("?") for teksti in sisalto.muutoskysymykset.values())
    for ryhma in sisalto.laakeryhmat:
        assert ryhma.atc_etuliitteet, ryhma.nimi  # ATC ensisijainen; avainsanat varakeino
        assert ryhma.tarkennusaiheet, ryhma.nimi
        assert all(aihe in sisalto.tarkennuskysymykset for aihe in ryhma.tarkennusaiheet)


def test_alkusisallon_ryhmilla_on_atc_etuliitteet(sessio):
    import re

    sisalto = lataa_sisalto(sessio)

    for ryhma in sisalto.laakeryhmat:
        assert ryhma.atc_etuliitteet, ryhma.nimi
        assert all(
            re.fullmatch(r"[A-Z](\d{2}([A-Z]([A-Z](\d{2})?)?)?)?", e) for e in ryhma.atc_etuliitteet
        )


def test_alkusisalto_luokittelee_demodatan_atc_koodit(sessio):
    sisalto = lataa_sisalto(sessio)

    odotetut = {
        "B01AA03": "verenohennus",
        "B01AC06": "verenohennus",
        "N02AA05": "opioidit",
        "N02AJ": "opioidit",
        "A10AB01": "insuliini",
        "C01AA05": "sydänglykosidit",
        "C03CA01": "nesteenpoistolääkkeet",
        "C10AA01": "kolesterolilääkkeet",
        "L01XA01": "solunsalpaajat",
        "R03BA05": "hengitystielääkkeet",
        "M05BA04": "osteoporoosilääkkeet",
        "N03AE01": "rauhoittavat ja unilääkkeet",
        "R06AA02": "väsyttävät allergialääkkeet",
    }
    for atc, ryhma in odotetut.items():
        assert sisalto.tunnista_ryhma("x", atc).nimi == ryhma, atc


def test_alkusisalto_ryhmat(sessio):
    sisalto = lataa_sisalto(sessio)

    assert sisalto.tunnista_ryhma("Warfarin Sodium 5 MG Oral Tablet").korkea_riski is True
    assert sisalto.tunnista_ryhma("Simvastatin 10 MG Oral Tablet").korkea_riski is False
    assert sisalto.tunnista_ryhma("insulin human  isophane 70 UNT/ML [Humulin]").korkea_riski
    assert sisalto.tunnista_ryhma("Epoetin Alfa 4000 UNT/ML") is None


def test_alkusisallon_kysymyksissa_ei_laakkeen_nimea(sessio):
    sisalto = lataa_sisalto(sessio)
    nimet = [
        "Warfarin Sodium 5 MG Oral Tablet",
        "Simvastatin 20 MG Oral Tablet",
        "24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet",
    ]
    muutokset = [muutos(tyyppi, nimi) for tyyppi in MuutosTyyppi for nimi in nimet]

    tekstit = " ".join(k.teksti.lower() for k in luo_kysymykset(muutokset, sisalto))

    for nimi in nimet:
        assert ainesosa(nimi) not in tekstit


def test_pois_kaytosta_otettu_sisalto_ei_lataudu(sessio):
    sessio.get(LaakeryhmaRivi, "verenohennus").on_aktiivinen = False
    sessio.get(TarkennuskysymysRivi, "kaatuminen").on_aktiivinen = False
    sessio.commit()

    sisalto = lataa_sisalto(sessio)

    assert sisalto.tunnista_ryhma("Warfarin Sodium 5 MG") is None
    assert "kaatuminen" not in sisalto.tarkennuskysymykset
    assert all("kaatuminen" not in ryhma.tarkennusaiheet for ryhma in sisalto.laakeryhmat)
