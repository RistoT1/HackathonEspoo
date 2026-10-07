"""VAIN DEV: testimuutos kantaan ja heti perään tunnistus, jotta nähdään huomaako se muutoksen."""

import uuid
from datetime import datetime, timedelta

from app.data.adapteri import Datalahde, Testikirjoittaja
from app.mallit import MuutosTyyppi, PotilaanLaakemuutokset, Testimuutos
from app.muutokset.palvelu import hae_potilaan_laakemuutokset


class TestimuutosVirhe(ValueError):
    __test__ = False  # pytest: ei testiluokka


def _uusi_koodi() -> str:
    return f"DEV-{uuid.uuid4().hex[:8]}"


def tee_testimuutos(
    datalahde: Datalahde,
    kirjoittaja: Testikirjoittaja,
    pyynto: Testimuutos,
    viitepaiva: datetime,
    ikkuna_paivia: int,
) -> PotilaanLaakemuutokset | None:
    """None jos vanhusta ei ole. TestimuutosVirhe jos lopetettavaa laaketta ei ole kaytossa."""
    if datalahde.hae_vanhus(pyynto.vanhus_id) is None:
        return None

    paiva = viitepaiva - timedelta(days=pyynto.paivia_sitten)
    lopetetaan = pyynto.tyyppi in (MuutosTyyppi.LOPETETTU_LAAKE, MuutosTyyppi.ANNOSMUUTOS)
    lisataan = pyynto.tyyppi in (MuutosTyyppi.UUSI_LAAKE, MuutosTyyppi.ANNOSMUUTOS)

    if lopetetaan:
        loytyi = kirjoittaja.lopeta_laake(
            pyynto.vanhus_id, pyynto.lopetettava_koodi, paiva, viitepaiva
        )
        if not loytyi:
            raise TestimuutosVirhe(
                f"Koodilla {pyynto.lopetettava_koodi} ei ole kaytossa olevaa laaketta"
            )

    if lisataan:
        koodi = pyynto.koodi or _uusi_koodi()
        kirjoittaja.lisaa_laake(pyynto.vanhus_id, koodi, pyynto.nimi, paiva)

    return hae_potilaan_laakemuutokset(datalahde, pyynto.vanhus_id, viitepaiva, ikkuna_paivia)
