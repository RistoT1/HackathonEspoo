"""Twilio ConversationRelay: puhelu websocketilla (PUHELU_TAPA=relay).

Twilio hoitaa puheentunnistuksen ja -synteesin; tähän tulee henkilön puhe tekstinä
("prompt") ja tästä lähtee AI:n puhe tekstinä ("text"). Tilakone (keskustelu.py) on sama
kuin Gather-mallissa. Ero: interruptible=none + reportInputDuringAgentSpeech=speech, eli
AI puhuu lauseen loppuun JA henkilön puhe kuullaan samaan aikaan (11.10.: Gatherissa joko
puhe katkesi tai vastauksen alku jäi kuulematta).

Viestit: https://www.twilio.com/docs/voice/conversationrelay/websocket-messages
"""

import asyncio
import json
import logging
import re
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session, sessionmaker
from twilio.request_validator import RequestValidator

from app.config import Settings, hae_asetukset
from app.puhelu.agentti import Keskusteluagentti, hae_agentti
from app.puhelu.istunnot import Istuntovarasto, hae_istuntovarasto
from app.puhelu.keskustelu import (
    Puheluistunto,
    Vuoro,
    ilman_lyhytta_kuittausta,
    kasittele,
    liita_edelliseen,
    palauta,
    tilannekuva,
)
from app.puhelu.kielentarkistus import Tarkistin, hae_tarkistin
from app.puhelu.loki import Puheluloki, hae_puheluloki
from app.puhelu.palvelu import kasittele_tila, viimeistele_vuoro
from app.puhelu.puhe import (
    VALIVASTAUKSET,
    VALIVASTAUS_LISA,
    kysymyksen_alku_arvio_ms,
    lopetus_twiml,
    puheeksi,
    puheen_kesto_arvio_ms,
    virhe_twiml,
)
from app.puhelu.saannot import ei_ohitettava
from app.puhelu.twilio_tarkistus import twilion_lomake
from app.tietokanta import hae_sessio, hae_sessiotehdas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/puhelu", tags=["puhelu"])

# Ensimmäisen välitäytteen jälkeen lisätäyte näin usein, kunnes malli on valmis.
TOINEN_TAYTE_S = 9.0
LISATAYTTEET = (VALIVASTAUS_LISA, "Mietin vielä hetken.", "Kiitos kärsivällisyydestä, hetki vielä.")
# Montako kertaa vuoro aloitetaan alusta, kun henkilö jatkaa puhettaan mallin miettiessä.
# Sen jälkeen jatko ohitetaan, ettei vuoro venyisi loputtomiin.
MAX_JATKOT = 3
# AI:n puheen kesto on arvio (14 merkkiä/s, ei mitattu Twiliosta). Puhe, joka ALKOI ennen
# kysymystä, ei ole vastaus siihen. Väärä luokitus ei jumita: hiljaisuusvahti kysyy uudelleen.
KYSYMYKSEN_ALUN_OSUUS = 0.9
# Henkilön puheen alku ilman osittaista tunnistusta: arvio tekstin pituudesta (arvio, ei
# mitattu). Hidas tahti rajaa vanhan osittaisen (kohina ilman lopullista tunnistusta).
PUHENOPEUS_MERKKIA_S = 12.0
HIDAS_PUHE_MERKKIA_S = 6.0
# Näin kauan AI:n kysymyksen jälkeen ilman puhetta -> kysymys toistetaan (_hiljainen_vuoro).
HILJAISUUS_S = 15.0
# Kaiku: AI:n oma puhe kuuluu puhelimen kaiuttimesta takaisin tunnistukseen (11.10.: välitäyte
# "Selvä, kirjaan tämän ylös." -> "Selvä kirja." -> malli neuvoi siirtämään "kirjan"). Kaiku
# alkaa AI:n puheen aikana tai pian sen jälkeen (Twilion viive, arvio).
KAIUN_VIIVE_S = 6.0
KAIUN_MIN_SANAT = 2  # yksi sana ("ei", "kyllä") on liian usein aito vastaus
SANOTTUJA_MUISTISSA = 6


def _sanat(teksti: str) -> list[str]:
    return re.findall(r"[a-zåäö0-9]+", teksti.lower())


def _sama_sana(kuultu: str, sanottu: str) -> bool:
    """Tunnistus katkaisee sanoja ("kirjaan" -> "kirja"): alku riittää, lyhyet tarkasti."""
    if min(len(kuultu), len(sanottu)) < 3:
        return kuultu == sanottu
    return sanottu.startswith(kuultu) or kuultu.startswith(sanottu)


def on_kaikua(kuultu: str, sanottu: str) -> bool:
    """Kuultu on AI:n tekstin pätkä: vähintään KAIUN_MIN_SANAT sanaa PERÄKKÄIN samassa
    järjestyksessä. Aito vastaus lainaa kysymyksen sanoja harvoin peräkkäin ("On toiminut."
    vs "Onko vatsasi toiminut?" ei ole kaikua)."""
    kuullut = _sanat(kuultu)
    sanotut = _sanat(sanottu)
    if len(kuullut) < KAIUN_MIN_SANAT:
        return False
    for alku in range(len(sanotut) - len(kuullut) + 1):
        if all(_sama_sana(sana, sanotut[alku + i]) for i, sana in enumerate(kuullut)):
            return True
    return False


async def _laheta_puhe(ws: WebSocket, sanottavat: list[str]) -> None:
    """AI:n puhe Twiliolle lause kerrallaan; viimeinen last=true. Ei keskeytettävissä."""
    for indeksi, teksti in enumerate(sanottavat):
        viimeinen = indeksi == len(sanottavat) - 1
        luettava = puheeksi(teksti)
        await ws.send_json(
            {
                "type": "text",
                "token": luettava if viimeinen else f"{luettava} ",
                "last": viimeinen,
                "interruptible": False,
            }
        )


class Relayistunto:
    """Yhden puhelun websocket-tila: vuorot välitäytteineen ja henkilön puheen luokittelu
    (kaiku, ennen kysymystä alkanut, mallin miettiessä jatkettu, hätä, hiljaisuus)."""

    def __init__(
        self,
        ws: WebSocket,
        istunto: Puheluistunto,
        istunnot: Istuntovarasto,
        agentti: Keskusteluagentti,
        tarkistin: Tarkistin,
        asetukset: Settings,
        loki: Puheluloki | None,
        sessiotehdas: sessionmaker[Session],
    ) -> None:
        self.ws = ws
        self.istunto = istunto
        self.istunnot = istunnot
        self.agentti = agentti
        self.tarkistin = tarkistin
        self.asetukset = asetukset
        self.loki = loki
        self.sessiotehdas = sessiotehdas
        self.puskuri: list[str] = []
        self.tyo: asyncio.Task | None = None
        self.kiire = asyncio.Event()  # hätä tai vakava huoli kesken vuoron
        self.jatko: list[str] = []  # mallin miettiessä jatkettu puhe (sama vastaus)
        self.jatkettu = asyncio.Event()
        self.odottaa_mallia = False  # henkilön vuoro kesken, AI ei ole vielä vastannut
        # Arviot (time.monotonic): milloin Twilion puhejono tyhjenee ja milloin viimeisimmän
        # repliikin kysymys alkaa kuulua.
        self.puhe_loppuu = 0.0
        self.kysymys_kuuluu = 0.0
        # Henkilön puhe: ensimmäisen osittaisen tunnistuksen hetki (None = ei puhu nyt),
        # viimeisin kuultu ja vastausten laskuri hiljaisuusvahdille.
        self.puhe_alkoi: float | None = None
        self.viimeksi_kuultu = 0.0
        self.vastauksia = 0
        self.vahti: asyncio.Task | None = None
        # AI:n viimeisimmät puheet kaikutarkistukseen: (alkaa, loppuu, teksti), monotonic.
        self.sanotut: list[tuple[float, float, str]] = []
        self.paattynyt = False

    def osittainen(self) -> None:
        """Osittainen tunnistus (partialPrompts): henkilö puhuu. Ensimmäinen kertoo alun."""
        nyt = time.monotonic()
        if self.puhe_alkoi is None:
            self.puhe_alkoi = nyt
            self._kirjaa("puhe_alkoi")
        self.viimeksi_kuultu = nyt

    def _puheen_alku(self, teksti: str) -> float:
        """Milloin henkilö alkoi sanoa tämän: osittaisesta tunnistuksesta, muuten arvio."""
        nyt = time.monotonic()
        tauko_s = self.asetukset.relay_puheen_tauko_ms / 1000
        if self.puhe_alkoi is None:
            return nyt - tauko_s - len(teksti) / PUHENOPEUS_MERKKIA_S
        aikaisin = nyt - tauko_s - len(teksti) / HIDAS_PUHE_MERKKIA_S - 2.0
        return max(self.puhe_alkoi, aikaisin)

    def ota_vastaan(self, teksti: str) -> None:
        """Henkilön puhe. Mallin miettiessä tullut puhe on saman vastauksen jatkoa: henkilö
        piti tauon kesken ajatuksen, eikä ole vielä kuullut seuraavaa kysymystä (11.10.:
        "...vatsa ei toimi ja" + "olo on kun kännissä" hukattiin). Vuoro aloitetaan alusta
        koko puheella. Aiemmin jatko kirjattiin vastaukseksi SEURAAVAAN kysymykseen ("mä
        mietin... anteeksi"), siksi sitä ei puskuroida seuraavalle vuorolle. Hätämerkki ja
        vakava huoli keskeyttävät vuoron ja käsitellään heti omana vuoronaan.

        Puhe, joka ALKOI ennen kuin AI ehti kysymykseen, ei ole vastaus siihen, vaikka se
        loppuisi kysymyksen jälkeen (11.10.: myöhästynyt vastaus edelliseen kysymykseen
        luettiin vastaukseksi seuraavaan). Se liitetään edelliseen vastaukseen.

        AI:n oman puheen kaiku hylätään ensimmäisenä, myös ennen hätätarkistusta: kaikuna
        palannut "soita 112" ei ole henkilön hätä."""
        alkoi = self._puheen_alku(teksti)
        self.puhe_alkoi = None
        self.viimeksi_kuultu = time.monotonic()
        kaiku = self._kaiku(teksti, alkoi)
        if kaiku is not None:
            self._kirjaa("kaiku", kuultu=teksti, sanottu=kaiku)
            return
        vuoro_kesken = self.tyo is not None and not self.tyo.done()
        ennen_kysymysta = alkoi < self.kysymys_kuuluu
        if not vuoro_kesken and ennen_kysymysta and not ei_ohitettava(teksti):
            liitetty = liita_edelliseen(self.istunto, teksti)
            self._kirjaa("puhe_ai_puhuessa", kuultu=teksti, liitetty=liitetty)
            return
        self.vastauksia += 1
        if not vuoro_kesken:
            self.puskuri.append(teksti)
            self.tyo = asyncio.create_task(self._tyontekija())
            return
        if ei_ohitettava(teksti):
            self.puskuri.append(teksti)
            self._kirjaa("kiireellinen_puhe", kuultu=teksti)
            self.kiire.set()
            return
        if self.odottaa_mallia:
            self.jatko.append(teksti)
            self._kirjaa("jatkettu_puhe", kuultu=teksti)
            self.jatkettu.set()
            return
        self._kirjaa("ohitettu_puhe", kuultu=teksti, syy="vuoro kesken")

    async def aloita(self) -> None:
        await self._vuoro(None)

    async def _tyontekija(self) -> None:
        while self.puskuri and not self.paattynyt:
            teksti = " ".join(self.puskuri)
            self.puskuri.clear()
            await self._vuoro(teksti)

    async def _vuoro(self, teksti: str | None) -> None:
        istunto = self.istunto
        alku = time.perf_counter()
        vaihe_ennen = istunto.vaihe.value
        self._kirjaa("kuultu", vaihe=vaihe_ennen, kuultu=teksti)
        self.kiire.clear()
        self.jatkettu.clear()
        self.jatko.clear()
        kuva = tilannekuva(istunto)
        tehtava = self._kasittely(teksti)
        kiire = asyncio.create_task(self.kiire.wait())
        jatkettu = asyncio.create_task(self.jatkettu.wait())
        aikaraja = self.asetukset.puhelu_valivastaus_s
        taytteita = 0
        jatkoja = 0
        self.odottaa_mallia = teksti is not None
        try:
            while True:
                valmiit, _ = await asyncio.wait(
                    {tehtava, kiire, jatkettu},
                    timeout=aikaraja,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if jatkettu in valmiit and teksti is not None:
                    # Henkilö jatkoi samaa vastausta: vuoro perutaan ja aloitetaan alusta
                    # koko puheella (myös jos malli ehti valmiiksi: repliikkiä ei ole sanottu).
                    tehtava.cancel()
                    await asyncio.gather(tehtava, return_exceptions=True)
                    palauta(istunto, kuva)
                    teksti = " ".join([teksti, *self.jatko])
                    self.jatko.clear()
                    self.jatkettu.clear()
                    jatkoja += 1
                    self.odottaa_mallia = jatkoja < MAX_JATKOT
                    self._kirjaa("vuoro_alusta", kuultu=teksti, jatkoja=jatkoja)
                    tehtava = self._kasittely(teksti)
                    jatkettu = asyncio.create_task(self.jatkettu.wait())
                    # Henkilö puhui juuri: täytteen ajastus alusta.
                    ensimmainen_tayte = taytteita == 0
                    aikaraja = (
                        self.asetukset.puhelu_valivastaus_s if ensimmainen_tayte else TOINEN_TAYTE_S
                    )
                    continue
                if tehtava in valmiit:
                    vuoro = tehtava.result()
                    break
                if kiire in valmiit:
                    # Mallin odottelu (await) on vuoron ainoa keskeytyskohta: historiaan ei
                    # jää lukematonta repliikkiä. Hätä käsitellään seuraavana vuorona.
                    tehtava.cancel()
                    self._kirjaa("vuoro_keskeytetty", syy="kiireellinen puhe")
                    return
                # Pitkä hiljaisuus saa vanhuksen luulemaan, että puhelu katkesi: täyte
                # välein, kunnes malli on valmis (tarkkuus ennen nopeutta, vuoro voi kestää).
                if taytteita == 0:
                    tayte = VALIVASTAUKSET[istunto.valivastauksia % len(VALIVASTAUKSET)]
                    istunto.valivastauksia += 1
                else:
                    tayte = LISATAYTTEET[(taytteita - 1) % len(LISATAYTTEET)]
                taytteita += 1
                await self._puhu([tayte], kysyy=False)
                self._kirjaa("valivastaus", sanottu=tayte)
                aikaraja = TOINEN_TAYTE_S
        finally:
            self.odottaa_mallia = False
            kiire.cancel()
            jatkettu.cancel()
            if self.jatko:
                # Tuli juuri kun malli valmistui: vastaus on jo matkalla, ei aloiteta alusta.
                self._kirjaa("ohitettu_puhe", kuultu=" ".join(self.jatko), syy="vastaus valmis")
                self.jatko.clear()
        valivastattu = taytteita > 0
        if valivastattu:
            vuoro = ilman_lyhytta_kuittausta(vuoro)

        self._kirjaa(
            "vuoro",
            vaihe_ennen=vaihe_ennen,
            kuultu=teksti,
            mittaukset=dict(istunto.mittaukset),
            syyt=list(istunto.syyt),
            sanottu=vuoro.sanottavat,
            kuuntele=vuoro.kuuntele,
            valivastattu=valivastattu,
            loppu=istunto.loppu,
            kesto_ms=round((time.perf_counter() - alku) * 1000),
        )
        if vuoro.kuuntele:
            await self._puhu(vuoro.sanottavat, kysyy=True)
            return
        await self._lopeta(vuoro)

    def _kaiku(self, teksti: str, alkoi: float) -> str | None:
        """AI:n puhe, jonka kaikua kuultu on (alkoi sen aikana tai KAIUN_VIIVE_S sisällä)."""
        for alku, loppu, sanottu in reversed(self.sanotut):
            # Alaraja väljä: ilman osittaista tunnistusta puheen alku on arvio.
            aikana = alku - 2.0 <= alkoi <= loppu + KAIUN_VIIVE_S
            # Myös luettu muoto: puheeksi lukee numerot numero kerrallaan ("1 1 2").
            if aikana and any(on_kaikua(teksti, muoto) for muoto in (sanottu, puheeksi(sanottu))):
                return sanottu
        return None

    async def _puhu(self, sanottavat: list[str], kysyy: bool) -> None:
        """Puhe Twiliolle ja arvio sen kestosta: repliikki alkaa kuulua vasta, kun aiemmin
        lähetetty puhe (välitäyte) on luettu."""
        alku = max(time.monotonic(), self.puhe_loppuu)
        self.puhe_loppuu = alku + puheen_kesto_arvio_ms(sanottavat) / 1000
        self.sanotut = [*self.sanotut, (alku, self.puhe_loppuu, " ".join(sanottavat))][
            -SANOTTUJA_MUISTISSA:
        ]
        if kysyy:
            kysymykseen_s = kysymyksen_alku_arvio_ms(sanottavat) / 1000
            self.kysymys_kuuluu = alku + KYSYMYKSEN_ALUN_OSUUS * kysymykseen_s
            self._vahdi_uudelleen()
        await _laheta_puhe(self.ws, sanottavat)

    def _vahdi_uudelleen(self) -> None:
        if self.vahti is not None:
            self.vahti.cancel()
        self.vahti = asyncio.create_task(self._vahdi_hiljaisuutta(self.vastauksia))

    async def _vahdi_hiljaisuutta(self, vastauksia: int) -> None:
        """Ei vastausta kysymykseen: toistetaan se (kasittele tyhjällä -> _hiljainen_vuoro,
        toisella kerralla lopetus). Relay ei kerro hiljaisuudesta itse. Pelastaa myös, jos
        vastaus luokiteltiin väärin kysymystä edeltäneeksi puheeksi."""
        while True:
            hiljaa_asti = max(self.puhe_loppuu, self.viimeksi_kuultu) + HILJAISUUS_S
            odotus = hiljaa_asti - time.monotonic()
            if odotus <= 0:
                break
            await asyncio.sleep(odotus)
        vuoro_kesken = self.tyo is not None and not self.tyo.done()
        if self.paattynyt or vuoro_kesken or self.vastauksia != vastauksia:
            return
        self._kirjaa("hiljaisuus", odotettu_s=HILJAISUUS_S)
        self.puhe_alkoi = None
        self.puskuri.append("")
        self.tyo = asyncio.create_task(self._tyontekija())

    def _kasittely(self, teksti: str | None) -> asyncio.Task[Vuoro]:
        return asyncio.create_task(
            kasittele(self.istunto, teksti, None, self.agentti, self.tarkistin)
        )

    async def _lopeta(self, vuoro: Vuoro) -> None:
        """Viimeiset lauseet (lopetus, 112-ohje) luetaan <Connect action>:n TwiML:llä, jotta
        ne kuuluvat kokonaan ennen puhelun sulkemista."""
        self.paattynyt = True
        if self.vahti is not None:
            self.vahti.cancel()
        with self.sessiotehdas() as sessio:
            viimeistele_vuoro(self.istunto, vuoro, self.istunnot, sessio)
        await self.ws.send_json(
            {"type": "end", "handoffData": json.dumps({"sanottavat": vuoro.sanottavat})}
        )

    def _kirjaa(self, tapahtuma: str, **tiedot: object) -> None:
        if self.loki is not None:
            self.loki.kirjaa(self.istunto.istunto_id, tapahtuma, tapa="relay", **tiedot)


def _allekirjoitus_kelpaa(ws: WebSocket, istunto_id: str, asetukset: Settings) -> bool:
    """Twilio allekirjoittaa ConversationRelayn websocket-kättelyn (Onboarding-ohje).
    Allekirjoitettu osoite on wss-muodossa; https-muoto hyväksytään varalla."""
    allekirjoitus = ws.headers.get("x-twilio-signature", "")
    if not asetukset.twilio_auth_token or not asetukset.julkinen_url or not allekirjoitus:
        return False
    validaattori = RequestValidator(asetukset.twilio_auth_token)
    https_url = f"{asetukset.julkinen_url.rstrip('/')}/puhelu/relay/{istunto_id}"
    wss_url = https_url.replace("https://", "wss://", 1)
    return any(validaattori.validate(url, {}, allekirjoitus) for url in (wss_url, https_url))


@router.websocket("/relay/{istunto_id}")
async def relay(
    ws: WebSocket,
    istunto_id: str,
    istunnot: Annotated[Istuntovarasto, Depends(hae_istuntovarasto)],
    agentti: Annotated[Keskusteluagentti, Depends(hae_agentti)],
    tarkistin: Annotated[Tarkistin, Depends(hae_tarkistin)],
    asetukset: Annotated[Settings, Depends(hae_asetukset)],
    loki: Annotated[Puheluloki | None, Depends(hae_puheluloki)],
    sessiotehdas: Annotated[sessionmaker[Session], Depends(hae_sessiotehdas)],
) -> None:
    if not _allekirjoitus_kelpaa(ws, istunto_id, asetukset):
        logger.warning("Relay: virheellinen X-Twilio-Signature (istunto %s)", istunto_id)
        await ws.close(code=1008)
        return
    await ws.accept()
    istunto = istunnot.hae(istunto_id)
    if istunto is None:
        await ws.close(code=1008)
        return

    # Ensimmäinen viesti on setup. CallSidin pitää olla tämän soiton: toinen varmistus
    # allekirjoituksen lisäksi.
    setup = await ws.receive_json()
    oikea_puhelu = setup.get("type") == "setup" and setup.get("callSid") == istunto.puhelu_sid
    if not oikea_puhelu:
        logger.warning("Relay: väärä tai puuttuva setup (istunto %s)", istunto_id)
        await ws.close(code=1008)
        return

    yhteys = Relayistunto(ws, istunto, istunnot, agentti, tarkistin, asetukset, loki, sessiotehdas)
    await yhteys.aloita()
    try:
        while not yhteys.paattynyt:
            viesti = await ws.receive_json()
            tyyppi = viesti.get("type")
            if tyyppi == "prompt" and viesti.get("last", True):
                teksti = (viesti.get("voicePrompt") or "").strip()
                if teksti:
                    yhteys.ota_vastaan(teksti)
                else:
                    yhteys.puhe_alkoi = None  # kohina: ei puhetta
            elif tyyppi == "prompt":
                yhteys.osittainen()
            elif tyyppi == "error":
                logger.warning("Relay-virhe Twiliolta: %s", viesti.get("description"))
                yhteys._kirjaa("relay_virhe", kuvaus=viesti.get("description"))
            elif tyyppi == "interrupt":
                yhteys._kirjaa("keskeytys", **{k: v for k, v in viesti.items() if k != "type"})
            elif tyyppi != "prompt":
                # Vianetsintä: muut Twilion viestit (info, dtmf ...) lokiin sellaisenaan.
                tiedot = {k: v for k, v in viesti.items() if k != "type"}
                yhteys._kirjaa(f"relay_{tyyppi}", **tiedot)
    except WebSocketDisconnect:
        pass
    finally:
        if yhteys.tyo is not None and not yhteys.tyo.done():
            yhteys.tyo.cancel()
        if yhteys.vahti is not None:
            yhteys.vahti.cancel()


@router.post("/relay-loppu/{istunto_id}", include_in_schema=False)
def relay_loppu(
    istunto_id: str,
    kentat: Annotated[dict[str, str], Depends(twilion_lomake)],
    istunnot: Annotated[Istuntovarasto, Depends(hae_istuntovarasto)],
    sessio: Annotated[Session, Depends(hae_sessio)],
    asetukset: Annotated[Settings, Depends(hae_asetukset)],
    loki: Annotated[Puheluloki | None, Depends(hae_puheluloki)],
) -> Response:
    """<Connect action>: relay-istunto päättyi. Luetaan viimeiset lauseet ja suljetaan."""
    if loki is not None:
        loki.kirjaa(
            istunto_id,
            "relay_paattyi",
            tila=kentat.get("SessionStatus"),
            virhe=kentat.get("ErrorCode"),
            virheviesti=kentat.get("ErrorMessage"),
        )
    try:
        sanottavat = json.loads(kentat.get("HandoffData") or "{}").get("sanottavat") or []
    except (json.JSONDecodeError, AttributeError):
        sanottavat = []
    if not sanottavat:
        # Ei hallittua lopetusta (yhteysvirhe tai henkilö katkaisi): puhelu päätetään.
        kasittele_tila(istunto_id, kentat.get("CallStatus", ""), istunnot, sessio)
        twiml = virhe_twiml(asetukset.puhe_aani) if kentat.get("ErrorCode") else None
        return Response(
            content=twiml or lopetus_twiml([], asetukset.puhe_aani), media_type="application/xml"
        )
    return Response(
        content=lopetus_twiml(sanottavat, asetukset.puhe_aani), media_type="application/xml"
    )
