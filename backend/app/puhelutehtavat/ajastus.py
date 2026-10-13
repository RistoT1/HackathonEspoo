"""Ajastettu muutosten tunnistus (APScheduler). Paalla, kun TUNNISTUS_VALI_MINUUTIT > 0."""

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import hae_asetukset
from app.data.adapteri import DataEiSaatavilla, hae_datalahde
from app.dev.henkilotiedot import tayta_puuttuvat
from app.puhelutehtavat.palvelu import TunnistusKaynnissa, tunnista_ja_jonouta
from app.tietokanta import hae_sessiotehdas

logger = logging.getLogger(__name__)

IKKUNA_PAIVIA = 90
MIN_IKA = 65


def aja_tunnistus() -> None:
    datalahde = hae_datalahde()
    try:
        viitepaiva = datalahde.uusin_paiva()
    except DataEiSaatavilla as virhe:
        logger.warning("Tunnistus ohitettiin: %s", virhe)
        return
    with hae_sessiotehdas()() as sessio:
        try:
            tulos = tunnista_ja_jonouta(datalahde, sessio, viitepaiva, IKKUNA_PAIVIA, MIN_IKA)
        except TunnistusKaynnissa:
            logger.warning("Tunnistus ohitettiin: rinnakkainen ajo")
            return
        if hae_asetukset().dev_tila:
            # VAIN DEV: uusille jonossa oleville synteettinen nimi (dev/henkilotiedot.py).
            tayta_puuttuvat(sessio)
    # Vain lukumaarat lokiin, ei tunnisteita (datan minimointi).
    logger.info(
        "Tunnistus valmis: luotuja=%d paivitettyja=%d uusia_muutoksia=%d",
        tulos.luotuja,
        tulos.paivitettyja,
        tulos.uusia_muutoksia,
    )


def kaynnista_ajastin(vali_minuutit: int) -> BackgroundScheduler:
    ajastin = BackgroundScheduler(timezone="UTC")
    ajastin.add_job(
        aja_tunnistus,
        "interval",
        minutes=vali_minuutit,
        max_instances=1,
        coalesce=True,
        id="laakemuutosten_tunnistus",
        next_run_time=datetime.now(UTC),  # ensimmainen skannaus heti kaynnistyksessa
    )
    ajastin.start()
    return ajastin
