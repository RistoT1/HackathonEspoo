"""Mita puhelussa sanotaan ja TwiML siita. Puhtaat funktiot: ei Twiliota, ei kantaa.

Avaus ja lopetus ovat kiinteita tekstejä, eivat LLM:n tuottamia: AI Act art. 50 -ilmoitus ja
"valitan hoitajalle" eivat saa koskaan jaada pois.
"""

import re
from dataclasses import dataclass

from twilio.twiml.voice_response import Gather, VoiceResponse

from app.mallit import Kysymys, Muutoskonteksti, MuutosTyyppi, Puhelukonteksti

KIELI = "fi-FI"

AVAUS = (
    "Hei, täällä on hyvinvointialueen tekoälyavustaja. "
    "Olen tietokoneohjelma, en ihminen. Soitan, koska lääkitykseesi on tullut muutos, "
    "ja välitän vastauksesi hoitajalle."
)
TESTIN_AVAUS = (
    "Tämä on testipuhelu. Olen tekoäly. "
    "Luen satunnaisen synteettisen potilaan muutokset ja puhelun kysymykset."
)
TESTIN_LOPETUS = "Testi päättyi. Kiitos ja hei hei."

TEKNINEN_VIKA = "Pahoittelut, tapahtui tekninen vika. Hoitaja soittaa sinulle myöhemmin. Näkemiin."

_NUMEROSANAT = (
    "nolla",
    "yksi",
    "kaksi",
    "kolme",
    "neljä",
    "viisi",
    "kuusi",
    "seitsemän",
    "kahdeksan",
    "yhdeksän",
)
# Puhelinnumerot (09 2525 0111, 0800 147 111, 116117, 112) numero kerrallaan: puhesynteesi
# lukisi "112" lukuna "satakaksitoista" (Jere, aani.puheeksi).
_PUHELINNUMERO = re.compile(r"\b(0\d(?:[ -]?\d){5,}|116 ?117|112)\b")


def puheeksi(teksti: str) -> str:
    """Teksti ääneen luettavaksi: puhelinnumerot numero kerrallaan."""
    return _PUHELINNUMERO.sub(
        lambda osuma: ", ".join(_NUMEROSANAT[int(n)] for n in osuma.group(0) if n.isdigit()),
        teksti,
    )


# Twilion calls.create(twiml=...) hyvaksyy enintaan 4000 merkkia.
MAX_TWIML_PITUUS = 4000

_MUUTOKSEN_NIMI = {
    MuutosTyyppi.UUSI_LAAKE: "uusi lääke",
    MuutosTyyppi.LOPETETTU_LAAKE: "lopetettu lääke",
    MuutosTyyppi.ANNOSMUUTOS: "annosmuutos",
    MuutosTyyppi.LYHYT_KUURI: "lyhyt lääkekuuri",
}


class TwimlLiianPitka(ValueError):
    pass


def _milloin(paivia_sitten: int) -> str:
    if paivia_sitten <= 0:
        return "tänään"
    if paivia_sitten == 1:
        return "eilen"
    return f"{paivia_sitten} päivää sitten"


def muutoksen_kuvaus(muutos: Muutoskonteksti) -> str:
    """Esim. 'Uusi lääke, ryhmä verenohennus, 3 päivää sitten.' Ei laakkeen nimea."""
    nimi = _MUUTOKSEN_NIMI[muutos.tyyppi]
    ryhma = f", ryhmä {muutos.laakeryhma}" if muutos.laakeryhma else ""
    milloin = _milloin(muutos.paivia_sitten)
    return f"{nimi.capitalize()}{ryhma}, {milloin}."


def puhelun_repliikit(kysymykset: list[Kysymys]) -> list[str]:
    """Varsinaisen puhelun runko: avaus ja kysymykset jarjestyksessa."""
    return [AVAUS, *(kysymys.teksti for kysymys in kysymykset)]


def testipuhelun_repliikit(kysymykset: list[Kysymys], konteksti: Puhelukonteksti) -> list[str]:
    """VAIN DEV: kertoo kontekstin aaneen ja lukee kysymykset numeroituina."""
    tausta = []
    if konteksti.ika is not None:
        tausta.append(f"Potilas on {konteksti.ika}-vuotias.")
    tausta.append(f"Käytössä on {konteksti.aktiivisia_laakkeita} lääkettä.")
    muutokset = [muutoksen_kuvaus(muutos) for muutos in konteksti.muutokset]
    numeroidut = [
        f"Kysymys {numero}. {kysymys.teksti}" for numero, kysymys in enumerate(kysymykset, 1)
    ]
    return [TESTIN_AVAUS, *tausta, *muutokset, *numeroidut, TESTIN_LOPETUS]


def luo_twiml(repliikit: list[str], aani: str) -> str:
    """Repliikit perakkain lyhyella tauolla. Twilio lukee ne suomeksi."""
    vastaus = VoiceResponse()
    for repliikki in repliikit:
        vastaus.say(repliikki, voice=aani)
        vastaus.pause(length=1)
    twiml = str(vastaus)

    if len(twiml) > MAX_TWIML_PITUUS:
        raise TwimlLiianPitka(f"TwiML {len(twiml)} merkkia, Twilion raja {MAX_TWIML_PITUUS}")
    return twiml


# Twilio: enintaan 500 vihjetta, kukin enintaan 100 merkkia.
MAX_VIHJEITA = 500
MAX_VIHJEEN_PITUUS = 100


@dataclass(frozen=True)
class Kuuntelu:
    """Twilion puheentunnistuksen (<Gather>) asetukset yhdelle vuorolle."""

    aani: str
    tauko_s: int  # hiljaisuus, jonka jalkeen puhe katsotaan loppuneeksi
    odotus_s: int  # kauanko odotetaan puheen alkamista ennen kuin vuoro on hiljainen
    malli: str | None = None  # Twilion speechModel; None = Twilion oletus
    vihjeet: tuple[str, ...] = ()  # odotetut sanat/fraasit (Twilion hints)
    # VAIN DEV-LOKI: Twilio lähettää keskeneräisen tunnistuksen tähän puheen edetessä
    # (partialResultCallback), jolloin lokista näkee, milloin puhe alkoi kuulua.
    osittais_url: str | None = None


def vihjeet_merkkijonoksi(vihjeet: tuple[str, ...]) -> str:
    """Twilion hints-muoto. Tuplat, liian pitkat ja pilkulliset pois, jarjestys sailyy."""
    kelvolliset: list[str] = []
    for vihje in vihjeet:
        siisti = vihje.strip()
        kelpaa = siisti and len(siisti) <= MAX_VIHJEEN_PITUUS and "," not in siisti
        if kelpaa and siisti not in kelvolliset:
            kelvolliset.append(siisti)
    return ",".join(kelvolliset[:MAX_VIHJEITA])


def vuoro_twiml(
    sanottavat: list[str],
    kuuntele: bool,
    action_url: str,
    kuuntelu_asetukset: Kuuntelu,
    keskeytymaton: int = 0,
) -> str:
    """Yksi keskustelun vuoro. kuuntele=True: puhe <Gather>:in sisalla, jolloin vanhus voi
    keskeyttaa ja vastaus lahetetaan action_url:iin (myos tyhja, jotta hiljaisuus kasitellaan).
    kuuntele=False: puhe ja <Hangup/>.
    keskeytymaton: näin monta ensimmäistä lausetta sanotaan ennen kuuntelua, jolloin puhe
    ei voi katkaista niitä. Vähintään kaikki paitsi viimeinen (kysymys).

    Tauko on aina sekunteja, ei "auto": auto lopettaa kuuntelun ensimmaiseen taukoon, mika
    katkaisee hitaasti puhuvan kesken lauseen (ja Twilio kieltaa auton, kun malli on valittu).
    """
    aani = kuuntelu_asetukset.aani
    vastaus = VoiceResponse()
    if not kuuntele:
        for teksti in sanottavat:
            vastaus.say(puheeksi(teksti), voice=aani)
        vastaus.hangup()
        return str(vastaus)

    # Kaikki puhe kuuntelun ulkopuolelle: <Gather>:in sisällä mikä tahansa ääni linjalla
    # (henkilön "mm", häly, kaiutinpuhelimen kaiku) katkaisee AI:n kesken lauseen
    # (11.10.). Hinta: kysymyksen aikana alkanut vastaus voi pätkiä alusta. Molempiin
    # (luetaan loppuun JA kuunnellaan) tarvitaan ConversationRelay (relay.py).
    keskeytymaton = len(sanottavat)  # Gather-mallissa kaikki luetaan loppuun (ks. alla)
    for teksti in sanottavat[:keskeytymaton]:
        vastaus.say(puheeksi(teksti), voice=aani)
    kuunneltavat = sanottavat[keskeytymaton:]

    lisat: dict[str, str] = {}
    if kuuntelu_asetukset.osittais_url:
        lisat["partial_result_callback"] = kuuntelu_asetukset.osittais_url
        lisat["partial_result_callback_method"] = "POST"
    if kuuntelu_asetukset.malli:
        lisat["speech_model"] = kuuntelu_asetukset.malli
    vihjeet = vihjeet_merkkijonoksi(kuuntelu_asetukset.vihjeet)
    if vihjeet:
        lisat["hints"] = vihjeet

    kuuntelu = Gather(
        input="speech",
        language=KIELI,
        action=action_url,
        method="POST",
        speech_timeout=kuuntelu_asetukset.tauko_s,
        timeout=kuuntelu_asetukset.odotus_s,
        action_on_empty_result=True,
        **lisat,
    )
    for teksti in kuunneltavat:
        kuuntelu.say(puheeksi(teksti), voice=aani)
    vastaus.append(kuuntelu)
    return str(vastaus)


@dataclass(frozen=True)
class Relay:
    """Twilio ConversationRelay -asetukset (config.py: RELAY_*)."""

    tts: str
    aani: str
    stt: str
    stt_malli: str | None
    tauko_ms: int
    vihjeet: bool = False  # Googlen long-malli fi-FI hylkää hints-vihjeet (64101)
    osittaiset: bool = True  # partialPrompts: puheen alkuhetki relaylle


def relay_twiml(ws_url: str, loppu_url: str, relay: Relay, vihjeet: tuple[str, ...]) -> str:
    """Puhelun alku ConversationRelaylla. interruptible=none: AI puhuu lauseen loppuun;
    reportInputDuringAgentSpeech=speech: henkilön puhe kuullaan silti samaan aikaan.
    Avaus lähetetään websocketista (tilakone), ei welcomeGreetingillä."""
    vastaus = VoiceResponse()
    yhteys = vastaus.connect(action=loppu_url, method="POST")
    lisat: dict[str, object] = {}
    if relay.stt_malli:
        lisat["speech_model"] = relay.stt_malli
    vihjeteksti = vihjeet_merkkijonoksi(vihjeet) if relay.vihjeet else ""
    if vihjeteksti:
        lisat["hints"] = vihjeteksti
    yhteys.conversation_relay(
        url=ws_url,
        language=KIELI,
        tts_provider=relay.tts,
        voice=relay.aani,
        transcription_provider=relay.stt,
        interruptible="none",
        report_input_during_agent_speech="speech",
        # Osittaiset tunnistukset: relay tietää, milloin henkilö ALKOI puhua (relay.py).
        partial_prompts=relay.osittaiset,
        speech_timeout=max(600, min(5000, relay.tauko_ms)),
        **lisat,
    )
    return str(vastaus)


def relay_lopetusaani(tts: str, aani: str, vara_aani: str) -> tuple[str, str | None]:
    """Muunna ConversationRelay-ääni saman palveluntarjoajan <Say>-ääneksi.

    Relayn ElevenLabs-äänessä voi olla malli- ja nopeusasetukset, joita <Say> ei tue.
    <Say> tarvitsee lisäksi palveluntarjoajan etuliitteen ja suomen kielikoodin.
    """
    if tts.casefold() == "elevenlabs":
        tunniste = aani.split("-", 1)[0]
        return f"ElevenLabs.{tunniste}", KIELI
    return vara_aani, None


def lopetus_twiml(sanottavat: list[str], aani: str, kieli: str | None = None) -> str:
    """ConversationRelayn jälkeen: viimeiset lauseet loppuun asti ja puhelu kiinni."""
    vastaus = VoiceResponse()
    for teksti in sanottavat:
        asetukset = {"voice": aani}
        if kieli:
            asetukset["language"] = kieli
        vastaus.say(puheeksi(teksti), **asetukset)
    vastaus.hangup()
    return str(vastaus)


def puheen_kesto_arvio_ms(sanottavat: list[str]) -> int:
    """Karkea arvio ääneen lukemisen kestosta (~14 merkkiä/s, Polly suomi). Ei mitattu
    Twiliosta. Lokin lisäksi relay ajoittaa sillä kysymyksen alun, kaiun ja hiljaisuuden."""
    merkkeja = sum(len(teksti) for teksti in sanottavat)
    return round(merkkeja / 14 * 1000)


def kysymyksen_alku_arvio_ms(sanottavat: list[str]) -> int:
    """Arvio, milloin repliikin viimeinen kysymys alkaa kuulua (ilman kysymystä: koko
    repliikin kesto). Sitä ennen tullut puhe ei voi olla vastaus siihen (relay)."""
    lauseet = re.findall(r"[^.!?]+[.!?]*", " ".join(sanottavat))
    kysymykset = [indeksi for indeksi, lause in enumerate(lauseet) if lause.strip().endswith("?")]
    ennen = lauseet[: kysymykset[-1]] if kysymykset else lauseet
    return puheen_kesto_arvio_ms(ennen)


# Välivastaus, kun malli miettii pidempään: lyhyt kuuntelun merkki, vaihtuu joka kerta
# (11.10.: "Hetkinen, kirjaan tämän" joka vuorolla ärsytti ja kuulosti kylmältä).
# Pelkkä "Okei." oli liian ohut (11.10.): välitäyte kertoo, mitä tapahtuu.
VALIVASTAUKSET = (
    "Okei, merkitsen tämän.",
    "Selvä, kirjaan tämän ylös.",
    "Ymmärrän, merkitsen tämän.",
    "Kiitos, kirjaan tämän.",
)
VALIVASTAUS_LISA = "Hetki vielä."


def odotus_twiml(teksti: str, uudelleen_url: str, aani: str) -> str:
    """Sanotaan välivastaus ja pyydetään Twiliota hakemaan vuoro uudelleen (<Redirect>)."""
    vastaus = VoiceResponse()
    vastaus.say(puheeksi(teksti), voice=aani)
    vastaus.redirect(uudelleen_url, method="POST")
    return str(vastaus)


def virhe_twiml(aani: str) -> str:
    """Kun puhelun tilaa ei loydy (esim. backend kaynnistyi uudelleen kesken puhelun)."""
    vastaus = VoiceResponse()
    vastaus.say(TEKNINEN_VIKA, voice=aani)
    vastaus.hangup()
    return str(vastaus)
