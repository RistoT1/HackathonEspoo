"""Hoitajan näkymän kantaoperaatiot: tallennetut puhelun tulokset ja katseluloki.

Kannassa on puhelusta vain hoitajan yhteenveto, ei raakavastauksia eikä litterointia.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mallit import PuhelunLoppu, TallennettuTulos, Yhteenveto
from app.tietokanta import KatseluLokiRivi, PuhelunTulosRivi, PuhelutehtavaRivi

# Hackathonissa ei ole kirjautumista: katsojaa ei tunnisteta (tuotannossa Entra ID).
KATSOJA = "hackathon-demo (kirjautumaton)"
_AIKOJEN_ALKU = datetime.min.replace(tzinfo=UTC)


def _utc(aika: datetime | None) -> datetime | None:
    """SQLite palauttaa aikaleimat ilman aikavyöhykettä; tallennus on aina UTC."""
    if aika is None or aika.tzinfo is not None:
        return aika
    return aika.replace(tzinfo=UTC)


def _tulos(rivi: PuhelunTulosRivi) -> TallennettuTulos:
    # JSON-sarakkeen None tallentuu JSON-nulliksi, ei SQL NULLiksi: kumpikin = ei yhteenvetoa.
    yhteenveto = Yhteenveto.model_validate(rivi.yhteenveto) if rivi.yhteenveto else None
    loppu = PuhelunLoppu(rivi.loppu) if rivi.loppu is not None else None
    return TallennettuTulos(
        istunto_id=rivi.istunto_id,
        tehtava_id=rivi.tehtava_id,
        loppu=loppu,
        paattyi=_utc(rivi.paattyi),
        yhteenveto=yhteenveto,
    )


def _paattymisaika(tulos: TallennettuTulos) -> datetime:
    return tulos.paattyi or _AIKOJEN_ALKU


def uusimmat_tulokset(sessio: Session, tehtava_idt: list[str]) -> dict[str, TallennettuTulos]:
    """Tehtävän uusin tallennettu puhelun tulos. Tehtävä ilman tulosta puuttuu tuloksesta.

    Tulos ilman päättymisaikaa on vanhin. Järjestys tässä eikä SQL:ssä, koska Postgres
    järjestää NULLit DESC-järjestyksessä ensimmäisiksi ja SQLite viimeisiksi.
    """
    if not tehtava_idt:
        return {}
    rivit = sessio.scalars(
        select(PuhelunTulosRivi).where(PuhelunTulosRivi.tehtava_id.in_(tehtava_idt))
    ).all()

    uusimmat: dict[str, TallennettuTulos] = {}
    for tulos in sorted(map(_tulos, rivit), key=_paattymisaika):
        uusimmat[tulos.tehtava_id] = tulos
    return uusimmat


def vanhuksen_tulokset(sessio: Session, vanhus_id: str) -> list[TallennettuTulos]:
    """Saman henkilön kaikkien tehtävien tallennetut tulokset (tapaussivun oirehistoria)."""
    rivit = sessio.scalars(
        select(PuhelunTulosRivi)
        .join(PuhelutehtavaRivi, PuhelutehtavaRivi.id == PuhelunTulosRivi.tehtava_id)
        .where(PuhelutehtavaRivi.vanhus_id == vanhus_id)
    ).all()
    return sorted(map(_tulos, rivit), key=_paattymisaika)


def kaikki_tulokset(sessio: Session) -> list[TallennettuTulos]:
    """Kaikki tallennetut puhelun tulokset (potilaslista), vanhin ensin."""
    rivit = sessio.scalars(select(PuhelunTulosRivi)).all()
    return sorted(map(_tulos, rivit), key=_paattymisaika)


def kirjaa_katselu(sessio: Session, tehtava_id: str) -> None:
    """Audit-loki tehtävänäkymän katselusta (SAANTELY.md: tietoturva)."""
    sessio.add(KatseluLokiRivi(tehtava_id=tehtava_id, katsoja=KATSOJA, toiminto="katselu"))
    sessio.commit()


def kirjaa_potilaan_katselu(sessio: Session, vanhus_id: str) -> None:
    """Audit-loki potilassivun katselusta: henkilön kaikki tehtävät ja puhelut näkyvät."""
    sessio.add(KatseluLokiRivi(vanhus_id=vanhus_id, katsoja=KATSOJA, toiminto="potilassivu"))
    sessio.commit()
