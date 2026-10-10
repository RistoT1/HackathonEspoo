"""Mita puhelussa sanotaan ja TwiML siita. Puhtaat funktiot: ei Twiliota, ei kantaa.

Avaus ja lopetus ovat kiinteita tekstejä, eivat LLM:n tuottamia: AI Act art. 50 -ilmoitus ja
"valitan hoitajalle" eivat saa koskaan jaada pois.
"""

from dataclasses import dataclass

from twilio.twiml.voice_response import Gather, VoiceResponse

from app.mallit import Kysymys, Muutoskonteksti, MuutosTyyppi, Puhelukonteksti

KIELI = "fi-FI"

AVAUS = (
    "Hei, täällä on hyvinvointipalvelun tekoälyavustaja. "
    "Olen tietokoneohjelma, en ihminen. Soitan, koska lääkitykseenne on tullut muutos."
)
LOPETUS = (
    "Kiitos. Välitän tämän hoitajalle. Jos olonne huononee, soittakaa hätänumeroon yksi yksi kaksi."
)
TESTIN_AVAUS = (
    "Tämä on testipuhelu. Olen tekoäly. "
    "Luen satunnaisen synteettisen potilaan muutokset ja puhelun kysymykset."
)
TESTIN_LOPETUS = "Testi päättyi. Kiitos ja hei hei."

TEKNINEN_VIKA = "Pahoittelut, tapahtui tekninen vika. Hoitaja soittaa teille myöhemmin. Näkemiin."

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
    """Varsinaisen puhelun runko: avaus, kysymykset jarjestyksessa, lopetus."""
    return [AVAUS, *(kysymys.teksti for kysymys in kysymykset), LOPETUS]


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
    sanottavat: list[str], kuuntele: bool, action_url: str, kuuntelu_asetukset: Kuuntelu
) -> str:
    """Yksi keskustelun vuoro. kuuntele=True: puhe <Gather>:in sisalla, jolloin vanhus voi
    keskeyttaa ja vastaus lahetetaan action_url:iin (myos tyhja, jotta hiljaisuus kasitellaan).
    kuuntele=False: puhe ja <Hangup/>.

    Tauko on aina sekunteja, ei "auto": auto lopettaa kuuntelun ensimmaiseen taukoon, mika
    katkaisee hitaasti puhuvan kesken lauseen (ja Twilio kieltaa auton, kun malli on valittu).
    """
    aani = kuuntelu_asetukset.aani
    vastaus = VoiceResponse()
    if not kuuntele:
        for teksti in sanottavat:
            vastaus.say(teksti, voice=aani)
        vastaus.hangup()
        return str(vastaus)

    lisat: dict[str, str] = {}
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
    for teksti in sanottavat:
        kuuntelu.say(teksti, voice=aani)
    vastaus.append(kuuntelu)
    return str(vastaus)


def virhe_twiml(aani: str) -> str:
    """Kun puhelun tilaa ei loydy (esim. backend kaynnistyi uudelleen kesken puhelun)."""
    vastaus = VoiceResponse()
    vastaus.say(TEKNINEN_VIKA, voice=aani)
    vastaus.hangup()
    return str(vastaus)
