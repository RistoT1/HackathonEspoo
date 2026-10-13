"""Muisti edellisestä puhelusta: saman henkilön (pseudonyymi vanhus_id) uusin
hoitajayhteenveto tiivistetään agentille ("viime kerralla huimasi, onko nyt parempi?")
ja hoitajan näkymään. Triage nostaa tason, jos sama oire toistuu (triage/saannot.py).

Datan minimointi: luetaan vain jo tallennettu yhteenveto (puhelun_tulos), ei litterointia.
Mallille menee vain oire, voimakkuus ja montako päivää puhelusta on. Vakavat huolet
(esim. itsetuhoisuus) eivät mene agentille: ne ovat hoitajan asia. Säilytysraja
MUISTI_PAIVAT. Tiivistys on deterministinen (ei LLM:ää).
"""

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import hae_asetukset
from app.mallit import EdellinenPuhelu, Puhelukonteksti, PuhelunLoppu, Puhelutehtava, Yhteenveto
from app.tietokanta import PuhelunTulosRivi, PuhelutehtavaRivi

MAX_KUVAUKSIA = 2  # agentti kysyy yhdestä asiasta; kaksi riittää taustaksi
_HAKURAJA = 20  # uusimmat tulokset, joista etsitään viimeisin keskustelu

# Puhelut, joissa ei keskusteltu: niistä ei ole muistettavaa, joten ne ohitetaan.
_EI_KESKUSTELUA = frozenset({PuhelunLoppu.EI_VASTANNUT.value, PuhelunLoppu.EI_VASTAUSTA.value})


def _utc(aika: datetime) -> datetime:
    """SQLite palauttaa ajan ilman aikavyöhykettä; tallennetut ajat ovat UTC:tä."""
    if aika.tzinfo is None:
        return aika.replace(tzinfo=UTC)
    return aika


def _kuvaukset(yhteenveto: Yhteenveto) -> list[str]:
    """Mallin analyysin oireet voimakkuuksineen; ilman analyysia sääntöjen oireet."""
    kuvaukset: list[str] = []
    for oire in yhteenveto.oireet:
        voimakkuus_tiedossa = oire.voimakkuus not in ("", "ei tiedossa")
        kuvaus = f"{oire.oire} ({oire.voimakkuus})" if voimakkuus_tiedossa else oire.oire
        kuvaukset.append(kuvaus)
    if not kuvaukset:
        kuvaukset = list(yhteenveto.saanto.tunnistetut_oireet)
    return kuvaukset[:MAX_KUVAUKSIA]


def tiivista(yhteenveto: Yhteenveto, paattyi: datetime, nyt: datetime) -> EdellinenPuhelu | None:
    """Puhdas funktio. None = ei oireita, joista kysyä uudelleen, tai vakava huoli."""
    if yhteenveto.saanto.vakavat_huolet:
        return None
    kuvaukset = _kuvaukset(yhteenveto)
    if not kuvaukset:
        return None
    paivia_sitten = max(0, (_utc(nyt) - _utc(paattyi)).days)
    return EdellinenPuhelu(
        paivia_sitten=paivia_sitten,
        taso=yhteenveto.taso.value,
        oireet=list(yhteenveto.saanto.tunnistetut_oireet),
        kuvaukset=kuvaukset,
        voimakkuudet={
            oire.oire: oire.voimakkuus
            for oire in yhteenveto.oireet
            if oire.voimakkuus not in ("", "ei tiedossa")
        },
    )


def hae_edellinen_puhelu(
    sessio: Session,
    vanhus_id: str,
    nyt: datetime | None = None,
    ennen: datetime | None = None,
    paivat: int | None = None,
) -> EdellinenPuhelu | None:
    """Saman henkilön viimeisin keskustelu säilytysrajan sisällä. Jos siinä ei kerrottu
    oireita, palautetaan None (vanhempaa ei kaiveta esiin). ennen = vain tätä aiemmat
    puhelut (hoitajan näkymä: tehtävän oma puhelu ei ole "edellinen")."""
    paivat = hae_asetukset().muisti_paivat if paivat is None else paivat
    if paivat <= 0:
        return None
    nyt = _utc(nyt or datetime.now(UTC))
    raja = nyt - timedelta(days=paivat)

    rivit = sessio.scalars(
        select(PuhelunTulosRivi)
        .join(PuhelutehtavaRivi, PuhelutehtavaRivi.id == PuhelunTulosRivi.tehtava_id)
        .where(
            PuhelutehtavaRivi.vanhus_id == vanhus_id,
            PuhelunTulosRivi.paattyi.is_not(None),
            PuhelunTulosRivi.yhteenveto.is_not(None),
        )
        .order_by(PuhelunTulosRivi.paattyi.desc())
        .limit(_HAKURAJA)
    )
    for rivi in rivit:
        paattyi = _utc(rivi.paattyi)
        liian_uusi = ennen is not None and paattyi >= _utc(ennen)
        if liian_uusi or rivi.loppu in _EI_KESKUSTELUA:
            continue
        if paattyi < raja:
            return None
        try:
            yhteenveto = Yhteenveto.model_validate(rivi.yhteenveto)
        except ValidationError:
            continue
        return tiivista(yhteenveto, paattyi, nyt)
    return None


def konteksti_muistilla(
    sessio: Session, tehtava: Puhelutehtava, nyt: datetime | None = None
) -> Puhelukonteksti:
    """Tehtävän konteksti + edellinen puhelu. Liitetään soiton alkaessa, ei tallenneta."""
    edellinen = hae_edellinen_puhelu(sessio, tehtava.vanhus_id, nyt)
    return tehtava.konteksti.model_copy(update={"edellinen_puhelu": edellinen})
