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
from app.muutokset.reitit import router as muutokset_router
from app.puhelutehtavat.ajastus import kaynnista_ajastin
from app.puhelutehtavat.reitit import router as puhelutehtavat_router
from app.tietokanta import aja_migraatiot, hae_engine


@asynccontextmanager
async def elinkaari(_: FastAPI) -> AsyncGenerator[None, None]:
    aja_migraatiot()
    vali = hae_asetukset().tunnistus_vali_minuutit
    ajastin = kaynnista_ajastin(vali) if vali > 0 else None
    yield
    if ajastin is not None:
        ajastin.shutdown(wait=False)


app = FastAPI(title="Hyvinvointikumppani", lifespan=elinkaari)


@app.exception_handler(DataEiSaatavilla)
async def data_ei_saatavilla(_: Request, virhe: DataEiSaatavilla) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(virhe)})


app.include_router(muutokset_router)
app.include_router(puhelutehtavat_router)
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