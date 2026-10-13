import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.data.adapteri import DataEiSaatavilla, Datalahde, hae_datalahde
from app.hoitaja import ajat, historia, jono, palvelu, potilas
from app.mallit import (
    EdellinenPuhelu,
    Oirehistoria,
    Puhelutehtava,
    PuhelutehtavanTila,
    TallennettuTulos,
    TilanMuutos,
)
from app.muisti.muisti import hae_edellinen_puhelu
from app.muutokset.palvelu import ratkaise_viitepaiva
from app.puhelutehtavat import varasto
from app.puhelutehtavat.palvelu import (
    MAX_YRITYKSET,
    TehtavaaEiLoydy,
    TilasiirtymaVirhe,
    hae_tehtava,
    vaihda_tila,
)
from app.tietokanta import hae_sessio

logger = logging.getLogger(__name__)
reititin = APIRouter()
_app_hakemisto = Path(__file__).resolve().parent.parent
mallipohjat = Jinja2Templates(directory=_app_hakemisto / "templates")
# Ajat näytetään Suomen aikaan (kanta UTC): {{ aika | aika("%d.%m.%Y") }}.
mallipohjat.env.filters["aika"] = ajat.muotoile
mallipohjat.env.filters["suomen_aika"] = ajat.suomen_aika
SessioDep = Annotated[Session, Depends(hae_sessio)]
DatalahdeDep = Annotated[Datalahde, Depends(hae_datalahde)]

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
    tapaus: Puhelutehtava,
    puhelutulos: TallennettuTulos | None,
    edellinen_puhelu: EdellinenPuhelu | None = None,
    oirehistoria: Oirehistoria | None = None,
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
        "edellinen_puhelu": edellinen_puhelu,
        "oirehistoria": oirehistoria,
        "oirehistoria_json": oirehistoria.model_dump(mode="json") if oirehistoria else None,
    }


@reititin.get("/hoitaja/", response_class=HTMLResponse)
def nayta_jono(
    pyynto: Request,
    sessio: SessioDep,
    tila: PuhelutehtavanTila | None = None,
) -> HTMLResponse:
    kaikki_tapaukset = varasto.listaa(sessio, tila=None, raja=1000)
    arviot = jono.hae_arviot(sessio, [tapaus.id for tapaus in kaikki_tapaukset])
    rajatut = (
        [tapaus for tapaus in kaikki_tapaukset if tapaus.tila == tila]
        if tila is not None
        else kaikki_tapaukset
    )
    tapaukset = jono.jarjesta(rajatut, arviot)
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
            "arviot": arviot,
            "valittu_tila": tila,
            "tilat": list(PuhelutehtavanTila),
            "tilan_nimet": TILAN_NIMET,
            "tilan_css": {status: _tilan_css_luokka(status) for status in PuhelutehtavanTila},
            "yhteenveto": {
                "avoimet": avoimet,
                "odottavat": odottavat,
                "kasitellyt": kasitellyt,
                "hoitaja_soittaa": jono.hoitaja_soittaa(arviot),
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

    palvelu.kirjaa_katselu(sessio, tapaus.id)
    puhelutulos = palvelu.uusimmat_tulokset(sessio, [tapaus.id]).get(tapaus.id)
    # Sama muisti kuin agentilla, mutta tämän tehtävän puhelua edeltävä.
    ennen = puhelutulos.paattyi if puhelutulos is not None else None
    edellinen = hae_edellinen_puhelu(sessio, tapaus.vanhus_id, ennen=ennen)
    # Saman henkilön kaikki puhelut (myös muiden tehtävien), sama lähde kuin muistilla.
    tulokset = palvelu.vanhuksen_tulokset(sessio, tapaus.vanhus_id)
    oirehistoria = historia.oirehistoria(tulokset, tapaus.id)

    return mallipohjat.TemplateResponse(
        request=pyynto,
        name="tapauksen_tiedot.html",
        context=_tehtavan_nakyma(tapaus, puhelutulos, edellinen, oirehistoria),
    )


@reititin.get("/hoitaja/potilaat/", response_class=HTMLResponse)
def nayta_potilaat(pyynto: Request, sessio: SessioDep) -> HTMLResponse:
    """Potilaslista: henkilöt, joilla on soittotehtäviä. Rivistä potilaan sivulle."""
    rivit = potilas.potilasrivit(potilas.kaikki_tehtavat(sessio), palvelu.kaikki_tulokset(sessio))
    return mallipohjat.TemplateResponse(
        request=pyynto, name="potilaat.html", context={"potilaat": rivit}
    )


@reititin.get("/hoitaja/potilaat/{vanhus_id}", response_class=HTMLResponse)
def nayta_potilas(
    pyynto: Request,
    vanhus_id: str,
    sessio: SessioDep,
    datalahde: DatalahdeDep,
) -> HTMLResponse:
    """Potilassivu: perustiedot, lääkitys, soittotehtävät ja oireet puheluissa."""
    tehtavat = potilas.vanhuksen_tehtavat(sessio, vanhus_id)
    vanhus, jaksot, viitepaiva = None, None, None
    try:
        vanhus = datalahde.hae_vanhus(vanhus_id)
        if vanhus is not None:
            jaksot = datalahde.hae_laakejaksot(vanhus_id)
            # Sama "nyt" kuin tehtävällä: demodata on historiallista (ks. viitepäivä).
            viitepaiva = (
                tehtavat[0].viitepaiva if tehtavat else ratkaise_viitepaiva(None, datalahde)
            )
    except (DataEiSaatavilla, SQLAlchemyError):
        # Sivu toimii ilman terveysdataa: tehtävät ja puhelut ovat sovelluksen omassa kannassa.
        logger.warning("Potilassivu ilman terveysdataa", exc_info=True)
    if vanhus is None and not tehtavat:
        raise HTTPException(status_code=404, detail="Potilasta ei löydy")

    palvelu.kirjaa_potilaan_katselu(sessio, vanhus_id)
    tulokset = palvelu.vanhuksen_tulokset(sessio, vanhus_id)
    oirehistoria = historia.oirehistoria(tulokset)
    arviot = jono.hae_arviot(sessio, [tehtava.id for tehtava in tehtavat])

    return mallipohjat.TemplateResponse(
        request=pyynto,
        name="potilas.html",
        context={
            "tiedot": potilas.potilastiedot(vanhus_id, vanhus, jaksot, viitepaiva),
            "tehtavat": tehtavat,
            "arviot": arviot,
            "tilan_nimet": TILAN_NIMET,
            "tilan_css": {tila: _tilan_css_luokka(tila) for tila in PuhelutehtavanTila},
            "oirehistoria": oirehistoria,
            "oirehistoria_json": oirehistoria.model_dump(mode="json") if oirehistoria else None,
        },
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
