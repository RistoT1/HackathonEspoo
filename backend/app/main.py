from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.hoitaja.reitit import reititin


_app_hakemisto = Path(__file__).resolve().parent
sovellus = FastAPI(title="Hyvinvointikumppani")
sovellus.mount(
    "/static",
    StaticFiles(directory=_app_hakemisto / "static"),
    name="static",
)
sovellus.include_router(reititin)
