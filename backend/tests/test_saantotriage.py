"""Sääntöjen triage (triage/saannot.py): lääkemuutoksen poikkeama nostaa keltaiseksi
huomiosanana omalla paikallaan kuulemisjärjestyksessä."""

from app.mallit import Kiireellisyys
from app.puhelu.tietopohja import Tietopohjahavainto
from app.triage.saannot import saantoloydokset

PERUSTE = "lääkemuutosta ei noudateta"


def _loydokset(havainnot: list[Tietopohjahavainto], laakepoikkeamat: dict[int, str]):
    return saantoloydokset(havainnot, [], None, None, None, laakepoikkeamat)


def test_poikkeama_nostaa_keltaiseksi():
    loydokset = _loydokset([Tietopohjahavainto()], {0: PERUSTE})

    assert loydokset.taso == Kiireellisyys.KELTAINEN
    assert loydokset.huomiosanat == [PERUSTE]
    assert loydokset.perusteet == [f"huomiosana: {PERUSTE}"]


def test_poikkeama_on_oman_vuoronsa_sanojen_perassa():
    havainnot = [
        Tietopohjahavainto(keltainen=["kaadu"]),
        Tietopohjahavainto(keltainen=["huima"]),
        Tietopohjahavainto(keltainen=["pyörry", "kaadu"]),
    ]

    loydokset = _loydokset(havainnot, {1: PERUSTE})

    assert loydokset.huomiosanat == ["kaadu", "huima", PERUSTE, "pyörry"]


def test_ilman_poikkeamaa_vihrea():
    loydokset = _loydokset([Tietopohjahavainto()], {})

    assert loydokset.taso == Kiireellisyys.VIHREA
    assert loydokset.huomiosanat == []
