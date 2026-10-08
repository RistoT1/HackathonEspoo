"""VAIN DEV: reitit testimuutoksille. Palauttavat 404, ellei DEV_TILA=true."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import Settings, hae_asetukset
from app.data.adapteri import Datalahde, Testikirjoittaja, hae_datalahde, hae_testikirjoittaja
from app.dev.palvelu import TestimuutosVirhe, tee_testimuutos
from app.mallit import (
    PotilaanLaakemuutokset,
    PuhelutehtavienNollaus,
    Testimuutos,
    TestimuutostenNollaus,
    Yhteystieto,
)
from app.puhelutehtavat import varasto
from app.puhelutehtavat.palvelu import aseta_yhteystieto
from app.tietokanta import hae_sessio


def vaadi_dev_tila(asetukset: Annotated[Settings, Depends(hae_asetukset)]) -> None:
    if not asetukset.dev_tila:
        raise HTTPException(status_code=404)


router = APIRouter(prefix="/dev", tags=["dev"], dependencies=[Depends(vaadi_dev_tila)])

DatalahdeDep = Annotated[Datalahde, Depends(hae_datalahde)]
KirjoittajaDep = Annotated[Testikirjoittaja, Depends(hae_testikirjoittaja)]


@router.post("/testimuutos", response_model=PotilaanLaakemuutokset)
def tee_muutos(
    pyynto: Testimuutos,
    datalahde: DatalahdeDep,
    kirjoittaja: KirjoittajaDep,
    ikkuna_paivia: int = 90,
) -> PotilaanLaakemuutokset:
    """Tekee muutoksen demodataan ja palauttaa vanhuksen tunnistetut muutokset sen jalkeen."""
    viitepaiva = datalahde.uusin_paiva()
    try:
        tulos = tee_testimuutos(datalahde, kirjoittaja, pyynto, viitepaiva, ikkuna_paivia)
    except TestimuutosVirhe as virhe:
        raise HTTPException(status_code=400, detail=str(virhe)) from virhe
    if tulos is None:
        raise HTTPException(status_code=404, detail="Vanhusta ei loydy")
    return tulos


@router.delete("/testimuutokset", response_model=TestimuutostenNollaus)
def nollaa_muutokset(kirjoittaja: KirjoittajaDep) -> TestimuutostenNollaus:
    """Palauttaa demodatan ennalleen: lopetukset perutaan ja lisatyt laakkeet poistetaan."""
    palautettuja, poistettuja = kirjoittaja.poista_testimuutokset()
    return TestimuutostenNollaus(
        palautettuja_lopetuksia=palautettuja, poistettuja_laakkeita=poistettuja
    )


@router.delete("/puhelutehtavat", response_model=PuhelutehtavienNollaus)
def nollaa_puhelutehtavat(
    sessio: Annotated[Session, Depends(hae_sessio)],
) -> PuhelutehtavienNollaus:
    """Tyhjentaa soittojonon, jotta demon voi ajaa alusta. Tuotannossa tehtavia ei poisteta."""
    return PuhelutehtavienNollaus(poistettuja_puhelutehtavia=varasto.poista_kaikki(sessio))


@router.put("/yhteystiedot/{vanhus_id}", response_model=Yhteystieto)
def aseta_puhelinnumero(
    vanhus_id: str,
    yhteystieto: Yhteystieto,
    sessio: Annotated[Session, Depends(hae_sessio)],
) -> Yhteystieto:
    """Asettaa vanhukselle demonumeron (oma, Twilioon vahvistettu numero). Ei oikeita numeroita."""
    aseta_yhteystieto(sessio, vanhus_id, yhteystieto.puhelinnumero)
    return yhteystieto
