from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mallit import Puhelutehtava, PuhelutehtavanTila, TilanMuutos
from app.puhelutehtavat import varasto
from app.puhelutehtavat.palvelu import (
    MAX_YRITYKSET,
    TehtavaaEiLoydy,
    TilasiirtymaVirhe,
    hae_tehtava,
    vaihda_tila,
)
from app.tietokanta import KatseluLokiRivi, PuhelunTulosRivi, hae_sessio

reititin = APIRouter()
_app_hakemisto = Path(__file__).resolve().parent.parent
mallipohjat = Jinja2Templates(directory=_app_hakemisto / "templates")
SessioDep = Annotated[Session, Depends(hae_sessio)]

TILAN_NIMET = {
    PuhelutehtavanTila.ODOTTAA: "Odottaa soittoa",
    PuhelutehtavanTila.SOITETAAN: "Puhelu käynnissä",
    PuhelutehtavanTila.VALMIS: "Puhelu valmis",
    PuhelutehtavanTila.EI_VASTANNUT: "Ei vastannut",
    PuhelutehtavanTila.PERUTTU: "Peruttu",
}

SALLITUT_TOIMINNOT: dict[PuhelutehtavanTila, list[PuhelutehtavanTila]] = {
    PuhelutehtavanTila.ODOTTAA: [PuhelutehtavanTila.PERUTTU],
    PuhelutehtavanTila.SOITETAAN: [
        PuhelutehtavanTila.VALMIS,
        PuhelutehtavanTila.EI_VASTANNUT,
    ],
    PuhelutehtavanTila.EI_VASTANNUT: [
        PuhelutehtavanTila.ODOTTAA,
        PuhelutehtavanTila.PERUTTU,
    ],
    PuhelutehtavanTila.VALMIS: [],
    PuhelutehtavanTila.PERUTTU: [],
}


def _tilan_css_luokka(tila: PuhelutehtavanTila) -> str:
    return {
        PuhelutehtavanTila.ODOTTAA: "odottaa",
        PuhelutehtavanTila.SOITETAAN: "kaynnissa",
        PuhelutehtavanTila.VALMIS: "valmis",
        PuhelutehtavanTila.EI_VASTANNUT: "ei-vastannut",
        PuhelutehtavanTila.PERUTTU: "peruttu",
    }[tila]


def _tehtavan_nakyma(
    tapaus: Puhelutehtava, puhelutulos: PuhelunTulosRivi | None
) -> dict[str, object]:
    sallittuja = list(SALLITUT_TOIMINNOT[tapaus.tila])
    if tapaus.tila == PuhelutehtavanTila.EI_VASTANNUT and tapaus.yritykset >= MAX_YRITYKSET:
        sallittuja.remove(PuhelutehtavanTila.ODOTTAA)

    return {
        "tapaus": tapaus,
        "tilan_nimi": TILAN_NIMET[tapaus.tila],
        "tilan_css": _tilan_css_luokka(tapaus.tila),
        "sallitut_toiminnot": sallittuja,
        "toimintojen_nimet": TILAN_NIMET,
        "puhelutulos": puhelutulos,
    }


@reititin.get("/hoitaja/", response_class=HTMLResponse)
def nayta_jono(
    pyynto: Request,
    sessio: SessioDep,
    tila: PuhelutehtavanTila | None = None,
) -> HTMLResponse:
    kaikki_tapaukset = varasto.listaa(sessio, tila=None, raja=1000)
    tapaukset = (
        [tapaus for tapaus in kaikki_tapaukset if tapaus.tila == tila]
        if tila is not None
        else kaikki_tapaukset
    )
    avoimet = sum(
        tapaus.tila in (PuhelutehtavanTila.ODOTTAA, PuhelutehtavanTila.SOITETAAN)
        for tapaus in kaikki_tapaukset
    )
    odottavat = sum(tapaus.tila == PuhelutehtavanTila.ODOTTAA for tapaus in kaikki_tapaukset)
    kasitellyt = sum(
        tapaus.tila in (PuhelutehtavanTila.VALMIS, PuhelutehtavanTila.PERUTTU)
        for tapaus in kaikki_tapaukset
    )

    return mallipohjat.TemplateResponse(
        request=pyynto,
        name="takaisinsoittojono.html",
        context={
            "tapaukset": tapaukset,
            "valittu_tila": tila,
            "tilat": list(PuhelutehtavanTila),
            "tilan_nimet": TILAN_NIMET,
            "tilan_css": {status: _tilan_css_luokka(status) for status in PuhelutehtavanTila},
            "yhteenveto": {
                "avoimet": avoimet,
                "odottavat": odottavat,
                "kasitellyt": kasitellyt,
            },
        },
    )


@reititin.get("/hoitaja/tapaukset/{tunniste}", response_class=HTMLResponse)
def nayta_tapaus(
    pyynto: Request,
    tunniste: str,
    sessio: SessioDep,
) -> HTMLResponse:
    try:
        tapaus = hae_tehtava(sessio, tunniste)
    except TehtavaaEiLoydy as virhe:
        raise HTTPException(status_code=404, detail="Soittotehtävää ei löydy") from virhe

    sessio.add(
        KatseluLokiRivi(
            tehtava_id=tapaus.id,
            katsoja="hackathon-demo (kirjautumaton)",
            toiminto="katselu",
        )
    )
    sessio.commit()
    puhelutulos = sessio.scalar(
        select(PuhelunTulosRivi)
        .where(PuhelunTulosRivi.tehtava_id == tapaus.id)
        .order_by(PuhelunTulosRivi.paattyi.desc())
        .limit(1)
    )

    return mallipohjat.TemplateResponse(
        request=pyynto,
        name="tapauksen_tiedot.html",
        context=_tehtavan_nakyma(tapaus, puhelutulos),
    )


@reititin.patch("/hoitaja/tapaukset/{tunniste}/tila", response_model=Puhelutehtava)
def paivita_tapauksen_tila(
    tunniste: str,
    muutos: TilanMuutos,
    sessio: SessioDep,
) -> Puhelutehtava:
    try:
        return vaihda_tila(sessio, tunniste, muutos.tila)
    except TehtavaaEiLoydy as virhe:
        raise HTTPException(status_code=404, detail="Soittotehtävää ei löydy") from virhe
    except TilasiirtymaVirhe as virhe:
        raise HTTPException(status_code=409, detail=str(virhe)) from virhe
