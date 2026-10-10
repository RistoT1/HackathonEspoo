"""Keskusteluagentti: LLM:n KYTKENTAKOHTA puheluun.

Tilakone (keskustelu.py) paattaa jarjestyksen, hatamerkit, toistot ja lopetuksen. Agentti
vain muotoilee kysymyksen ja reagoi vastaukseen. Nain LLM ei voi ohittaa AI-ilmoitusta,
112-ohjausta eika lopetusta, ja puhelu toimii ilman LLM:aa (KiinteaAgentti).

LLM:n kytkenta:
1. Tee luokka, joka toteuttaa Keskusteluagentti-rajapinnan (esim. LlmAgentti tahan tiedostoon).
   Kutsu mallia OpenAI-yhteensopivasti (httpx, asetukset llm_base_url / llm_malli config.py:ssa).
2. Palauta se hae_agentti()-funktiosta.
3. Vastausaika: Twilio odottaa webhookia ~15 s. Pida mallikutsu selvasti alle sen
   (llm_aikakatkaisu_s) ja palaa virheessa KiinteaAgentin vastaukseen, jotta puhelu ei katkea.

Datan minimointi: agentille ei anneta nimea, puhelinnumeroa, laakkeen nimea eika diagnoosia,
vain Kysymys, Puhelukonteksti ja taman puhelun keskustelu.
"""

from dataclasses import dataclass
from typing import Protocol

from app.mallit import Kysymys, Puhelukonteksti


@dataclass(frozen=True)
class Repliikki:
    puhuja: str  # "agentti" tai "vanhus"
    teksti: str


@dataclass(frozen=True)
class Reaktio:
    kuittaus: str  # lyhyt vastaus ennen seuraavaa kysymysta, esim. "Kiitos, kirjasin sen."
    jatkokysymys: str | None = None  # enintaan yksi per kysymys; tilakone valvoo rajan


class Keskusteluagentti(Protocol):
    async def muotoile_kysymys(
        self, kysymys: Kysymys, konteksti: Puhelukonteksti, historia: list[Repliikki]
    ) -> str:
        """Kysymys selkokielella. Saa kayttaa kontekstia, ei saa lisata diagnooseja."""
        ...

    async def reagoi(
        self,
        kysymys: Kysymys,
        vastaus: str,
        konteksti: Puhelukonteksti,
        historia: list[Repliikki],
        jatkokysymys_sallittu: bool,
    ) -> Reaktio:
        """Kuittaa vastauksen ja paattaa tarvitaanko tarkennus (milloin, kuinka paha).

        Ei laakeneuvoja eika diagnooseja: kysymykseen hoidosta vastataan
        "valitan kysymyksenne hoitajalle".
        """
        ...


class KiinteaAgentti:
    """Toimii ilman LLM:aa: kysymys sellaisenaan, kuittaus aina sama, ei jatkokysymyksia."""

    async def muotoile_kysymys(
        self, kysymys: Kysymys, konteksti: Puhelukonteksti, historia: list[Repliikki]
    ) -> str:
        return kysymys.teksti

    async def reagoi(
        self,
        kysymys: Kysymys,
        vastaus: str,
        konteksti: Puhelukonteksti,
        historia: list[Repliikki],
        jatkokysymys_sallittu: bool,
    ) -> Reaktio:
        return Reaktio(kuittaus="Kiitos.")


def hae_agentti() -> Keskusteluagentti:
    """FastAPI-dependency. Vaihda LLM-agenttiin tassa, kun malli on valmis."""
    return KiinteaAgentti()
