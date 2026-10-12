from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import hae_asetukset
from app.data.adapteri import DataEiSaatavilla
from app.dev.reitit import router as dev_router
from app.hoitaja.reitit import reititin as hoitaja_reititin
from app.mallit import PuhelunTulos
from app.muutokset.reitit import router as muutokset_router
from app.puhelu.istunnot import hae_istuntovarasto
from app.puhelu.puhe import TwimlLiianPitka
from app.puhelu.reitit import router as puhelu_router
from app.puhelu.relay import router as relay_router
from app.puhelu.soittaja import SoittoEiKaytossa, SoittoEpaonnistui
from app.puhelutehtavat.ajastus import kaynnista_ajastin
from app.puhelutehtavat.reitit import router as puhelutehtavat_router
from app.tietokanta import aja_migraatiot, hae_engine, hae_sessiotehdas, tallenna_puhelun_tulos
from app.triage.analyysi import LlmAnalysoija
from app.triage.tyontekija import Triagetyontekija


def _tallenna_tulos_kantaan(tulos: PuhelunTulos) -> None:
    with hae_sessiotehdas()() as sessio:
        tallenna_puhelun_tulos(sessio, tulos)


@asynccontextmanager
async def elinkaari(_: FastAPI) -> AsyncGenerator[None, None]:
    aja_migraatiot()
    asetukset = hae_asetukset()
    if asetukset.llm_malli:
        # Puhelun jälkeinen analyysi hoitajalle samalla mallilla (paikallinen/EU).
        analysoija = LlmAnalysoija(
            asetukset.llm_base_url,
            asetukset.llm_malli,
            asetukset.llm_api_avain,
            ajattelu=asetukset.llm_analyysi_ajattelu,
            pida_muistissa=asetukset.llm_pida_muistissa,
        )
        varasto = hae_istuntovarasto()
        varasto.aseta_triage(
            Triagetyontekija(analysoija, varasto.puheluita_kaynnissa, _tallenna_tulos_kantaan)
        )
    vali = asetukset.tunnistus_vali_minuutit
    ajastin = kaynnista_ajastin(vali) if vali > 0 else None
    yield
    if ajastin is not None:
        ajastin.shutdown(wait=False)


app = FastAPI(title="Hyvinvointikumppani", lifespan=elinkaari)


@app.exception_handler(DataEiSaatavilla)
async def data_ei_saatavilla(_: Request, virhe: DataEiSaatavilla) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(virhe)})


@app.exception_handler(SoittoEiKaytossa)
async def soitto_ei_kaytossa(_: Request, virhe: SoittoEiKaytossa) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(virhe)})


@app.exception_handler(SoittoEpaonnistui)
async def soitto_epaonnistui(_: Request, virhe: SoittoEpaonnistui) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(virhe)})


@app.exception_handler(TwimlLiianPitka)
async def twiml_liian_pitka(_: Request, virhe: TwimlLiianPitka) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(virhe)})


app.include_router(muutokset_router)
app.include_router(puhelutehtavat_router)
app.include_router(puhelu_router)
app.include_router(relay_router)
app.include_router(dev_router)
app.include_router(hoitaja_reititin)

_app_hakemisto = Path(__file__).resolve().parent
app.mount(
    "/static",
    StaticFiles(directory=_app_hakemisto / "static"),
    name="static",
)


@app.get("/terveys")
def terveys() -> dict[str, str]:
    """Varmistaa kantayhteyden."""
    with hae_engine().connect() as yhteys:
        yhteys.execute(text("SELECT 1"))
    return {"tila": "ok"}
