"""Palvelukerros: yhdistaa datalahteen ja puhtaat tunnistusfunktiot.

Muut moduulit (esim. kysymykset/) kayttavat naita funktioita suoraan Pythonissa, eivat HTTP:lla.
"""

from datetime import UTC, date, datetime

from app.data.adapteri import Datalahde
from app.mallit import Laakejakso, PotilaanLaakemuutokset, Vanhus, VanhusLyhyt
from app.muutokset.laakkeet import laakkeet_viitepaivana, tunnista_laakemuutokset


def ratkaise_viitepaiva(annettu: datetime | None, datalahde: Datalahde) -> datetime:
    """'Nyt' tunnistukselle. Oletus datan uusin paiva, koska demodata on historiallista."""
    if annettu is None:
        return datalahde.uusin_paiva()
    if annettu.tzinfo is None:
        return annettu.replace(tzinfo=UTC)
    return annettu


def laske_ika(syntymapaiva: date, viitepaiva: date) -> int:
    synttarit_kayty = (viitepaiva.month, viitepaiva.day) >= (syntymapaiva.month, syntymapaiva.day)
    vuodet = viitepaiva.year - syntymapaiva.year
    if synttarit_kayty:
        return vuodet
    return vuodet - 1


def valitse_vanhukset(
    vanhukset: list[Vanhus], viitepaiva: datetime, min_ika: int
) -> list[VanhusLyhyt]:
    """Elossa viitepaivana ja vahintaan min_ika vuotta. Vain pseudonymisoidut kentat."""
    paiva = viitepaiva.date()
    valitut: list[VanhusLyhyt] = []
    for vanhus in vanhukset:
        elossa = vanhus.kuolinpaiva is None or vanhus.kuolinpaiva > paiva
        ika = laske_ika(vanhus.syntymapaiva, paiva)
        if not elossa or ika < min_ika:
            continue
        valitut.append(VanhusLyhyt(vanhus_id=vanhus.vanhus_id, ika=ika, sukupuoli=vanhus.sukupuoli))
    return valitut


def koosta_laakemuutokset(
    vanhus_id: str,
    jaksot: list[Laakejakso],
    viitepaiva: datetime,
    ikkuna_paivia: int,
    ika: int | None = None,
) -> PotilaanLaakemuutokset:
    laakkeet = laakkeet_viitepaivana(jaksot, viitepaiva)
    aktiiviset = [laake for laake in laakkeet if laake.kaytossa]
    muutokset = tunnista_laakemuutokset(jaksot, viitepaiva, ikkuna_paivia)
    return PotilaanLaakemuutokset(
        vanhus_id=vanhus_id,
        viitepaiva=viitepaiva,
        ikkuna_paivia=ikkuna_paivia,
        aktiiviset_laakkeet=aktiiviset,
        muutokset=muutokset,
        ika=ika,
    )


def hae_potilaan_laakemuutokset(
    datalahde: Datalahde, vanhus_id: str, viitepaiva: datetime, ikkuna_paivia: int
) -> PotilaanLaakemuutokset | None:
    """None jos vanhusta ei ole. Taman paalle rakentuu kysymysten generointi."""
    vanhus = datalahde.hae_vanhus(vanhus_id)
    if vanhus is None:
        return None
    ika = laske_ika(vanhus.syntymapaiva, viitepaiva.date())
    jaksot = datalahde.hae_laakejaksot(vanhus_id)
    return koosta_laakemuutokset(vanhus_id, jaksot, viitepaiva, ikkuna_paivia, ika)


def hae_kaikki_laakemuutokset(
    datalahde: Datalahde, viitepaiva: datetime, ikkuna_paivia: int, min_ika: int
) -> list[PotilaanLaakemuutokset]:
    """Vanhukset, joilla on muutoksia ikkunassa: laukaisinlista soitoille."""
    vanhukset = valitse_vanhukset(datalahde.hae_vanhukset(), viitepaiva, min_ika)
    vanhus_idt = [vanhus.vanhus_id for vanhus in vanhukset]
    jaksot_vanhuksittain = datalahde.hae_laakejaksot_monelle(vanhus_idt)

    tulokset: list[PotilaanLaakemuutokset] = []
    for vanhus in vanhukset:
        jaksot = jaksot_vanhuksittain.get(vanhus.vanhus_id, [])
        tulos = koosta_laakemuutokset(
            vanhus.vanhus_id, jaksot, viitepaiva, ikkuna_paivia, vanhus.ika
        )
        if tulos.muutokset:
            tulokset.append(tulos)
    return tulokset
