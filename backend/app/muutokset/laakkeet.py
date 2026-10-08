"""Laakemuutosten tunnistus. Deterministinen, puhtaat funktiot: ei IO:ta, ei LLM:aa.

Ikkuna on (viitepaiva - ikkuna_paivia, viitepaiva]. Viitepaivan jalkeiset tapahtumat ohitetaan.
"""

import re
from datetime import datetime, timedelta

from app.mallit import Laake, Laakejakso, Laakemuutos, MuutosTyyppi

# Jos saman koodin edellinen jakso paattyi enintaan taman verran ennen uuden alkua (tai jatkuu),
# kyse on uusinnasta eika uudesta laakkeesta. Sama raja annosmuutoksen jaksojen valilla.
UUSINTA_TOLERANSSI_PV = 7

# Synthean nimen alussa: rekisterinumero ("NDA020800 "), "Abuse-Deterrent " ja maarat
# ("24 HR ", "10 ML ", "0.25 ML ", "60 ACTUAT "); lopussa brandi "[Epogen]". Eivat ainesosaa.
_REKISTERI_ETULIITE = re.compile(r"^(NDA\d+|Abuse-Deterrent)\s+", re.IGNORECASE)
_MAARA_ETULIITE = re.compile(r"^(\d[\d.]*\s*[A-Za-z]*\s+)+")
_BRANDI = re.compile(r"\[[^\]]*\]")
_ENSIMMAINEN_NUMERO = re.compile(r"\d")
_VALIT = re.compile(r"\s+")


def ainesosa(nimi: str) -> str:
    """Ainesosa laakkeen nimesta: teksti ennen ensimmaista vahvuutta, pienilla.

    Heuristiikka annosmuutoksen tunnistukseen. Yhdistelmavalmisteet tunnistuvat ensimmaisen
    ainesosan perusteella (esim. "Amlodipine 5 MG / Olmesartan ..." -> "amlodipine").
    """
    puhdas = _BRANDI.sub(" ", nimi)
    puhdas = _REKISTERI_ETULIITE.sub("", puhdas.strip())
    ilman_etuliitetta = _MAARA_ETULIITE.sub("", puhdas)
    osumat = _ENSIMMAINEN_NUMERO.search(ilman_etuliitetta)
    loppu = osumat.start() if osumat else len(ilman_etuliitetta)
    tulos = _VALIT.sub(" ", ilman_etuliitetta[:loppu]).strip().lower()
    if not tulos:
        return nimi.strip().lower()
    return tulos


def on_kaytossa(jakso: Laakejakso, viitepaiva: datetime) -> bool:
    alkanut = jakso.alkoi <= viitepaiva
    ei_paattynyt = jakso.paattyi is None or jakso.paattyi > viitepaiva
    return alkanut and ei_paattynyt


def _laake(jakso: Laakejakso, viitepaiva: datetime) -> Laake:
    return Laake(**jakso.model_dump(), kaytossa=on_kaytossa(jakso, viitepaiva))


def laakkeet_viitepaivana(jaksot: list[Laakejakso], viitepaiva: datetime) -> list[Laake]:
    """Laakehistoria viitepaivaan asti, uusin ensin. Tulevat jaksot jatetaan pois."""
    alkaneet = [jakso for jakso in jaksot if jakso.alkoi <= viitepaiva]
    alkaneet.sort(key=lambda jakso: jakso.alkoi, reverse=True)
    return [_laake(jakso, viitepaiva) for jakso in alkaneet]


def _on_uusinta(jakso: Laakejakso, jaksot: list[Laakejakso]) -> bool:
    raja = jakso.alkoi - timedelta(days=UUSINTA_TOLERANSSI_PV)
    for aiempi in jaksot:
        if aiempi is jakso or aiempi.koodi != jakso.koodi or aiempi.alkoi >= jakso.alkoi:
            continue
        if aiempi.paattyi is None or aiempi.paattyi >= raja:
            return True
    return False


def _jatkuu_samalla_koodilla(
    jakso: Laakejakso, jaksot: list[Laakejakso], viitepaiva: datetime
) -> bool:
    raja = jakso.paattyi + timedelta(days=UUSINTA_TOLERANSSI_PV)
    for muu in jaksot:
        if muu is jakso or muu.koodi != jakso.koodi:
            continue
        if on_kaytossa(muu, viitepaiva):
            return True
        jatkoa = jakso.alkoi < muu.alkoi <= min(raja, viitepaiva)
        if jatkoa:
            return True
    return False


# Tayspitka ATC (7 merkkia) yksiloi vaikuttavan aineen; lyhyempi on ryhmataso.
ATC_AINE_PITUUS = 7


def sama_aine(a: Laakejakso, b: Laakejakso) -> bool:
    """Sama vaikuttava aine: ensisijaisesti ATC (tuotannon tapa), muuten nimen ainesosa."""
    molemmilla_aine_atc = (
        a.atc is not None
        and b.atc is not None
        and len(a.atc) == ATC_AINE_PITUUS
        and len(b.atc) == ATC_AINE_PITUUS
    )
    if molemmilla_aine_atc:
        return a.atc == b.atc
    return ainesosa(a.nimi) == ainesosa(b.nimi)


def _edeltava_eri_vahvuus(jakso: Laakejakso, jaksot: list[Laakejakso]) -> Laakejakso | None:
    """Annosmuutos: sama aine, eri valmistekoodi, aiempi jakso paattyi uuden alkaessa."""
    toleranssi = timedelta(days=UUSINTA_TOLERANSSI_PV)
    ehdokkaat = [
        aiempi
        for aiempi in jaksot
        if aiempi.koodi != jakso.koodi
        and aiempi.alkoi < jakso.alkoi
        and aiempi.paattyi is not None
        and abs(aiempi.paattyi - jakso.alkoi) <= toleranssi
        and sama_aine(aiempi, jakso)
    ]
    if not ehdokkaat:
        return None
    return max(ehdokkaat, key=lambda aiempi: aiempi.paattyi)


def _pvm(aika: datetime) -> str:
    return f"{aika.day}.{aika.month}.{aika.year}"


def _muutos(
    tyyppi: MuutosTyyppi,
    jakso: Laakejakso,
    tapahtui: datetime,
    viitepaiva: datetime,
    kuvaus: str,
    aiempi: Laakejakso | None = None,
) -> Laakemuutos:
    paivia_sitten = (viitepaiva - tapahtui).days
    aiempi_laake = _laake(aiempi, viitepaiva) if aiempi else None
    return Laakemuutos(
        tyyppi=tyyppi,
        laake=_laake(jakso, viitepaiva),
        aiempi_laake=aiempi_laake,
        tapahtui=tapahtui,
        paivia_sitten=paivia_sitten,
        kuvaus=kuvaus,
    )


def tunnista_laakemuutokset(
    jaksot: list[Laakejakso], viitepaiva: datetime, ikkuna_paivia: int
) -> list[Laakemuutos]:
    """Rakenteinen muutoslista ikkunasta, uusin ensin."""
    ikkunan_alku = viitepaiva - timedelta(days=ikkuna_paivia)
    muutokset: list[Laakemuutos] = []
    kasitellyt_paattyneet: list[Laakejakso] = []  # annosmuutoksen aiemmat ja lyhyet kuurit

    alkaneet = [jakso for jakso in jaksot if ikkunan_alku < jakso.alkoi <= viitepaiva]
    for jakso in alkaneet:
        if _on_uusinta(jakso, jaksot):
            continue

        aiempi = _edeltava_eri_vahvuus(jakso, jaksot)
        if aiempi is not None:
            kasitellyt_paattyneet.append(aiempi)
            kuvaus = f"Lääkitys muuttunut {_pvm(jakso.alkoi)}: {aiempi.nimi} -> {jakso.nimi}"
            muutokset.append(
                _muutos(MuutosTyyppi.ANNOSMUUTOS, jakso, jakso.alkoi, viitepaiva, kuvaus, aiempi)
            )
            continue

        paattyi_ikkunassa = jakso.paattyi is not None and jakso.paattyi <= viitepaiva
        if paattyi_ikkunassa and not _jatkuu_samalla_koodilla(jakso, jaksot, viitepaiva):
            kasitellyt_paattyneet.append(jakso)
            kuvaus = f"Lyhyt lääkekuuri {_pvm(jakso.alkoi)}–{_pvm(jakso.paattyi)}: {jakso.nimi}"
            muutokset.append(
                _muutos(MuutosTyyppi.LYHYT_KUURI, jakso, jakso.alkoi, viitepaiva, kuvaus)
            )
            continue

        kuvaus = f"Uusi lääke aloitettu {_pvm(jakso.alkoi)}: {jakso.nimi}"
        muutokset.append(_muutos(MuutosTyyppi.UUSI_LAAKE, jakso, jakso.alkoi, viitepaiva, kuvaus))

    paattyneet = [
        jakso
        for jakso in jaksot
        if jakso.paattyi is not None and ikkunan_alku < jakso.paattyi <= viitepaiva
    ]
    for jakso in paattyneet:
        jo_kasitelty = any(jakso is kasitelty for kasitelty in kasitellyt_paattyneet)
        if jo_kasitelty or _jatkuu_samalla_koodilla(jakso, jaksot, viitepaiva):
            continue

        kuvaus = f"Lääke lopetettu {_pvm(jakso.paattyi)}: {jakso.nimi}"
        muutokset.append(
            _muutos(MuutosTyyppi.LOPETETTU_LAAKE, jakso, jakso.paattyi, viitepaiva, kuvaus)
        )

    muutokset.sort(key=lambda muutos: muutos.tapahtui, reverse=True)
    return muutokset
