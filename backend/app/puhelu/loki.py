"""VAIN DEV: puhelun vuorokohtainen loki vianetsintaan (mita kuultiin, mika paatti, mita sanottiin).

Sisaltaa litteroinnin, joten se on oletuksena pois ja toimii vain DEV_TILA=true +
PUHELU_LOKI=true. Vain synteettisiin testipuheluihin; ei tuotantoon (datan minimointi).
Tiedostot: <PUHELU_LOKIKANSIO>/<paiva>_<istunto>.jsonl, kansio on .gitignoressa.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

from fastapi import Depends

from app.config import Settings, hae_asetukset

logger = logging.getLogger(__name__)


class Puheluloki:
    def __init__(self, kansio: Path) -> None:
        self._kansio = kansio
        self._lukko = Lock()

    def tiedosto(self, istunto_id: str) -> Path:
        paiva = datetime.now(UTC).strftime("%Y%m%d")
        return self._kansio / f"{paiva}_{istunto_id[:8]}.jsonl"

    def kirjaa(self, istunto_id: str, tapahtuma: str, **tiedot: Any) -> None:
        rivi = {
            "aika": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "istunto": istunto_id,
            "tapahtuma": tapahtuma,
            **tiedot,
        }
        try:
            with self._lukko:
                self._kansio.mkdir(parents=True, exist_ok=True)
                with self.tiedosto(istunto_id).open("a", encoding="utf-8") as tiedosto:
                    tiedosto.write(json.dumps(rivi, ensure_ascii=False, default=str) + "\n")
        except OSError as virhe:
            # Loki ei saa koskaan kaataa puhelua.
            logger.warning("Puhelulokiin ei voitu kirjoittaa: %s", virhe)


def hae_puheluloki(asetukset: Annotated[Settings, Depends(hae_asetukset)]) -> Puheluloki | None:
    """FastAPI-dependency. None = loki pois (oletus)."""
    if not (asetukset.dev_tila and asetukset.puhelu_loki):
        return None
    return Puheluloki(Path(asetukset.puhelu_lokikansio))
