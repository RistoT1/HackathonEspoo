"""Hoitajan näkymän ajat Suomen aikaan. Kanta ja rajapinnat pysyvät UTC:ssä; muunnos tehdään
vasta näytettäessä (sivupohjien |aika-suodatin ja hoitajalle muotoillut tekstit), joten kesä-
ja talviaika menevät oikein eikä tallennettua dataa tarvitse muuttaa.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

SUOMEN_AIKA = ZoneInfo("Europe/Helsinki")


def suomen_aika(aika: datetime) -> datetime:
    """SQLite palauttaa ajan ilman aikavyöhykettä; tallennetut ajat ovat UTC:tä."""
    if aika.tzinfo is None:
        aika = aika.replace(tzinfo=UTC)
    return aika.astimezone(SUOMEN_AIKA)


def muotoile(aika: datetime | None, muoto: str) -> str:
    """Jinja-suodatin: {{ tapaus.luotu | aika("%d.%m.%Y klo %H:%M") }}. None -> tyhjä."""
    if aika is None:
        return ""
    return suomen_aika(aika).strftime(muoto)
