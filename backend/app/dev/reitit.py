"""VAIN DEV: reitit testimuutoksille. Palauttavat 404, ellei DEV_TILA=true."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import Settings, hae_asetukset
from app.data.adapteri import Datalahde, Testikirjoittaja, hae_datalahde, hae_testikirjoittaja
from app.dev.palvelu import TestimuutosVirhe, tee_testimuutos
from app.mallit import (
    KaikuTulos,
    PotilaanLaakemuutokset,
    PuhelunAloitus,
    PuhelunTulos,
    PuhelutehtavienNollaus,
    Testimuutos,
    TestimuutostenNollaus,
    Yhteystieto,
)
from app.puhelu.agentti import Keskusteluagentti, hae_agentti, lammita_ja_merkitse
from app.puhelu.istunnot import Istuntovarasto, hae_istuntovarasto
from app.puhelu.lammitys import Lammitysvahti, hae_lammitysvahti
from app.puhelu.palvelu import (
    aloita_testikeskustelu,
    kaiun_url,
    kasittele_kaiku,
    kuuntelu_asetuksista,
    rajat_asetuksista,
    relay_asetuksista,
    soita_kaikutesti,
    soita_testipuhelu,
)
from app.puhelu.puhe import vuoro_twiml
from app.puhelu.reitit import twiml_vastaus
from app.puhelu.soittaja import Soittaja, hae_soittaja
from app.puhelu.twilio_tarkistus import julkinen_url, twilion_lomake
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


SessioDep = Annotated[Session, Depends(hae_sessio)]
SoittajaDep = Annotated[Soittaja, Depends(hae_soittaja)]
AsetuksetDep = Annotated[Settings, Depends(hae_asetukset)]
IstunnotDep = Annotated[Istuntovarasto, Depends(hae_istuntovarasto)]

_JONO_TYHJA = "Jonossa ei ole tehtavia: aja ensin POST /puhelutehtavat/tunnista"


def _demonumero(asetukset: Settings) -> str:
    if not asetukset.demo_puhelinnumero:
        raise HTTPException(status_code=409, detail="DEMO_PUHELINNUMERO puuttuu .env:sta")
    return asetukset.demo_puhelinnumero


@router.post("/puhelu/testi", response_model=PuhelunAloitus)
def testipuhelu(
    sessio: SessioDep, soittaja: SoittajaDep, asetukset: AsetuksetDep
) -> PuhelunAloitus:
    """Soittaa DEMO_PUHELINNUMEROON ja lukee suomeksi satunnaisen jonotehtavan muutokset ja
    kysymykset. Ei kuuntele. Ei muuta jonoa. Ei tarvitse ngrokia."""
    numero = _demonumero(asetukset)
    tulos = soita_testipuhelu(sessio, soittaja, numero, asetukset.puhe_aani)
    if tulos is None:
        raise HTTPException(status_code=409, detail=_JONO_TYHJA)
    return tulos


@router.post("/puhelu/keskustelu", response_model=PuhelunAloitus)
def testikeskustelu(
    sessio: SessioDep,
    soittaja: SoittajaDep,
    istunnot: IstunnotDep,
    asetukset: AsetuksetDep,
    agentti: Annotated[Keskusteluagentti, Depends(hae_agentti)],
    taustatyot: BackgroundTasks,
    vahti: Annotated[Lammitysvahti | None, Depends(hae_lammitysvahti)],
) -> PuhelunAloitus:
    """Koko keskustelu DEMO_PUHELINNUMEROON satunnaisen tehtavan kysymyksilla: kuuntelee
    vastaukset ja tunnistaa hatasanat. Ei muuta jonoa. Vaatii ngrokin (JULKINEN_URL).
    Tulos: GET /dev/puhelu/tulokset."""
    if vahti is not None and not vahti.lammin:
        # 12.10.: kylmällä mallilla puhelu kulki varatoiminnalla ilman lisäkysymyksiä.
        raise HTTPException(status_code=503, detail=_kylman_mallin_viesti(vahti))
    numero = _demonumero(asetukset)
    perus_url = julkinen_url(asetukset)
    tulos = aloita_testikeskustelu(
        sessio,
        soittaja,
        istunnot,
        numero,
        perus_url,
        rajat_asetuksista(asetukset),
        relay=relay_asetuksista(asetukset),
        # Järjestelmäohje mallin välimuistiin sillä aikaa kun puhelin soi.
        lammitys=lambda viestit, valmis: taustatyot.add_task(
            lammita_ja_merkitse, agentti, viestit, valmis
        ),
    )
    if tulos is None:
        raise HTTPException(status_code=409, detail=_JONO_TYHJA)
    return tulos


def _kylman_mallin_viesti(vahti: Lammitysvahti) -> str:
    if vahti.viimeisin_s is None:
        return "Malli ei ole vielä lämmin (Ollama käynnissä?). Odota hetki ja soita uudelleen."
    return (
        f"Malli lämpenee vielä (viimeisin lämmitys {vahti.viimeisin_s:.0f} s). "
        "Odota hetki ja soita uudelleen."
    )


@router.get("/puhelu/tulokset", response_model=list[PuhelunTulos])
def puhelujen_tulokset(istunnot: IstunnotDep) -> list[PuhelunTulos]:
    """Päättyneiden puhelujen tilapäiset dev-tulokset vastauksineen (uusin ensin)."""
    return istunnot.tulokset()


@router.post("/puhelu/kaiku")
def kaikutesti(soittaja: SoittajaDep, asetukset: AsetuksetDep) -> dict[str, str]:
    """Puheentunnistuksen testi: soittaa DEMO_PUHELINNUMEROON, kuuntelee ja toistaa kuullun
    kolmesti (myos varmuus ja hatamerkki). Vaatii ngrokin. Tulos: GET /dev/puhelu/kaiut."""
    numero = _demonumero(asetukset)
    puhelu_sid = soita_kaikutesti(soittaja, numero, julkinen_url(asetukset))
    return {"puhelu_sid": puhelu_sid}


@router.get("/puhelu/kaiut", response_model=list[KaikuTulos])
def kaikujen_tulokset(istunnot: IstunnotDep) -> list[KaikuTulos]:
    """Mita puheentunnistus kuuli kaikutesteissa (uusin ensin)."""
    return istunnot.kaiut()


@router.post("/puhelu/kaiku/vuoro/{kierros}", include_in_schema=False)
def kaiun_vuoro(
    kierros: int,
    kentat: Annotated[dict[str, str], Depends(twilion_lomake)],
    istunnot: IstunnotDep,
    asetukset: AsetuksetDep,
) -> Response:
    """Twilion webhook kaikutestille."""
    vuoro = kasittele_kaiku(kierros, kentat, istunnot, asetukset.stt_malli)
    seuraava_url = kaiun_url(julkinen_url(asetukset), kierros + 1)
    kuuntelu = kuuntelu_asetuksista(asetukset, konteksti=None)
    twiml = vuoro_twiml(vuoro.sanottavat, vuoro.kuuntele, seuraava_url, kuuntelu)
    return twiml_vastaus(twiml)
