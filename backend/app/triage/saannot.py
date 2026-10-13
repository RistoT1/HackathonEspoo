"""Sääntöpohjainen kiireellisyys puhelun lopussa. Puhdas funktio: ei LLM:ää, ei kantaa.

Pohja on Jeren tietopohja.kiireellisyys() (hätä ja vakavat huolet punainen; huoli,
huolestuttava oire, huomiosana, uhkaus tai toistuva epäselvyys keltainen). Lisäksi
puhelun tapahtumat nostavat: tukitila punaiseksi, hoitajapyyntö, lääkeneuvon kysyminen,
epävarma vastaus, kesken katkennut puhelu ja edellisessä puhelussa kerrottu, yhä jatkuva
oire (muisti) keltaiseksi. Sääntö on alaraja: malli ja
hoitaja voivat vain nostaa tasoa.
"""

from app.mallit import Kiireellisyys, KysymyksenVastaus, PuhelunLoppu, Saantoloydokset
from app.puhelu.tietopohja import (
    SELKONIMET,
    Tietopohjahavainto,
    kiireellisyys,
    korkeampi_taso,
)
from app.puhelu.tietopohja import saannot as tietopohjan_saannot


def _keraa(havainnot: list[Tietopohjahavainto], kentta: str) -> list[str]:
    tulos: list[str] = []
    for havainto in havainnot:
        for arvo in getattr(havainto, kentta):
            if arvo not in tulos:
                tulos.append(arvo)
    return tulos


def saantoloydokset(
    havainnot: list[Tietopohjahavainto],
    vastaukset: list[KysymyksenVastaus],
    loppu: PuhelunLoppu | None,
    hatamerkki: str | None,
    tukitila: str | None,
    edelliset_oireet: list[str] | tuple[str, ...] = (),
) -> Saantoloydokset:
    epaselvia = sum(vastaus.epavarma for vastaus in vastaukset)
    taso = kiireellisyys(havainnot, epaselvia)
    perusteet: list[str] = []

    def nosta(uusi: str, peruste: str) -> None:
        nonlocal taso
        taso = korkeampi_taso(taso, uusi)
        perusteet.append(peruste)

    hatamerkit = _keraa(havainnot, "hata")
    if hatamerkki and hatamerkki not in hatamerkit:
        hatamerkit.append(hatamerkki)
    vakavat = _keraa(havainnot, "vakavat")
    for merkki in hatamerkit:
        nosta("punainen", f"hätämerkki: {SELKONIMET.get(merkki, merkki.replace('_', ' '))}")
    for huoli in vakavat:
        nosta("punainen", f"vakava huoli: {SELKONIMET.get(huoli, huoli)}")
    if tukitila:
        nosta("punainen", f"puhelu jäi tukitilaan: {tukitila}")
    for oire in _keraa(havainnot, "huolestuttavat"):
        nosta("keltainen", f"huolestuttava oire: {oire}")
    # Oireen tunnistus ohittaa kieltomuodon ("ei enää huimaa"), joten tämä on jatkuva oire.
    tunnistetut = _keraa(havainnot, "oireet")
    toistuvat = [oire for oire in tunnistetut if oire in edelliset_oireet]
    for oire in toistuvat:
        nosta("keltainen", f"sama oire jatkuu edellisestä puhelusta: {oire}")
    if _keraa(havainnot, "huoli"):
        nosta("keltainen", "henkilö ilmaisi huolta tai pelkoa")
    for sana in _keraa(havainnot, "keltainen"):
        nosta("keltainen", f"huomiosana: {sana}")
    if loppu == PuhelunLoppu.HALUAA_HOITAJAN:
        nosta("keltainen", "henkilö pyysi hoitajaa")
    if any(vastaus.kysymys_hoitajalle for vastaus in vastaukset):
        nosta("keltainen", "henkilö kysyi lääkeneuvoa: hoitajan vastattava")
    if epaselvia:
        nosta("keltainen", f"{epaselvia} vastausta jäi epävarmaksi")
    if loppu == PuhelunLoppu.KESKEYTYI:
        nosta("keltainen", "puhelu katkesi kesken")
    uhkauksia = sum(havainto.uhkaus for havainto in havainnot)
    if uhkauksia:
        nosta("keltainen", "henkilö uhkaili puhelussa")
    loukkauksia = sum(havainto.loukkaus for havainto in havainnot)
    if loukkauksia >= tietopohjan_saannot()["loukkaus"]["nostaa_keltaiseksi"]:
        # Äkillinen vihaisuus voi kertoa kivusta, pelosta tai sekavuudesta (Jere).
        nosta("keltainen", f"henkilö puhui loukkaavasti {loukkauksia} kertaa")

    return Saantoloydokset(
        taso=Kiireellisyys(taso),
        perusteet=perusteet,
        hatamerkit=hatamerkit,
        hatamerkit_kieltomuodossa=_keraa(havainnot, "hata_kielletty"),
        vakavat_huolet=vakavat,
        huolen_ilmaukset=_keraa(havainnot, "huoli"),
        huomiosanat=_keraa(havainnot, "keltainen"),
        tunnistetut_oireet=tunnistetut,
        huolestuttavat_oireet=_keraa(havainnot, "huolestuttavat"),
        toistuvat_oireet=toistuvat,
        loukkaavia_puheenvuoroja=loukkauksia,
        uhkauksia=uhkauksia,
    )
