from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field


reititin = APIRouter()
_app_hakemisto = Path(__file__).resolve().parent.parent
mallipohjat = Jinja2Templates(directory=_app_hakemisto / "templates")


@dataclass
class Tapaus:
    tunniste: str
    kiireellisyys: str
    otsikko: str
    yhteenveto: str
    laukaisin: str
    peruste: str
    saapunut: str
    tila: str = "Avoin"
    muistiinpano: str = ""


class KasittelyPyynto(BaseModel):
    tila: Literal["Käsitelty", "Takaisinsoitto tehty"]
    muistiinpano: str = Field(default="", max_length=2000)


# Vain käyttöliittymän paikalliseen esikatseluun. Korvataan tietokantapalvelulla.
_tapaukset: dict[str, Tapaus] = {
    "DEMO-014": Tapaus(
        tunniste="DEMO-014",
        kiireellisyys="Punainen",
        otsikko="Kiireellinen oire mainittu",
        yhteenveto=(
            "Synteettisessä demokeskustelussa mainittiin rintakipu. "
            "112-ohjaus annettu; tarkista tapahtuma sovitun toimintamallin mukaan."
        ),
        laukaisin="Sääntöpohjainen punaisen lipun tunnistus",
        peruste="Rintakipu tunnistettiin puheluvastauksesta.",
        saapunut="12:41",
    ),
    "DEMO-022": Tapaus(
        tunniste="DEMO-022",
        kiireellisyys="Keltainen",
        otsikko="Vointi muuttunut uuden lääkkeen jälkeen",
        yhteenveto=(
            "Synteettinen vastaaja kertoi aloittaneensa lääkkeen ja voinnin muuttuneen. "
            "Lisäselvitys hoitajan kanssa tarpeen."
        ),
        laukaisin="Uusi lääkemerkintä",
        peruste="Asiakas kertoi voinnin muutoksesta; hoitaja arvioi jatkon.",
        saapunut="12:35",
    ),
    "DEMO-031": Tapaus(
        tunniste="DEMO-031",
        kiireellisyys="Keltainen",
        otsikko="Lääkkeen aloittaminen mietityttää",
        yhteenveto=(
            "Synteettinen vastaaja ei ollut aloittanut lääkettä ja halusi keskustella "
            "asiasta hoitajan kanssa."
        ),
        laukaisin="Uusi lääkemerkintä",
        peruste="Asiakas pyysi hoitajan yhteydenottoa.",
        saapunut="12:12",
    ),
}


@reititin.get("/hoitaja/", response_class=HTMLResponse)
async def nayta_jono(pyynto: Request) -> HTMLResponse:
    tapaukset = sorted(
        _tapaukset.values(),
        key=lambda tapaus: (
            {"Punainen": 0, "Keltainen": 1, "Vihreä": 2}.get(
                tapaus.kiireellisyys, 3
            ),
            tapaus.saapunut,
        ),
    )
    return mallipohjat.TemplateResponse(
        request=pyynto,
        name="takaisinsoittojono.html",
        context={"tapaukset": tapaukset},
    )


@reititin.get("/hoitaja/tapaukset/{tunniste}", response_class=HTMLResponse)
async def nayta_tapaus(pyynto: Request, tunniste: str) -> HTMLResponse:
    tapaus = _tapaukset.get(tunniste)
    if tapaus is None:
        raise HTTPException(status_code=404, detail="Tapausta ei löytynyt")

    return mallipohjat.TemplateResponse(
        request=pyynto,
        name="tapauksen_tiedot.html",
        context={"tapaus": tapaus},
    )


@reititin.post("/hoitaja/tapaukset/{tunniste}/kasittele")
async def kasittele_tapaus(tunniste: str, pyynto: KasittelyPyynto) -> dict[str, str]:
    tapaus = _tapaukset.get(tunniste)
    if tapaus is None:
        raise HTTPException(status_code=404, detail="Tapausta ei löytynyt")

    tapaus.tila = pyynto.tila
    tapaus.muistiinpano = pyynto.muistiinpano.strip()
    return {"tila": tapaus.tila, "viesti": "Tapauksen tiedot päivitettiin."}
