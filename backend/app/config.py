from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # .env on repon juuressa (sama kuin docker composella); backend/.env ohittaa sen tarvittaessa.
    # Absoluuttiset polut: toimii mista tahansa kaynnistettyna (repon juuri, IDE, backend/).
    model_config = SettingsConfigDict(
        env_file=(_BACKEND.parent / ".env", _BACKEND / ".env"), extra="ignore"
    )

    database_url: str
    # Sallii /dev/-reitit, jotka kirjoittavat demodataan. Vain paikallisesti, ei tuotantoon.
    dev_tila: bool = False
    # Soitettavien skannauksen (laakemuutosten tunnistus -> soittojono) vali minuutteina.
    # Ensimmainen ajo heti kaynnistyksessa. 0 = pois (ajetaan kasin: POST /puhelutehtavat/tunnista).
    tunnistus_vali_minuutit: int = 15
    # Twilioon vahvistettu demonumero (E.164), jos vanhukselle ei ole omaa numeroa.
    # Synthea-datassa ei ole puhelinnumeroita. Ei koskaan repoon.
    demo_puhelinnumero: str | None = None

    # Puhelun LLM: OpenAI-yhteensopiva rajapinta. Demossa paikallinen Ollama, tuotannossa
    # EU-hostattu malli pelkalla asetusmuutoksella. Ei kuluttaja-API:a (ks. SAANTELY.md).
    llm_base_url: str = "http://localhost:11434/v1"
    llm_malli: str = ""  # Ollaman mallin nimi, esim. tulos komennosta `ollama list`
    llm_api_avain: str = "ollama"  # Ollama ei tarkista; EU-palvelussa oikea avain .env:iin
    llm_aikakatkaisu_s: float = 30.0


@lru_cache
def hae_asetukset() -> Settings:
    return Settings()
