from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.config import Settings, hae_asetukset
from app.data.adapteri import Datalahde, hae_datalahde
from app.dev.henkilotiedot import tayta_puuttuvat
from app.mallit import (
    Puhelutehtava,
    PuhelutehtavanTila,
    Soittotiedot,
    TilanMuutos,
    TunnistuksenTulos,
    TunnistusPyynto,
)
from app.muutokset.palvelu import ratkaise_viitepaiva
from app.puhelutehtavat import varasto
from app.puhelutehtavat.palvelu import (
    PuhelinnumeroPuuttuu,
    SoittotiedotEivatSaatavilla,
    TehtavaaEiLoydy,
    TilasiirtymaVirhe,
    TunnistusKaynnissa,
    hae_soittotiedot,
    hae_tehtava,
    ota_seuraava,
    tunnista_ja_jonouta,
    vaihda_tila,
)
from app.tietokanta import hae_sessio

router = APIRouter(prefix="/puhelutehtavat", tags=["puhelutehtavat"])

DatalahdeDep = Annotated[Datalahde, Depends(hae_datalahde)]
SessioDep = Annotated[Session, Depends(hae_sessio)]


@router.post("/tunnista", response_model=TunnistuksenTulos)
def tunnista(
    pyynto: TunnistusPyynto,
    datalahde: DatalahdeDep,
    sessio: SessioDep,
    asetukset: Annotated[Settings, Depends(hae_asetukset)],
) -> TunnistuksenTulos:
    """Ajaa lääkemuutosten tunnistuksen ja vie uudet muutokset soittojonoon."""
    viitepaiva = ratkaise_viitepaiva(pyynto.viitepaiva, datalahde)
    try:
        tulos = tunnista_ja_jonouta(
            datalahde, sessio, viitepaiva, pyynto.ikkuna_paivia, pyynto.min_ika
        )
    except TunnistusKaynnissa as virhe:
        raise HTTPException(status_code=409, detail=str(virhe)) from virhe
    if asetukset.dev_tila:
        # VAIN DEV: uusille jonossa oleville synteettinen nimi (dev/henkilotiedot.py).
        tayta_puuttuvat(sessio)
    return tulos


@router.get("", response_model=list[Puhelutehtava])
def listaa(
    sessio: SessioDep,
    tila: PuhelutehtavanTila | None = None,
    raja: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[Puhelutehtava]:
    return varasto.listaa(sessio, tila, raja)


@router.post(
    "/seuraava",
    response_model=Puhelutehtava,
    responses={204: {"description": "Jono on tyhjä"}},
)
def seuraava(sessio: SessioDep) -> Puhelutehtava | Response:
    """Puheluosa kutsuu: varaa vanhimman odottavan tehtävän (ODOTTAA -> SOITETAAN)."""
    tehtava = ota_seuraava(sessio)
    if tehtava is None:
        return Response(status_code=204)
    return tehtava


@router.get("/{tehtava_id}", response_model=Puhelutehtava)
def hae(tehtava_id: str, sessio: SessioDep) -> Puhelutehtava:
    try:
        return hae_tehtava(sessio, tehtava_id)
    except TehtavaaEiLoydy as virhe:
        raise HTTPException(status_code=404, detail="Tehtävää ei löydy") from virhe


@router.patch("/{tehtava_id}", response_model=Puhelutehtava)
def paivita_tila(tehtava_id: str, muutos: TilanMuutos, sessio: SessioDep) -> Puhelutehtava:
    try:
        return vaihda_tila(sessio, tehtava_id, muutos.tila)
    except TehtavaaEiLoydy as virhe:
        raise HTTPException(status_code=404, detail="Tehtävää ei löydy") from virhe
    except TilasiirtymaVirhe as virhe:
        raise HTTPException(status_code=409, detail=str(virhe)) from virhe


@router.get("/{tehtava_id}/soittotiedot", response_model=Soittotiedot)
def soittotiedot(
    tehtava_id: str,
    sessio: SessioDep,
    asetukset: Annotated[Settings, Depends(hae_asetukset)],
) -> Soittotiedot:
    """Puheluosalle: numero + kysymykset + konteksti. Vain kun tehtava on tilassa SOITETAAN."""
    try:
        return hae_soittotiedot(sessio, tehtava_id, asetukset.demo_puhelinnumero)
    except TehtavaaEiLoydy as virhe:
        raise HTTPException(status_code=404, detail="Tehtävää ei löydy") from virhe
    except (SoittotiedotEivatSaatavilla, PuhelinnumeroPuuttuu) as virhe:
        raise HTTPException(status_code=409, detail=str(virhe)) from virhe
