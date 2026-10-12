import asyncio
import time
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import Settings, hae_asetukset
from app.mallit import PuhelunAloitus
from app.puhelu.agentti import Keskusteluagentti, hae_agentti, lammita_ja_merkitse
from app.puhelu.istunnot import Istuntovarasto, Keskenvuoro, hae_istuntovarasto
from app.puhelu.keskustelu import Puheluistunto, Vuoro, ilman_lyhytta_kuittausta
from app.puhelu.kielentarkistus import Tarkistin, hae_tarkistin
from app.puhelu.loki import Puheluloki, hae_puheluloki
from app.puhelu.palvelu import (
    aloita_puhelu,
    aloita_vuoro,
    kasittele_tila,
    kuuntelu_asetuksista,
    rajat_asetuksista,
    relay_asetuksista,
    viimeistele_vuoro,
)
from app.puhelu.puhe import (
    VALIVASTAUKSET,
    VALIVASTAUS_LISA,
    odotus_twiml,
    puheen_kesto_arvio_ms,
    virhe_twiml,
    vuoro_twiml,
)
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
AgenttiDep = Annotated[Keskusteluagentti, Depends(hae_agentti)]
LokiDep = Annotated[Puheluloki | None, Depends(hae_puheluloki)]


def twiml_vastaus(twiml: str) -> Response:
    return Response(content=twiml, media_type="application/xml")


@router.post("/aloita/{tehtava_id}", response_model=PuhelunAloitus)
def aloita(
    tehtava_id: str,
    sessio: SessioDep,
    soittaja: Annotated[Soittaja, Depends(hae_soittaja)],
    istunnot: IstunnotDep,
    asetukset: AsetuksetDep,
    agentti: AgenttiDep,
    taustatyot: BackgroundTasks,
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
            rajat_asetuksista(asetukset),
            relay=relay_asetuksista(asetukset),
            # Järjestelmäohje mallin välimuistiin sillä aikaa kun puhelin soi.
            lammitys=lambda viestit, valmis: taustatyot.add_task(
                lammita_ja_merkitse, agentti, viestit, valmis
            ),
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
    agentti: AgenttiDep,
    sessio: SessioDep,
    asetukset: AsetuksetDep,
    loki: LokiDep,
    tarkistin: Annotated[Tarkistin, Depends(hae_tarkistin)],
) -> Response:
    """Twilion webhook: vastattiin tai vanhus vastasi. Palauttaa seuraavan vuoron TwiML:na.

    Ellei vuoro valmistu puhelu_valivastaus_s:ssa (hidas LLM), sanotaan "Hetkinen..." ja
    Twilio hakee tuloksen reitiltä /odota (<Redirect>). Näin malli ei aikakatkea Twilion
    15 s rajaan ja puhelu kuulostaa luontevalta (11.10.: 7/11 vuoroa aikakatkesi).
    """
    istunto = istunnot.hae(istunto_id)
    if istunto is None:
        _kirjaa(loki, istunto_id, None, None, None, kentat)
        return twiml_vastaus(virhe_twiml(asetukset.puhe_aani))

    saapui = time.perf_counter()
    kesken = Keskenvuoro(
        tehtava=aloita_vuoro(istunto, kentat, agentti, tarkistin),
        alku=saapui,
        kentat=kentat,
        vaihe_ennen=istunto.vaihe.value,
        ajoitus=_ajoitus(istunto, saapui),
    )
    if loki is not None:
        # Saapumishetki erikseen: vuoro kirjataan vasta mallin jälkeen, järjestys pitää näkyä.
        loki.kirjaa(
            istunto_id,
            "kuultu",
            vaihe=kesken.vaihe_ennen,
            kuultu=kentat.get("SpeechResult"),
            varmuus=kentat.get("Confidence"),
            ajoitus=kesken.ajoitus,
        )
    return await _odota_tai_valivastaa(
        istunto_id,
        istunto,
        kesken,
        asetukset.puhelu_valivastaus_s,
        istunnot,
        sessio,
        asetukset,
        loki,
    )


@router.post("/odota/{istunto_id}", include_in_schema=False)
async def odota(
    istunto_id: str,
    kentat: TwilioLomakeDep,
    istunnot: IstunnotDep,
    sessio: SessioDep,
    asetukset: AsetuksetDep,
    loki: LokiDep,
) -> Response:
    """Twilion <Redirect> välivastauksen jälkeen: odottaa kesken jääneen vuoron valmiiksi."""
    istunto = istunnot.hae(istunto_id)
    kesken = istunnot.ota_kesken(istunto_id)
    if istunto is None or kesken is None:
        return twiml_vastaus(virhe_twiml(asetukset.puhe_aani))
    kesken.odotuksia += 1
    return await _odota_tai_valivastaa(
        istunto_id, istunto, kesken, asetukset.puhelu_odotus_s, istunnot, sessio, asetukset, loki
    )


# Enintään näin monta välivastausta per vuoro; sitten puhelu päätetään kohteliaasti.
MAX_ODOTUKSIA = 2


async def _odota_tai_valivastaa(
    istunto_id: str,
    istunto: Puheluistunto,
    kesken: Keskenvuoro,
    aikaraja_s: float,
    istunnot: Istuntovarasto,
    sessio: Session,
    asetukset: Settings,
    loki: Puheluloki | None,
) -> Response:
    try:
        seuraava = await asyncio.wait_for(asyncio.shield(kesken.tehtava), timeout=aikaraja_s)
    except TimeoutError:
        if kesken.odotuksia >= MAX_ODOTUKSIA:
            # Ei pitäisi tapahtua (mallin oma aikaraja on lyhyempi), mutta ei jäädä jumiin.
            kesken.tehtava.cancel()
            _kirjaa(loki, istunto_id, istunto, kesken, None)
            return twiml_vastaus(virhe_twiml(asetukset.puhe_aani))
        kesken.valivastattu = True
        istunnot.aseta_kesken(istunto_id, kesken)
        teksti = VALIVASTAUKSET[istunto.valivastauksia % len(VALIVASTAUKSET)]
        istunto.valivastauksia += 1
        if kesken.odotuksia > 0:
            teksti = VALIVASTAUS_LISA
        if loki is not None:
            loki.kirjaa(
                istunto_id,
                "valivastaus",
                sanottu=teksti,
                odotuksia=kesken.odotuksia,
                vuoron_alusta_ms=round((time.perf_counter() - kesken.alku) * 1000),
            )
        _merkitse_vastattu(istunto, [teksti])
        uudelleen_url = f"{julkinen_url(asetukset)}/puhelu/odota/{istunto_id}"
        return twiml_vastaus(odotus_twiml(teksti, uudelleen_url, asetukset.puhe_aani))

    if kesken.valivastattu:
        seuraava = ilman_lyhytta_kuittausta(seuraava)
    viimeistele_vuoro(istunto, seuraava, istunnot, sessio)
    _kirjaa(loki, istunto_id, istunto, kesken, seuraava)
    _merkitse_vastattu(istunto, seuraava.sanottavat)
    perus_url = julkinen_url(asetukset)
    action_url = f"{perus_url}/puhelu/vuoro/{istunto_id}"
    kuuntelu = kuuntelu_asetuksista(asetukset, istunto.konteksti)
    if loki is not None:
        kuuntelu = replace(kuuntelu, osittais_url=f"{perus_url}/puhelu/osittainen/{istunto_id}")
    twiml = vuoro_twiml(
        seuraava.sanottavat, seuraava.kuuntele, action_url, kuuntelu, seuraava.keskeytymaton
    )
    return twiml_vastaus(twiml)


def _ajoitus(istunto: Puheluistunto, saapui: float) -> dict:
    """Lokia varten: kauanko edellisestä vastauksesta Twiliolle ja arvio sen lukemisesta.
    kuunteluaika_arvio_ms pieni tai negatiivinen = henkilö puhui AI:n puheen aikana."""
    if istunto.viimeksi_vastattu is None:
        return {}
    vali_ms = round((saapui - istunto.viimeksi_vastattu) * 1000)
    return {
        "edellisesta_vastauksesta_ms": vali_ms,
        "ai_puhe_arvio_ms": istunto.viimeksi_sanottu_ms,
        "kuunteluaika_arvio_ms": vali_ms - istunto.viimeksi_sanottu_ms,
    }


def _merkitse_vastattu(istunto: Puheluistunto, sanottavat: list[str]) -> None:
    istunto.viimeksi_vastattu = time.perf_counter()
    istunto.viimeksi_sanottu_ms = puheen_kesto_arvio_ms(sanottavat)


def _kirjaa(
    loki: Puheluloki | None,
    istunto_id: str,
    istunto: Puheluistunto | None,
    kesken: Keskenvuoro | None,
    seuraava: Vuoro | None,
    kentat: dict[str, str] | None = None,
) -> None:
    """Vuoro lokiin kokonaisuudessaan. Twilion kentistä vain puheen tiedot (ei numeroita)."""
    if loki is None:
        return
    kentat = kesken.kentat if kesken is not None else (kentat or {})
    loki.kirjaa(
        istunto_id,
        "vuoro",
        vaihe_ennen=kesken.vaihe_ennen if kesken else None,
        kuultu=kentat.get("SpeechResult"),
        varmuus=kentat.get("Confidence"),
        ajoitus=kesken.ajoitus if kesken else None,
        valivastattu=kesken.valivastattu if kesken else False,
        odotuksia=kesken.odotuksia if kesken else 0,
        mittaukset=dict(istunto.mittaukset) if istunto else None,
        syyt=list(istunto.syyt) if istunto else ["istuntoa ei loydy"],
        sanottu=seuraava.sanottavat if seuraava else None,
        keskeytymaton=seuraava.keskeytymaton if seuraava else 0,
        kuuntele=seuraava.kuuntele if seuraava else False,
        loppu=istunto.loppu if istunto else None,
        kesto_ms=round((time.perf_counter() - kesken.alku) * 1000) if kesken else None,
    )


@router.post("/osittainen/{istunto_id}", status_code=204, include_in_schema=False)
def osittainen(
    istunto_id: str, kentat: TwilioLomakeDep, istunnot: IstunnotDep, loki: LokiDep
) -> Response:
    """VAIN DEV-LOKI: Twilion keskeneräinen tunnistus puheen edetessä (partialResultCallback).
    Näyttää, milloin puhe alkoi kuulua suhteessa AI:n vastaukseen ja mitä alusta puuttuu."""
    if loki is not None:
        istunto = istunnot.hae(istunto_id)
        vastattu = istunto.viimeksi_vastattu if istunto else None
        loki.kirjaa(
            istunto_id,
            "osittainen",
            teksti=kentat.get("UnstableSpeechResult"),
            vakaus=kentat.get("Stability"),
            jarjestys=kentat.get("SequenceNumber"),
            vastauksesta_ms=round((time.perf_counter() - vastattu) * 1000) if vastattu else None,
        )
    return Response(status_code=204)


@router.post("/tila/{istunto_id}", status_code=204, include_in_schema=False)
def tila(
    istunto_id: str,
    kentat: TwilioLomakeDep,
    istunnot: IstunnotDep,
    sessio: SessioDep,
    loki: LokiDep,
) -> Response:
    """Twilion status callback: puhelu paattyi (myos ei vastattu / varattu / katkesi)."""
    if loki is not None:
        loki.kirjaa(
            istunto_id,
            "puhelu_paattyi",
            twilio_tila=kentat.get("CallStatus"),
            kesto_s=kentat.get("CallDuration"),
        )
    kasittele_tila(istunto_id, kentat.get("CallStatus", ""), istunnot, sessio)
    return Response(status_code=204)
