from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.data.adapteri import Datalahde, hae_datalahde
from app.mallit import Laake, PotilaanLaakemuutokset, VanhusLyhyt
from app.muutokset.laakkeet import laakkeet_viitepaivana
from app.muutokset.palvelu import (
    hae_kaikki_laakemuutokset,
    hae_potilaan_laakemuutokset,
    ratkaise_viitepaiva,
    valitse_vanhukset,
)

router = APIRouter(tags=["laakemuutokset"])

DatalahdeDep = Annotated[Datalahde, Depends(hae_datalahde)]
ViitepaivaQuery = Annotated[
    datetime | None,
    Query(description="'Nyt'. Oletus: datan uusin paiva, koska demodata on historiallista."),
]
IkkunaQuery = Annotated[int, Query(ge=1, le=3650, description="Kuinka monta paivaa taaksepain")]
MinIkaQuery = Annotated[int, Query(ge=0, le=120)]


@router.get("/potilaat", response_model=list[VanhusLyhyt])
def listaa_potilaat(
    datalahde: DatalahdeDep,
    viitepaiva: ViitepaivaQuery = None,
    min_ika: MinIkaQuery = 65,
    raja: Annotated[int, Query(ge=1, le=1000)] = 50,
) -> list[VanhusLyhyt]:
    paiva = ratkaise_viitepaiva(viitepaiva, datalahde)
    vanhukset = valitse_vanhukset(datalahde.hae_vanhukset(), paiva, min_ika)
    return vanhukset[:raja]


@router.get("/potilaat/{potilas_id}/laakkeet", response_model=list[Laake])
def potilaan_laakkeet(
    potilas_id: str, datalahde: DatalahdeDep, viitepaiva: ViitepaivaQuery = None
) -> list[Laake]:
    if datalahde.hae_vanhus(potilas_id) is None:
        raise HTTPException(status_code=404, detail="Potilasta ei löydy")
    paiva = ratkaise_viitepaiva(viitepaiva, datalahde)
    return laakkeet_viitepaivana(datalahde.hae_laakejaksot(potilas_id), paiva)


@router.get("/potilaat/{potilas_id}/laakemuutokset", response_model=PotilaanLaakemuutokset)
def potilaan_laakemuutokset(
    potilas_id: str,
    datalahde: DatalahdeDep,
    viitepaiva: ViitepaivaQuery = None,
    ikkuna_paivia: IkkunaQuery = 90,
) -> PotilaanLaakemuutokset:
    paiva = ratkaise_viitepaiva(viitepaiva, datalahde)
    tulos = hae_potilaan_laakemuutokset(datalahde, potilas_id, paiva, ikkuna_paivia)
    if tulos is None:
        raise HTTPException(status_code=404, detail="Potilasta ei löydy")
    return tulos


@router.get("/laakemuutokset", response_model=list[PotilaanLaakemuutokset])
def kaikki_laakemuutokset(
    datalahde: DatalahdeDep,
    viitepaiva: ViitepaivaQuery = None,
    ikkuna_paivia: IkkunaQuery = 90,
    min_ika: MinIkaQuery = 65,
) -> list[PotilaanLaakemuutokset]:
    paiva = ratkaise_viitepaiva(viitepaiva, datalahde)
    return hae_kaikki_laakemuutokset(datalahde, paiva, ikkuna_paivia, min_ika)
