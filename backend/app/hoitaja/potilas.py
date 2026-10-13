"""Potilassivu: yhden henkilön (pseudonyymi vanhus_id) tiedot hoitajalle yhdellä sivulla.

Perustiedot ja lääkitys adapterista (data/adapteri.py), soittotehtävät ja puhelut omasta
kannasta. Oirekaaviot ovat samat kuin tapaussivulla (historia.py, osat/oirehistoria.html).
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mallit import (
    Laake,
    Laakejakso,
    Potilasrivi,
    Potilastiedot,
    Puhelutehtava,
    PuhelutehtavanTila,
    TallennettuTulos,
    Vanhus,
)
from app.muutokset.laakkeet import laakkeet_viitepaivana
from app.muutokset.palvelu import laske_ika
from app.puhelutehtavat import varasto
from app.tietokanta import PuhelutehtavaRivi

# Synthean koodit; tuntematon arvo näytetään sellaisenaan.
SUKUPUOLET = {"F": "nainen", "M": "mies"}
AVOIMET = (PuhelutehtavanTila.ODOTTAA, PuhelutehtavanTila.SOITETAAN)


Tunnistetiedot = dict[str, tuple[str | None, str | None]]  # vanhus_id -> (nimi, hetu)


def tunnistetiedot(sessio: Session, vanhus_idt: list[str]) -> Tunnistetiedot:
    """Nimet ja henkilötunnukset erillisestä taulusta (vanhuksen_henkilotieto)."""
    rivit = varasto.hae_henkilotiedot(sessio, vanhus_idt)
    return {
        vanhus_id: (koko_nimi(rivi.etunimi, rivi.sukunimi), rivi.hetu)
        for vanhus_id, rivi in rivit.items()
    }


def koko_nimi(etunimi: str | None, sukunimi: str | None) -> str | None:
    nimi = " ".join(osa for osa in (etunimi, sukunimi) if osa)
    return nimi or None


def kaikki_tehtavat(sessio: Session) -> list[Puhelutehtava]:
    """Kaikki soittotehtävät potilaslistaa varten."""
    return [varasto.dto(rivi) for rivi in sessio.scalars(select(PuhelutehtavaRivi))]


def potilasrivit(
    tehtavat: list[Puhelutehtava],
    tulokset: list[TallennettuTulos],
    tunnisteet: Tunnistetiedot | None = None,
) -> list[Potilasrivi]:
    """Puhdas funktio. Yksi rivi per henkilö, jolla on soittotehtäviä; tuorein tapahtuma
    (puhelu tai uusi tehtävä) ensin."""
    henkilon_tehtavat: dict[str, list[Puhelutehtava]] = {}
    for tehtava in tehtavat:
        henkilon_tehtavat.setdefault(tehtava.vanhus_id, []).append(tehtava)
    tehtavan_henkilo = {tehtava.id: tehtava.vanhus_id for tehtava in tehtavat}
    henkilon_puhelut: dict[str, list[TallennettuTulos]] = {}
    for tulos in tulokset:
        vanhus_id = tehtavan_henkilo.get(tulos.tehtava_id)
        if vanhus_id is None or tulos.paattyi is None:
            continue
        henkilon_puhelut.setdefault(vanhus_id, []).append(tulos)

    rivit: list[tuple[datetime, Potilasrivi]] = []
    for vanhus_id, omat in henkilon_tehtavat.items():
        uusin_tehtava = max(omat, key=lambda tehtava: tehtava.luotu)
        puhelut = sorted(henkilon_puhelut.get(vanhus_id, []), key=lambda tulos: tulos.paattyi)
        arvioidut = [tulos for tulos in puhelut if tulos.yhteenveto is not None]
        viimeisin_puhelu = puhelut[-1].paattyi if puhelut else None
        tuorein = max(uusin_tehtava.luotu, viimeisin_puhelu or uusin_tehtava.luotu)
        nimi, hetu = (tunnisteet or {}).get(vanhus_id, (None, None))
        rivi = Potilasrivi(
            vanhus_id=vanhus_id,
            nimi=nimi,
            hetu=hetu,
            ika=uusin_tehtava.ika,
            tehtavia=len(omat),
            avoimia=sum(tehtava.tila in AVOIMET for tehtava in omat),
            puheluita=len(puhelut),
            viimeisin_puhelu=viimeisin_puhelu,
            viimeisin_taso=arvioidut[-1].yhteenveto.taso if arvioidut else None,
        )
        rivit.append((tuorein, rivi))
    return [rivi for _, rivi in sorted(rivit, key=lambda pari: pari[0], reverse=True)]


def vanhuksen_tehtavat(sessio: Session, vanhus_id: str) -> list[Puhelutehtava]:
    """Henkilön kaikki soittotehtävät, uusin ensin."""
    rivit = sessio.scalars(
        select(PuhelutehtavaRivi)
        .where(PuhelutehtavaRivi.vanhus_id == vanhus_id)
        .order_by(PuhelutehtavaRivi.luotu.desc())
    )
    return [varasto.dto(rivi) for rivi in rivit]


def potilastiedot(
    vanhus_id: str,
    vanhus: Vanhus | None,
    jaksot: list[Laakejakso] | None,
    viitepaiva: datetime | None,
    nimi: str | None = None,
    hetu: str | None = None,
) -> Potilastiedot:
    """Puhdas funktio. vanhus/jaksot None = terveysdata ei saatavilla (sivu näyttää silti
    tehtävät ja puhelut). Lääke näytetään kerran: uusinnat ovat datassa omia rivejään."""
    if vanhus is None or jaksot is None or viitepaiva is None:
        return Potilastiedot(
            vanhus_id=vanhus_id, nimi=nimi, hetu=hetu, terveysdata_saatavilla=False
        )

    kaytossa = [laake for laake in laakkeet_viitepaivana(jaksot, viitepaiva) if laake.kaytossa]
    uusimmat: dict[str, Laake] = {}
    for laake in kaytossa:  # uusin ensin
        uusimmat.setdefault(laake.koodi, laake)

    return Potilastiedot(
        vanhus_id=vanhus_id,
        nimi=nimi,
        hetu=hetu,
        ika=laske_ika(vanhus.syntymapaiva, viitepaiva.date()),
        sukupuoli=SUKUPUOLET.get(vanhus.sukupuoli, vanhus.sukupuoli),
        viitepaiva=viitepaiva,
        aktiiviset_laakkeet=sorted(uusimmat.values(), key=lambda laake: laake.nimi.lower()),
    )
