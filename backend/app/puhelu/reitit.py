from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import Settings, hae_asetukset
from app.mallit import PuhelunAloitus
from app.puhelu.agentti import Keskusteluagentti, hae_agentti
from app.puhelu.istunnot import Istuntovarasto, hae_istuntovarasto
from app.puhelu.palvelu import (
    aloita_puhelu,
    kasittele_tila,
    kasittele_vuoro,
    kuuntelu_asetuksista,
)
from app.puhelu.puhe import virhe_twiml, vuoro_twiml
from app.puhelu.soittaja import Soittaja, hae_soittaja
from app.puhelu.twilio_tarkistus import julkinen_url, twilion_lomake
from app.puhelutehtavat.palvelu import (
    PuhelinnumeroPuuttuu,
    SoittotiedotEivatSaatavilla,
    TehtavaaEiLoydy,
)
from app.tietokanta import hae_sessio

router = APIRouter(prefix="/puhelu", tags=["puhelu"])

SessioDep = Annotated[Session, Depends(hae_sessio)]
AsetuksetDep = Annotated[Settings, Depends(hae_asetukset)]
IstunnotDep = Annotated[Istuntovarasto, Depends(hae_istuntovarasto)]
TwilioLomakeDep = Annotated[dict[str, str], Depends(twilion_lomake)]


def twiml_vastaus(twiml: str) -> Response:
    return Response(content=twiml, media_type="application/xml")


@router.post("/aloita/{tehtava_id}", response_model=PuhelunAloitus)
def aloita(
    tehtava_id: str,
    sessio: SessioDep,
    soittaja: Annotated[Soittaja, Depends(hae_soittaja)],
    istunnot: IstunnotDep,
    asetukset: AsetuksetDep,
) -> PuhelunAloitus:
    """Soittaa tehtavan vanhukselle ja kay keskustelun (vaatii JULKINEN_URL:n, devissa ngrok).

    Tehtava varataan ensin: POST /puhelutehtavat/seuraava (tila SOITETAAN). Puhelun paattyessa
    tehtava siirtyy tilaan VALMIS tai EI_VASTANNUT ja tulos jaa triagelle.
    """
    perus_url = julkinen_url(asetukset)
    try:
        return aloita_puhelu(
            sessio,
            soittaja,
            istunnot,
            tehtava_id,
            asetukset.demo_puhelinnumero,
            perus_url,
            asetukset.matala_varmuus,
        )
    except TehtavaaEiLoydy as virhe:
        raise HTTPException(status_code=404, detail="Tehtävää ei löydy") from virhe
    except (SoittotiedotEivatSaatavilla, PuhelinnumeroPuuttuu) as virhe:
        raise HTTPException(status_code=409, detail=str(virhe)) from virhe


@router.post("/vuoro/{istunto_id}", include_in_schema=False)
async def vuoro(
    istunto_id: str,
    kentat: TwilioLomakeDep,
    istunnot: IstunnotDep,
    agentti: Annotated[Keskusteluagentti, Depends(hae_agentti)],
    sessio: SessioDep,
    asetukset: AsetuksetDep,
) -> Response:
    """Twilion webhook: vastattiin tai vanhus vastasi. Palauttaa seuraavan vuoron TwiML:na."""
    istunto = istunnot.hae(istunto_id)
    konteksti = istunto.konteksti if istunto else None
    seuraava = await kasittele_vuoro(istunto_id, kentat, istunnot, agentti, sessio)
    if seuraava is None:
        return twiml_vastaus(virhe_twiml(asetukset.puhe_aani))

    action_url = f"{julkinen_url(asetukset)}/puhelu/vuoro/{istunto_id}"
    kuuntelu = kuuntelu_asetuksista(asetukset, konteksti)
    twiml = vuoro_twiml(seuraava.sanottavat, seuraava.kuuntele, action_url, kuuntelu)
    return twiml_vastaus(twiml)


@router.post("/tila/{istunto_id}", status_code=204, include_in_schema=False)
def tila(
    istunto_id: str, kentat: TwilioLomakeDep, istunnot: IstunnotDep, sessio: SessioDep
) -> Response:
    """Twilion status callback: puhelu paattyi (myos ei vastattu / varattu / katkesi)."""
    kasittele_tila(istunto_id, kentat.get("CallStatus", ""), istunnot, sessio)
    return Response(status_code=204)
