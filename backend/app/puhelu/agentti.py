"""Keskusteluagentti: LLM:n kytkentäkohta puheluun (Jeren malli, puheavustaja.py).

Malli käy keskustelun vapaasti: se saa järjestelmäohjeen (persoonallisuus, ikäluokan
puhetapa, puhelun tehtävä ja kysymykset, tietopohja.jarjestelmaohje) ja keskustelun
viesteinä, ja vastaa tekstinä. Säännöt ajetaan ennen mallia (keskustelu.py): hätä,
vakavat huolet, lopetuspyyntö ja loukkaukset eivät kulje mallin kautta, ja malli saa
vuorokohtaiset piilo-ohjeet (huoli, oireen jatkokysymykset ja kotihoito-ohjeet).

Datan minimointi: mallille ei anneta nimeä, puhelinnumeroa, lääkkeen nimeä eikä
diagnoosia, vain ikäluokka, lääkemuutoksen tyyppi ja ryhmä sekä tämän puhelun keskustelu.
"""

import logging
from collections.abc import Callable
from typing import Annotated, Protocol

import httpx
from fastapi import Depends

from app.config import Settings, hae_asetukset

logger = logging.getLogger(__name__)

Viestit = list[dict[str, str]]


class Keskusteluagentti(Protocol):
    async def lammita(self, viestit: Viestit) -> None:
        """Käsittelee keskustelun alun (järjestelmäohje + avaus) välimuistiin soiton ajaksi."""
        ...

    async def vastaa(self, viestit: Viestit) -> str | None:
        """Mallin vastaus tekstinä. None = ei mallia; poikkeus = malli epäonnistui.
        Kummassakin tapauksessa tilakone käyttää kiinteää repliikkiä."""
        ...


class KiinteaAgentti:
    """Ilman LLM:ää: tilakone toimii kiinteillä repliikeillä ja säännöillä."""

    async def lammita(self, viestit: Viestit) -> None:
        return None

    async def vastaa(self, viestit: Viestit) -> str | None:
        return None


class LlmAgentti:
    """OpenAI-yhteensopiva chat-rajapinta (demossa Ollama, tuotannossa EU-hostattu malli)."""

    def __init__(
        self,
        base_url: str,
        malli: str,
        api_avain: str,
        aikaraja_s: float,
        ajattelu_pois: bool,
        transport: httpx.AsyncBaseTransport | None = None,
        lampotila: float = 0.6,
        pida_muistissa: bool = False,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._malli = malli
        self._otsakkeet = {"Authorization": f"Bearer {api_avain}"}
        self._aikaraja_s = aikaraja_s
        self._ajattelu_pois = ajattelu_pois
        self._transport = transport
        self._lampotila = lampotila
        self._pida_muistissa = pida_muistissa

    def _runko(self, viestit: Viestit, max_tokens: int) -> dict[str, object]:
        runko: dict[str, object] = {
            "model": self._malli,
            "messages": viestit,
            "temperature": self._lampotila,  # Jere 0,6 (LLM_LAMPOTILA)
            "max_tokens": max_tokens,
        }
        if self._ajattelu_pois:
            runko["reasoning_effort"] = "none"
        if self._pida_muistissa:
            runko["keep_alive"] = -1  # Ollama: malli ja välimuisti pysyvät ladattuina
        return runko

    async def lammita(self, viestit: Viestit) -> None:
        """Keskustelun TÄSMÄLLEEN SAMA alku kuin ensimmäisessä vuorossa (järjestelmäohje +
        avaus): Ollama käyttää välimuistia vain, kun edellinen pyyntö on uuden alkuosa.
        Mitattu 11.10. Gemma 12B: alku "ohje + Hei" -> ensimmäinen vuoro 21,3 s, alku
        "ohje + avaus" -> 3,1 s."""
        try:
            async with httpx.AsyncClient(timeout=120, transport=self._transport) as client:
                await client.post(self._url, json=self._runko(viestit, 1), headers=self._otsakkeet)
        except httpx.HTTPError as virhe:
            logger.warning("LLM:n lammitys epaonnistui: %s", virhe)

    async def vastaa(self, viestit: Viestit) -> str | None:
        async with httpx.AsyncClient(timeout=self._aikaraja_s, transport=self._transport) as client:
            vastaus = await client.post(
                self._url, json=self._runko(viestit, 200), headers=self._otsakkeet
            )
        vastaus.raise_for_status()
        return vastaus.json()["choices"][0]["message"]["content"] or ""


async def lammita_ja_merkitse(
    agentti: Keskusteluagentti, viestit: Viestit, valmis: Callable[[], None]
) -> None:
    """Taustatyö soiton ajaksi: lämmittää ja merkitsee mallin valmiiksi (myös virheessä,
    ettei puhelu jää odottamaan lämmitystä, joka ei valmistu)."""
    try:
        await agentti.lammita(viestit)
    finally:
        valmis()


def hae_agentti(asetukset: Annotated[Settings, Depends(hae_asetukset)]) -> Keskusteluagentti:
    """FastAPI-dependency. LLM_MALLI asetettu -> LLM-agentti, muuten kiinteä."""
    if not asetukset.llm_malli:
        return KiinteaAgentti()
    return LlmAgentti(
        base_url=asetukset.llm_base_url,
        malli=asetukset.llm_malli,
        api_avain=asetukset.llm_api_avain,
        aikaraja_s=asetukset.llm_vuoron_aikaraja_s,
        ajattelu_pois=asetukset.llm_ajattelu_pois,
        lampotila=asetukset.llm_lampotila,
        pida_muistissa=asetukset.llm_pida_muistissa,
    )
