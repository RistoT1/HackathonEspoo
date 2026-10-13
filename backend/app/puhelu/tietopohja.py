"""Puheavustajan tietopohja (Jere, puheavustaja/tietopohja.py): sääntöpohjainen tunnistus,
kiinteät repliikit, oireiden jatkokysymykset ja kotihoito-ohjeet sekä kiireellisyys.

Sisältö on versionhallituissa JSON-tiedostoissa (tieto/), ei koodissa eikä kannassa:
  keskustelusaannot.json  hätämerkit ja -viestit, vakavat huolet, huoli, lopetuspyyntö,
                          loukkaus, uudelleenkysymys, kiinteät repliikit (versiot arvotaan)
  oireohjeet.json         20 oiretta: tunnisteet, jatkokysymykset, kotihoito-ohjeet,
                          milloin hoitajaan yhteys, nostaako kiireellisyyttä

Kaikki on deterministista ja ajetaan jokaiselle puheenvuorolle ennen LLM:ää. Kielto
tulkitaan ("ei ole ollut rintakipua" ei ole hätä); hatasanat.py:n kiellotonta
tunnistusta käytetään sen rinnalla, joten hätä laukeaa, jos kumpi tahansa osuu.
Kotihoito-ohjeet ovat yleisiä omahoito-ohjeita, eivät lääkeneuvoja (oireohjeet.json).
TARKISTETTAVA hoitohenkilökunnan kanssa ennen tuotantoa. Vain suomi: Jeren ruotsin ja
englannin tekstit (kielet.json) jäivät pois, koska Twilio-puhelu on fi-FI.
"""

import json
import random
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_KANSIO = Path(__file__).with_name("tieto")

TASOT = ("vihreä", "keltainen", "punainen")


def _lue(nimi: str) -> dict:
    return json.loads((_KANSIO / nimi).read_text(encoding="utf-8"))


@lru_cache
def saannot() -> dict:
    return _lue("keskustelusaannot.json")


@lru_cache
def oireohjeet() -> dict:
    return _lue("oireohjeet.json")


@lru_cache
def oireet() -> dict[str, dict]:
    return {oire["oire"]: oire for oire in oireohjeet()["oireet"]}


# --- Kiinteät repliikit: versio arvotaan, ei samaa kahdesti peräkkäin ---

_ARPA = random.Random()


def valitse_versio(arvo: str | list[str], avain: str, edelliset: dict[str, str]) -> str:
    """Lista -> yksi versio, ei samaa kuin edellisellä kerralla samalla avaimella.
    edelliset kuuluu puheluistunnolle (vaihtelu on puhelukohtaista)."""
    if isinstance(arvo, str) or not arvo:
        return arvo or ""
    vaihtoehdot = [versio for versio in arvo if versio != edelliset.get(avain)] or list(arvo)
    valinta = _ARPA.choice(vaihtoehdot)
    edelliset[avain] = valinta
    return valinta


def teksti(avain: str, edelliset: dict[str, str]) -> str:
    """Kiinteä repliikki avaimella, esim. 'lopetuspyynto.lopetus' tai 'hataviestit.rintakipu'."""
    if avain == "varmistuskysymys":
        arvo = saannot()["kieltomuodossa_varmistettavat"]["varmistuskysymys"]
        return valitse_versio(arvo, avain, edelliset)
    arvo = saannot()
    for osa in avain.split("."):
        arvo = arvo[osa]
    return valitse_versio(arvo, avain, edelliset)


# --- Sääntöpohjainen tunnistus ---


def normalisoi(teksti_: str) -> str:
    """Pienet kirjaimet, yhdysmerkit pois ("rinta-kipu" -> "rintakipu"), välit siistiksi.
    Välimerkit säilyvät: niillä erotetaan lauseet kieltotarkistuksessa."""
    tulos = teksti_.lower().replace("’", "'")
    tulos = re.sub(r"(?<=\w)-(?=\w)", "", tulos)
    tulos = re.sub(r"\s+", " ", tulos)
    return tulos.strip()


def _kaanna(kuviot: list[str]) -> list[re.Pattern]:
    """Kuvio osuu vain sanan alkuun: "annoksen" ei saa osua kuvioon "oksen" (11.10.:
    "otin uuden annoksen" tunnistui pahoinvoinniksi ja nosti triagen keltaiseksi)."""
    return [
        re.compile(kuvio if kuvio.startswith(("^", "\\b")) else rf"\b(?:{kuvio})")
        for kuvio in kuviot
    ]


@lru_cache
def _kuviot() -> dict:
    s = saannot()
    return {
        "kielto": set(s["kieltosanat"]),
        "hata": {
            syy: _kaanna(kuviot)
            for syy, kuviot in s["hatamerkit"].items()
            if not syy.startswith("_")
        },
        "keltainen": _kaanna(s["hoitajalle_merkittavat"]["keltainen"]),
        "vakavat": {
            syy: _kaanna(tieto["tunnisteet"])
            for syy, tieto in s["vakavat_huolet"].items()
            if not syy.startswith("_")
        },
        "huoli": _kaanna(s["huoli"]["tunnisteet"]),
        "oireet": {nimi: _kaanna(tieto["tunnisteet"]) for nimi, tieto in oireet().items()},
        "loukkaus": _kaanna(s["loukkaus"]["tunnisteet"]),
        "uhkaus": _kaanna(s["loukkaus"]["uhkaus_tunnisteet"]),
        "lopetus": _kaanna(s["lopetuspyynto"]["tunnisteet"]),
        "toistopyynto": _kaanna(s["uudelleenkysymys"]["toistopyynnot"]),
    }


HATA_TUKITILAAN = frozenset(saannot().get("hata_jatka_tukitilassa", []))
VARMISTETTAVAT = frozenset(saannot()["kieltomuodossa_varmistettavat"]["merkit"])

# Selkokieliset nimet hoitajan yhteenvetoon ja lopetusviestiin.
SELKONIMET = {
    "itsetuhoisuus": "raskaita ajatuksia",
    "yliannostus": "liian suuri lääkemäärä",
    "kaltoinkohtelu": "huono kohtelu tai väkivalta",
    "aistiharhat": "outoja aistimuksia",
    "toivottomuus": "toivottomuutta",
}

_LAUSERAJA = re.compile(r"[,.;:!?]|\b(mutta|vaan|kuitenkin|nyt|paitsi|kun taas)\b")
_JALKIKIELTO = re.compile(
    r"^\w*\s+(ei|en) (ole|oo)(( enää| yhtään| ollenkaan)? "
    r"(ollut|ilmennyt|tuntunut|esiintynyt)\b|\s*([,.!?]|$))"
)


# Oireille ja huomiosanoille leveämpi kieltoikkuna: "en oo ollut sen väsyneempi" tunnistui
# väsymykseksi (12.10. läpikäynti). Hätämerkeillä 3 sanaa: leveämpi voisi peittää hädän.
OIREEN_KIELTOIKKUNA = 4


def _on_kielletty(teksti_: str, alku: int, ikkuna: int = 3) -> bool:
    """Kieltosana osuman edellä samassa lauseessa (enintään `ikkuna` sanaa): 'ei ole ollut
    rintakipua'. 'En tiedä, mutta rintaa puristaa' EI ole kielto, eikä 'ei ollut kuin
    vähän huimausta' (= vähän huimausta)."""
    ennen = teksti_[:alku]
    rajat = list(_LAUSERAJA.finditer(ennen))
    if rajat:
        ennen = ennen[rajat[-1].end() :]
    edelliset = re.findall(r"\w+", ennen)[-ikkuna:]
    kiellot = [i for i, sana in enumerate(edelliset) if sana in _kuviot()["kielto"]]
    if not kiellot:
        return False
    return "kuin" not in edelliset[kiellot[-1] :]


def _kielletty_jalkeen(teksti_: str, loppu: int) -> bool:
    """'Hengenahdistusta ei ole ollut': kielto osuman jälkeen samassa lauseessa."""
    return bool(_JALKIKIELTO.search(teksti_[loppu:]))


def _osuma_ilman_kieltoa(
    teksti_: str, kuviot: list[re.Pattern], ikkuna: int = 3
) -> re.Match | None:
    for kuvio in kuviot:
        for osuma in kuvio.finditer(teksti_):
            kielletty = _on_kielletty(teksti_, osuma.start(), ikkuna) or _kielletty_jalkeen(
                teksti_, osuma.end()
            )
            if not kielletty:
                return osuma
    return None


def _ensimmainen_osuma(teksti_: str, kuviot: list[re.Pattern]) -> re.Match | None:
    for kuvio in kuviot:
        osuma = kuvio.search(teksti_)
        if osuma:
            return osuma
    return None


@dataclass
class Tietopohjahavainto:
    hata: list[str] = field(default_factory=list)  # 112-tilanne
    hata_kielletty: list[str] = field(default_factory=list)  # mainittu kieltomuodossa
    vakavat: list[str] = field(default_factory=list)  # kaltoinkohtelu, aistiharhat, toivottomuus
    huoli: list[str] = field(default_factory=list)  # huoli, pelko, suru
    keltainen: list[str] = field(default_factory=list)  # nostaa vähintään keltaiseksi
    oireet: list[str] = field(default_factory=list)  # oireohjeet.json:n oireet
    huolestuttavat: list[str] = field(default_factory=list)  # oireista tasoa nostavat
    lopetuspyynto: bool = False
    toistopyynto: bool = False  # "mitä kysyit?", "toista"
    loukkaus: bool = False  # haukkuu avustajaa tai puhelua
    uhkaus: bool = False  # uhkailee avustajaa tai palvelua


def tarkista(teksti_: str) -> Tietopohjahavainto:
    """Ajetaan jokaiselle puheenvuorolle ENNEN LLM:ää. Ei riipu mallista."""
    normalisoitu = normalisoi(teksti_)
    kuviot = _kuviot()
    havainto = Tietopohjahavainto()
    for syy, hatakuviot in kuviot["hata"].items():
        if _osuma_ilman_kieltoa(normalisoitu, hatakuviot):
            havainto.hata.append(syy)
        elif _ensimmainen_osuma(normalisoitu, hatakuviot):
            havainto.hata_kielletty.append(syy)
    for syy, vakavat in kuviot["vakavat"].items():
        if _osuma_ilman_kieltoa(normalisoitu, vakavat):
            havainto.vakavat.append(syy)
    ilman_valimerkkeja = normalisoitu.strip(" .,!")
    havainto.lopetuspyynto = _osuma_ilman_kieltoa(ilman_valimerkkeja, kuviot["lopetus"]) is not None
    havainto.toistopyynto = any(k.search(ilman_valimerkkeja) for k in kuviot["toistopyynto"])
    havainto.uhkaus = _osuma_ilman_kieltoa(normalisoitu, kuviot["uhkaus"]) is not None
    loukkaa = _osuma_ilman_kieltoa(normalisoitu, kuviot["loukkaus"]) is not None
    havainto.loukkaus = havainto.uhkaus or loukkaa
    for kuvio in kuviot["huoli"]:
        osuma = kuvio.search(normalisoitu)
        uusi = osuma is not None and osuma.group(0) not in havainto.huoli
        if uusi and not _on_kielletty(normalisoitu, osuma.start()):
            havainto.huoli.append(osuma.group(0))
    for kuvio in kuviot["keltainen"]:
        osuma = kuvio.search(normalisoitu)
        if osuma and not _on_kielletty(normalisoitu, osuma.start(), OIREEN_KIELTOIKKUNA):
            havainto.keltainen.append(osuma.group(0))
    for nimi, oirekuviot in kuviot["oireet"].items():
        if _osuma_ilman_kieltoa(normalisoitu, oirekuviot, OIREEN_KIELTOIKKUNA):
            havainto.oireet.append(nimi)
            if oireet()[nimi]["huolestuttava"]:
                havainto.huolestuttavat.append(nimi)
    return havainto


_MYONTAVA = re.compile(
    r"\b(kyllä|joo|juu|jep|on ollut|on|ehkä|vähän|välillä|joskus|totta|niin on|olen|oon|"
    r"ajattelen|ajatellut)\b"
)
_KIELTAVA_ALKU = re.compile(r"^(ei|en|eipä|enpä|no ei|ei ole|ei oo|en ole|en oo)\b")


def on_myontava(teksti_: str) -> bool:
    """Vastaus itsetuhoisuuden varmistukseen. Epäselvä tai myöntävä -> True (turvallisempi)."""
    normalisoitu = normalisoi(teksti_).strip(" .,!?")
    epavarma = re.search(
        r"\b(en tiedä|en tiiä|en osaa sanoa|en ole varma|ehkä|vähän|välillä|joskus)\b",
        normalisoitu,
    )
    if epavarma:
        return True
    kieltava = _KIELTAVA_ALKU.search(normalisoitu) and not re.search(
        r"\b(mutta|vaikka)\b", normalisoitu
    )
    if kieltava:
        return False
    return bool(_MYONTAVA.search(normalisoitu)) or len(normalisoitu.split()) > 3


# --- Oireiden tieto puhelulle ja mallille ---


AJOITUS = "{muutoksen_jalkeen}"


def muutoksen_jalkeen(tyypit: list[str]) -> str:
    """Ajoituskysymyksen sanamuoto lääkemuutoksen tyypin mukaan ("lääkkeen lopettamisen
    jälkeen"). Useampi erityyppinen muutos tai tuntematon tyyppi -> yleinen muoto."""
    sanamuodot = oireohjeet()["muutoksen_jalkeen"]
    erilaiset = set(tyypit)
    if len(erilaiset) != 1:
        return sanamuodot["oletus"]
    return sanamuodot.get(erilaiset.pop(), sanamuodot["oletus"])


@lru_cache
def _ajoituskuviot() -> list[re.Pattern]:
    return _kaanna(oireohjeet()["ajoitus_kerrottu"])


def ajoitus_kerrottu(teksti_: str) -> bool:
    """Kertoiko henkilö jo, milloin oire alkoi ("sen jälkeen kun lopetin")."""
    normalisoitu = normalisoi(teksti_)
    return any(kuvio.search(normalisoitu) for kuvio in _ajoituskuviot())


def oireen_jatkokysymykset(
    oire: str, muutoksen_jalkeen: str | None = None, ajoitus_tiedossa: bool = False
) -> list[str]:
    """Tietopohjan jatkokysymykset. Ajoituskysymys täytetään muutoksen tyypillä ja jätetään
    pois, jos henkilö kertoi alkamisen jo (13.10. puhelu: kysyttiin uudelleen ja väärin)."""
    sanamuoto = muutoksen_jalkeen or oireohjeet()["muutoksen_jalkeen"]["oletus"]
    kysymykset = []
    for kysymys in oireet().get(oire, {}).get("lisakysymykset", []):
        ajoituskysymys = AJOITUS in kysymys
        if ajoituskysymys and ajoitus_tiedossa:
            continue
        kysymykset.append(kysymys.replace(AJOITUS, sanamuoto))
    return kysymykset


def kotihoito_ohje(oire: str) -> str:
    """Enintään kaksi kotihoito-ohjetta ja milloin hoitajaan otetaan yhteyttä. Kiinteä
    teksti oireohjeet.json:sta: malli ei keksi ohjeita."""
    tieto = oireet().get(oire)
    if tieto is None:
        return ""
    ohjeet = " ".join(tieto["kotihoito"][:2])
    return f"{ohjeet} Ota yhteyttä hoitajaan, {tieto['ota_yhteytta']}."


def oireohje_mallille(oire_lista: list[str]) -> str:
    """Mallille: tunnistetun oireen jatkokysymykset (tietopohjan, ei keksittyjä)."""
    osat = [
        f"oire '{nimi}': jatkokysymyksiä {' '.join(oireen_jatkokysymykset(nimi))}"
        for nimi in oire_lista
        if nimi in oireet()
    ]
    return "Tietopohja: henkilö mainitsi " + " | ".join(osat) if osat else ""


def jarkyttava_oire(oire_lista: list[str]) -> bool:
    """Kaatuminen, verenvuoto, sydämentykytys: vastaus alkaa huudahduksella."""
    jarkyttavat = set(saannot()["tunteet"]["jarkyttavat_oireet"])
    return any(oire in jarkyttavat for oire in oire_lista)


def huudahdus(kategoria: str, edelliset: dict[str, str], hata: bool = False) -> str:
    """Kiinteän repliikin alkuun: "Voi ei!". Itsetuhoisuudessa tyhjä (rauhallisuus)."""
    kiinteat = saannot()["tunteet"]["kiinteisiin"]
    valinta = kiinteat.get(kategoria) or (kiinteat["_oletus_hata"] if hata else {})
    return valitse_versio(valinta.get("fi", ""), f"huuto:{kategoria}", edelliset)


# --- Puhelun yhteenveto ja kiireellisyys ---


def huolet(havainnot: list[Tietopohjahavainto], epaselvia: int = 0) -> list[str]:
    """Puhelussa ilmenneet huolestuttavat asiat selkokielellä (lopetusviestiin)."""
    tulos: list[str] = []
    for havainto in havainnot:
        for syy in [*havainto.hata, *havainto.vakavat]:
            nimi = SELKONIMET.get(syy, syy.replace("_", " "))
            if nimi not in tulos:
                tulos.append(nimi)
        for oire in havainto.huolestuttavat:
            if oire not in tulos:
                tulos.append(oire)
    if not tulos and any(havainto.huoli for havainto in havainnot):
        tulos.append("huolta voinnista")
    if not tulos and any(havainto.keltainen for havainto in havainnot):
        tulos.append("vointiin liittyviä oireita")
    if epaselvia >= saannot()["uudelleenkysymys"]["epaselvat_nostaa_keltaiseksi"]:
        tulos.append("vastauksista oli vaikea saada selvää")
    return tulos


def kiireellisyys(havainnot: list[Tietopohjahavainto], epaselvia: int = 0) -> str:
    """Sääntöpohjainen alaraja tasolle. LLM tai hoitaja voi vain nostaa tätä.
    Toistuvasti epäselvät vastaukset nostavat keltaiseksi: syy voi olla yhteys tai
    sekavuus, ja sen arvioi hoitaja."""
    if any(havainto.hata or havainto.vakavat for havainto in havainnot):
        return "punainen"
    if epaselvia >= saannot()["uudelleenkysymys"]["epaselvat_nostaa_keltaiseksi"]:
        return "keltainen"
    # Kieltomuotoinen maininta ("ei ole rintakipua") kirjataan perusteluun, ei nosta tasoa.
    nostavat = any(
        havainto.keltainen or havainto.huolestuttavat or havainto.huoli or havainto.uhkaus
        for havainto in havainnot
    )
    if nostavat:
        return "keltainen"
    loukkauksia = sum(havainto.loukkaus for havainto in havainnot)
    if loukkauksia >= saannot()["loukkaus"]["nostaa_keltaiseksi"]:
        return "keltainen"
    return "vihreä"


def korkeampi_taso(*tasot: str) -> str:
    """Sääntö on alaraja: lopullinen taso on korkein (sääntö voi vain nostaa)."""
    kelvolliset = [taso for taso in tasot if taso in TASOT]
    return max(kelvolliset, key=TASOT.index) if kelvolliset else "keltainen"


# --- Tukitila: vakavan asian jälkeen kuunnellaan ja varmistetaan turvallisuus ---

_ITSETUHO_LISA = """
KOSKA HENKILÖ PUHUI ITSENSÄ VAHINGOITTAMISESTA:
- Sano lämpimästi omin sanoin, että toivot, ettei hän tee itselleen mitään, ja että hän
  on tärkeä. Älä väittele, älä moralisoi äläkä lupaa, että kaikki järjestyy.
- Kysy lempeästi, pystyykö hän pitämään itsensä turvassa siihen asti, kun hoitaja soittaa tänään.
- Jos lähellä on lääkkeitä tai muita vaarallisia välineitä, pyydä lempeästi laittamaan ne
  kauemmas tai antamaan ne jollekulle.
- Kysy, voisiko joku tulla hänen luokseen.
"""


def tukitilan_ohje(syy: str, itsetuho: bool) -> str:
    """Mallin ohje tukitilassa: lääkekysymykset pois, kuuntelu ja turvallisuus."""
    lisa = _ITSETUHO_LISA if itsetuho else ""
    return f"""Olet hyvinvointialueen tekoälyavustaja puhelimessa. Olet tekoäly, et ihminen.
Puhut suomea lämpimästi, rauhallisesti ja selkokielellä. Sinuttele.

TEHTÄVÄ NYT: TUKI
Henkilö kertoi juuri vakavasta asiasta ({syy}). Olet jo sanonut, että asia välitetään
hoitajalle tänään ja että vaaratilanteessa soitetaan hätänumeroon. Lääkekysymykset jäävät pois.

Tehtäväsi on kuunnella ja varmistaa turvallisuus:
- Reagoi lämpimästi siihen, mitä henkilö juuri sanoi. Älä vähättele, älä tuomitse,
  älä väittele, älä anna neuvoja äläkä kysele yksityiskohtia.
- Varmista lempeästi, onko hän nyt turvassa ja onko joku, joka voi olla hänen tukenaan.
- Jos hän on välittömässä vaarassa, kehota soittamaan hätänumeroon yksi yksi kaksi heti.
- Kerro tarvittaessa uudelleen, että hoitaja soittaa hänelle tänään.
- Yksi tai kaksi lyhyttä lausetta ja enintään yksi kysymys. Ei muita numeroita.
- Kun hän on kertonut olevansa turvassa tai haluaa lopettaa, kirjoita pelkkä sana LOPETUS.
{lisa}
Vastaus luetaan ääneen: ei markdownia, ei emojeja, ei lainausmerkkejä."""


# --- Keskustelu (Jere, puheavustaja.py): malli keskustelee vapaasti, säännöt reunoilla ---

LOPETUSMERKKI = "LOPETUS"  # malli sanoo tämän, kun kysymykset on käyty
UUDELLEENMERKKI = "UUDELLEEN"  # malli aloittaa tällä, kun kysyy saman kysymyksen uudelleen


@lru_cache
def puhelutyypit() -> dict:
    return _lue("puhelutyypit.json")


def ikaluokka(ika: int | None) -> str:
    """Ikäluokka iästä (puhelutyypit.json); tuntematon -> ikäihminen."""
    if ika is None:
        return "ikaihminen"
    for nimi, luokka in puhelutyypit()["ikaluokat"].items():
        if nimi.startswith("_"):
            continue
        alku, loppu = luokka["ikavali"]
        if alku <= ika <= loppu:
            return nimi
    return "ikaihminen"


# Jeren järjestelmäohje SANATARKASTI (puheavustaja/puheavustaja.py: SYSTEM_PROMPT), ainoa
# lisäys sinuttelusääntö. Puhelinnumerot luetaan numero kerrallaan (puhe.puheeksi).
JARJESTELMAOHJE = """Olet hyvinvointialueen hoitotiimin suomenkielinen tekoälyavustaja, joka soittaa
puhelun. {persoonallisuus} Olet tekoäly, et ihminen, etkä koskaan väitä muuta.

PUHUTTELU: käytä AINA sinä-muotoa (sinä, sinulla, sinun, voit, olet). Älä koskaan teitittele
(ei "te", "teillä", "olette", "voitte").

IHMINEN ENSIN, KYSYMYKSET SITTEN:
- Kuuntele, mitä henkilö oikeasti sanoi, ja vastaa siihen ensin. Kysymyslista ei ole kiireellinen.
- Kun henkilö kertoo kivusta, huolesta, pelosta, surusta, yksinäisyydestä tai muusta
  vaikeasta, PYSÄHDY siihen. Nimeä tunne hänen omin sanoin ja osoita, että ymmärsit.
  Kysy siitä lisää lempeästi. ÄLÄ siirry seuraavaan kysymykseen samassa vastauksessa.
- Siirry eteenpäin vasta, kun hän on saanut kertoa asiansa. Voit kysyä:
  "Haluatko kertoa tästä vielä, vai jatketaanko?"
- Älä vähättele ("ei se mitään", "kyllä se siitä"), älä tuomitse äläkä neuvo liikaa.
- Jos henkilö haukkuu tai uhkailee sinua, älä loukkaannu, älä puolustele äläkä moralisoi.
  Vastaa rauhallisesti, nimeä tunne lyhyesti ja kysy, onko kaikki hyvin tai voitko auttaa
  jossain asiassa. Vihaisuuden takana voi olla kipu, pelko tai väsymys.
- Kiitä kertomisesta, kun asia on henkilölle selvästi vaikea.
- Vaihtele sanojasi. Älä aloita joka vastausta samalla fraasilla.

TUNTEET JA HUUDAHDUKSET:
- Kun henkilö kertoo yllättävästä tai järkyttävästä asiasta (kaatuminen, kova kipu,
  onnettomuus, huono uutinen, joku on kohdellut häntä huonosti), aloita vastaus lyhyellä
  aidolla huudahduksella, esimerkiksi "Oho!", "Voi ei!" tai "Voi että!". Jatka sitten
  myötäelämällä ja kysy lisää.
- Iloiseen uutiseen sopii esimerkiksi "Oho, mukavaa!".
- Älä käytä huudahdusta arkisiin asioihin äläkä joka vastauksessa.
- Kun henkilö kertoo itsetuhoisista ajatuksista tai on hyvin surullinen, puhu rauhallisesti
  ilman huudahdusta.
Esimerkki:
Henkilö: "Kaaduin eilen kylpyhuoneessa."
Hyvä: "Oho! Satutitko itsesi?"
Huono: "Selvä. Onko sinulla ollut pahoinvointia?"

Esimerkkejä:
Henkilö: "Olen vähän huolissani, kun tämä lääke tuntuu oudolta."
Hyvä: "Ymmärrän, että se huolettaa. Kerrotko, miltä se tuntuu?"
Huono: "Selvä. Onko sinulla ollut pahoinvointia?"
Henkilö: "Vaimo kuoli keväällä, ja on ollut aika yksinäistä."
Hyvä: "Olen todella pahoillani. Se on iso menetys. Onko sinulla ketään, jonka kanssa jutella?"
Huono: "Otan osaa. Oletko ottanut lääkkeen ohjeen mukaan?"
Henkilö: "Ihan hyvin menee."
Hyvä: "Mukava kuulla. Mitä olet tänään puuhaillut?"

Säännöt:
- Vastaa puhelun kielellä (oletus suomi; jos alla on PUHELUN KIELI, käytä sitä):
  yleensä 1–3 lyhyttä lausetta, ohjeita antaessasi enintään 4.
- Vastauksesi luetaan ääneen. Älä käytä markdownia, luettelomerkkejä,
  otsikoita, emojeja, taulukoita tai koodia. Kirjoita numerot ja lyhenteet
  niin kuin ne sanotaan ääneen.
- Käyttäjän teksti tulee puheentunnistuksesta ja voi sisältää virheitä.
  Päättele tarkoitus asiayhteydestä. Jos et ymmärrä, kysy lyhyesti tarkennusta.
- Muista aiemmin sanottu. Älä aloita uusia aiheita omatoimisesti.
- Jos et tiedä vastausta, sano se suoraan äläkä keksi tietoja.
- Älä koskaan keksi puhelinnumeroita. Saat mainita vain numerot 112 ja 116117.
- Et tee diagnooseja etkä anna lääkeneuvoja (annokset, lääkkeen lopetus tai aloitus).
  Saat antaa vain tietopohjan kotihoito-ohjeita. Muuhun neuvoa kysyttäessä sano:
  "Välitän tämän hoitajalle."
- Jos henkilö mainitsee rintakivun, hengenahdistuksen tai halvausoireita, kehota
  soittamaan heti 112.
- Viestin lopussa suluissa oleva "Ohje" tai "Tietopohja" on sinulle tarkoitettu
  piilotettu ohje. Noudata sitä, mutta älä lue sitä ääneen.
"""

# Faktat henkilön tavallisiin kysymyksiin (lisätty Jeren ohjeeseen: mallin ei pidä keksiä).
_FAKTAT = """FAKTAT (vastaa näillä, kun henkilö kysyy; älä keksi muuta):
- Soittaja on hyvinvointialueen tekoälyavustaja, tietokoneohjelma, ei ihminen.
- Syy soittoon: henkilön lääkitykseen on tullut muutos, ja haluamme kuulla, miten hän voi.
- Lääkkeen nimeä ei kerrota puhelimessa tietosuojan vuoksi. Nimen näkee reseptistä tai
  apteekista, tai hoitaja voi kertoa sen.
- Puhelua ei äänitetä. Hoitajalle välitetään lyhyt yhteenveto. Hoitaja soittaa tarvittaessa."""


def jarjestelmaohje(ika: int | None, muutokset: list[str], kysymykset: list[str]) -> str:
    """Jeren tekstit sanatarkasti, mutta puhelukohtainen osa (ikäluokan puhetapa,
    lääkemuutos, kysymykset) LOPUSSA: Ollama käyttää välimuistia vain samaan alkuun asti.
    Mitattu 11.10. Gemma 12B: ikäluokka alussa -> eri ikäisen puhelun lämmitys 47,6 s
    (välimuistista 4/2818 tokenia); sama alku -> 5,6 s."""
    return f"{_yhteinen_ohje()}\n\n{_puhelun_tiedot(ika, muutokset, kysymykset)}"


@lru_cache
def _yhteinen_ohje() -> str:
    tyypit = puhelutyypit()
    persoonallisuus = tyypit["persoonallisuudet"][tyypit["oletukset"]["persoonallisuus"]]["fi"]
    perus = JARJESTELMAOHJE.replace("{persoonallisuus}", persoonallisuus)
    return f"{perus}\n{_FAKTAT}\n\n{puhelun_tehtava()}"


def _puhelun_tiedot(ika: int | None, muutokset: list[str], kysymykset: list[str]) -> str:
    """Ei nimeä, ei lääkkeen nimeä, ei diagnooseja (datan minimointi)."""
    ikaohje = puhelutyypit()["ikaluokat"][ikaluokka(ika)]["fi"]
    muutos = "; ".join(muutokset) or "lääkitykseen on tullut muutos"
    laakekysymykset = "\n".join(
        f"- {kysymys}" for kysymys in [*kysymykset, saannot()["yleiskysymykset"][1]]
    )
    return f"""TÄMÄN PUHELUN TIEDOT
{ikaohje}

Lääkemuutos: {muutos}.

LÄÄKKEESEEN LIITTYVÄT KYSYMYKSET:
{laakekysymykset}"""


def puhelun_tehtava() -> str:
    """Jeren tehtävänanto sanatarkasti (puheavustaja/tietopohja.py: puhelun_tehtava);
    lääkemuutos ja kysymykset ovat ohjeen lopussa (TÄMÄN PUHELUN TIEDOT)."""
    kielletyt = "\n".join(f"- {k}" for k in saannot()["kielletyt_aiheet"])
    yleiset = " ".join(oireohjeet()["yleiset_ohjeet"])
    return f"""PUHELUN TEHTÄVÄ
Soitat kuulumispuhelun henkilölle, jonka lääkitykseen on tullut muutos (tiedot lopussa).
Olet jo esitellyt itsesi tekoälyksi, kertonut soittavasi lääkemuutoksen takia ja kysynyt,
miten hänellä menee. Ota lääkekysymykset puheeksi luontevasti, kun kuulumiset on kuultu.
Puhuttele sinuttelemalla, lämpimästi ja kiireettömästi.

KESKUSTELE, ÄLÄ KUULUSTELE:
- Reagoi AINA ensin siihen, mitä henkilö juuri sanoi. Myötäelä lyhyesti ja kysy siihen
  liittyvä luonteva jatkokysymys hänen omasta kokemuksestaan: milloin, missä tilanteissa,
  kuinka paljon se haittaa arkea.
- Kun henkilön kertoma asia on käsitelty, kysy välillä yksi lopussa olevista lääkkeeseen
  liittyvistä kysymyksistä. Käy ne läpi puhelun aikana, mutta ei kaikkia peräkkäin.
- Yksi kysymys kerrallaan.

OHJEET OIREISIIN:
- Jos viestin lopussa on suluissa "Tietopohja", se kertoo tunnistetun oireen, siihen sopivat
  jatkokysymykset ja kotihoito-ohjeet. Kysy ensin yksi jatkokysymys. Kun tiedät tarpeeksi,
  kerro lyhyesti yksi tai kaksi tietopohjan kotihoito-ohjetta omin sanoin ja milloin
  hoitajaan kannattaa olla yhteydessä.
- Anna VAIN tietopohjan ohjeita. Älä keksi omia ohjeita, älä suosittele lääkkeitä, älä neuvo
  annoksista äläkä arvaa oireen syytä. {yleiset}

EPÄSELVÄT VASTAUKSET:
Jos vastaus ei liity kysymykseen, on ristiriitainen tai siitä ei saa selvää, ÄLÄ arvaa
vastausta. Aloita silloin vastauksesi sanalla {UUDELLEENMERKKI} ja kysy sama kysymys uudelleen
lyhyemmin ja yksinkertaisemmin, esimerkiksi niin että siihen voi vastata kyllä tai ei.
Jos viestin lopussa on suluissa ohje siirtyä eteenpäin, noudata sitä äläkä kysy uudelleen.

LOPETUS:
Kun lääkekysymykset on käyty ja henkilön kertomat asiat käsitelty, sano tasan sana
{LOPETUSMERKKI} ja ei mitään muuta.

RAJAT:
{kielletyt}
{saannot()["kun_henkilo_itse_kertoo"]}
"""


_PIILO = {
    "siirry": "Tähän ei saatu selvää vastausta. Siirry seuraavaan kysymykseen.",
    "ei_uudelleen": "Tätä on jo kysytty uudelleen. Älä kysy enää, siirry seuraavaan kysymykseen.",
    "lopettelu": "Puhelu on lopuillaan. Vastaa lyhyesti siihen, mitä henkilö sanoi, ja kysy, "
    "onko vielä jotain tärkeää, mitä hän haluaa kertoa. Älä aloita uusia aiheita.",
    "vaihtele": "Olet aloittanut aiemmat vastauksesi näin: {aloitukset}. Aloita tämä vastaus eri "
    "sanoilla, mutta pysy yhtä lämpimänä ja lyhyenä.",
    "jarkytys": "Tämä on säikäyttävä asia kuulla. Aloita vastaus lyhyellä huudahduksella, "
    "kuten Oho! tai Voi ei!, ja jatka myötäelämällä ja kysymällä, miten hän voi.",
    "laakeneuvo": "Henkilö kysyi lääkeneuvoa, ja siihen on jo vastattu, että hoitaja vastaa. "
    "Älä neuvo. Jatka keskustelua.",
}


def piilo_ohje(avain: str, **taytot: str) -> str:
    """Vuorokohtainen piilo-ohje mallille muodossa "\\n(Ohje: ...)"."""
    if avain == "huoli":
        teksti_ = saannot()["huoli"]["ohje_mallille"]
    elif avain == "huoli_jatko":
        teksti_ = saannot()["huoli"]["jatko_ohje"]
    else:
        teksti_ = _PIILO.get(avain, "")
    if teksti_ and taytot:
        teksti_ = teksti_.format(**taytot)
    return f"\n(Ohje: {teksti_})" if teksti_ else ""


def oireohje_kotihoidolla(
    oire_lista: list[str], muutoksen_jalkeen: str | None = None, henkilon_teksti: str = ""
) -> str:
    """Piilotettu vihje mallille: oireen jatkokysymykset, kotihoito-ohjeet ja milloin
    hoitajaan yhteys (Jere: tietopohja.oireohje_mallille). Yksi rivi ilman sulkeita:
    keskustelu poimii sen historiaan ja siisti_vastaus poistaa sen sulkeisiin asti."""
    ajoitus_tiedossa = ajoitus_kerrottu(henkilon_teksti)
    osat = []
    for nimi in oire_lista:
        tieto = oireet().get(nimi)
        if tieto is None:
            continue
        kysymykset = oireen_jatkokysymykset(nimi, muutoksen_jalkeen, ajoitus_tiedossa)
        osat.append(
            f"oire '{nimi}'. Jatkokysymyksiä: {' '.join(kysymykset)} "
            f"Kotihoito-ohjeet: {' '.join(tieto['kotihoito'])} "
            f"Hoitajaan yhteys: {tieto['ota_yhteytta']}."
        )
    if not osat:
        return ""
    # 13.10. puhelu: henkilö kertoi päänsäryn alkaneen lopettamisen jälkeen, ja malli kysyi
    # silti, alkoiko se lääkkeen aloittamisen jälkeen.
    loppu = " Älä kysy uudelleen sitä, minkä henkilö jo kertoi."
    return "\n(Tietopohja: henkilö mainitsi " + " | ".join(osat) + loppu + ")"


def _vartalot(teksti: str) -> set[str]:
    return {sana[:5] for sana in re.findall(r"[a-zåäö]+", teksti.lower()) if len(sana) >= 5}


def sisaltaa_kotihoidon(teksti: str, oire_lista: list[str]) -> bool:
    """Tekstissä on jonkin oireen kotihoito-ohje omin sanoin: vähintään kolme ohjeen
    sisältösanaa (lyhyessä ohjeessa kaikki)."""
    sanat = _vartalot(teksti)
    for nimi in oire_lista:
        for ohje in oireet().get(nimi, {}).get("kotihoito", []):
            ohjeen_sanat = _vartalot(ohje)
            tarve = min(3, len(ohjeen_sanat))
            if ohjeen_sanat and len(ohjeen_sanat & sanat) >= tarve:
                return True
    return False


def kotihoito_puheeksi(oire_lista: list[str]) -> str:
    """Jeren kotihoito-ohjeet sellaisinaan: kaksi ensimmäistä ohjetta enintään kahdesta
    oireesta ja milloin hoitajaan kannattaa olla yhteydessä."""
    tiedot = [oireet()[nimi] for nimi in oire_lista if nimi in oireet()][:2]
    if not tiedot:
        return ""
    lauseet = [ohje for tieto in tiedot for ohje in tieto["kotihoito"][:2]]
    lauseet.append(f"Ota yhteyttä hoitajaan, {tiedot[0]['ota_yhteytta']}.")
    return " ".join(lauseet)


def puhelun_vuororaja(
    havainnot: list[Tietopohjahavainto],
    perusraja: int,
    vuoro: int,
    tukitila: bool = False,
    itsetuho: bool = False,
) -> int | None:
    """Montako vuoroa puhelu saa kestää. None = ei rajaa (itsetuhoinen puhe). Huoli ja
    vakavat asiat pidentävät; huolta viimeisissä vuoroissa ei katkaista kesken."""
    kesto = saannot()["puhelun_kesto"]
    if itsetuho and kesto["itsetuho_ei_rajaa"]:
        return None
    raja = perusraja
    if any(h.huoli or h.huolestuttavat or h.uhkaus for h in havainnot):
        raja += kesto["huoli_lisavuorot"]
    if tukitila or any(h.vakavat or h.hata for h in havainnot):
        raja += kesto["vakava_lisavuorot"]
    viime = havainnot[-kesto["viime_vuorot"] :] if havainnot else []
    huolta_nyt = any(
        h.huoli or h.vakavat or h.hata or h.hata_kielletty or h.huolestuttavat for h in viime
    )
    if huolta_nyt:
        raja = max(raja, vuoro + 1)
    return min(raja, max(kesto["ehdoton_ylaraja"], perusraja))


@lru_cache
def _poikkeamakuviot() -> dict[str, list[re.Pattern]]:
    kuviot = saannot()["laakemuutoksen_poikkeama"]["kuviot"]
    return {tunniste: _kaanna(lista) for tunniste, lista in kuviot.items()}


def laakemuutoksen_poikkeama(tunniste: str, teksti_: str) -> str | None:
    """Peruste, jos vastaus juuri kysyttyyn muutoskysymykseen kertoo, ettei muutosta
    noudateta ("jatkanut" lopetettua lääkettä). Muut vastaukset eivät kelpaa."""
    pienena = teksti_.lower()
    if any(kuvio.search(pienena) for kuvio in _poikkeamakuviot().get(tunniste, [])):
        return saannot()["laakemuutoksen_poikkeama"]["peruste"]
    return None


def arvioi_vastaus(teksti_: str, varmuus: float | None, varmuusraja: float) -> str:
    """'selvä', 'toistopyyntö' tai 'epäselvä'. Ei mallia. Puheentunnistuksen laatu on
    Twilion varmuus (Jerellä Whisperin luottamusarvot)."""
    pienena = teksti_.lower().strip(" .,!")
    if any(kuvio.search(pienena) for kuvio in _kuviot()["toistopyynto"]):
        return "toistopyyntö"
    harhat = saannot()["uudelleenkysymys"]["puheentunnistuksen_harhat"]
    if not re.search(r"[a-zåäö]", pienena) or any(harha in pienena for harha in harhat):
        return "epäselvä"
    if varmuus is not None and varmuus < varmuusraja and len(pienena.split()) > 1:
        return "epäselvä"
    return "selvä"


def viimeisin_kysymys(historia: list[dict[str, str]]) -> str:
    """Avustajan viimeisin kysymyslause (toistoa varten)."""
    for viesti in reversed(historia):
        if viesti["role"] != "assistant":
            continue
        kysymykset = re.findall(r"[^.!?]*\?", viesti["content"])
        return kysymykset[-1].strip() if kysymykset else viesti["content"].strip()
    return ""


_PUHELINNUMERO = re.compile(r"\d[\d \-]{2,}\d")
NUMERON_KORVAUS = "Välitän tämän hoitajalle."


@lru_cache
def sallitut_numerot() -> frozenset[str]:
    """Numerot, jotka avustaja saa sanoa: vain kiinteiden tekstien numerot (112, 116117,
    kriisipuhelin...). Malli ei saa keksiä numeroita (Jere: malli keksi "992-200")."""
    numerot: set[str] = {"112", "116117"}

    def kay(arvo: object) -> None:
        if isinstance(arvo, str):
            for numero in re.findall(r"\d[\d ]{1,}\d", arvo):
                numerot.add(re.sub(r"\D", "", numero))
        elif isinstance(arvo, dict):
            for alkio in arvo.values():
                kay(alkio)
        elif isinstance(arvo, list):
            for alkio in arvo:
                kay(alkio)

    kay(saannot())
    return frozenset(numerot)


_LAUSEEN_LOPPU = re.compile(r"(?<=[.!?…])\s+")


def siisti_vastaus(teksti_: str) -> str:
    """Mallin vastaus ääneen: merkinnät ja piilo-ohjeet pois, keksityt numerot korvataan."""
    puhdas = re.sub(r"[*_#`>|\"]", "", teksti_)
    puhdas = re.sub(r"^\s*[-•]\s*", "", puhdas, flags=re.MULTILINE)
    puhdas = re.sub(r"\((Ohje|Tietopohja):[^)]*\)?", "", puhdas)
    tulos: list[str] = []
    for lause in _LAUSEEN_LOPPU.split(" ".join(puhdas.split())):
        keksitty = any(
            re.sub(r"\D", "", numero) not in sallitut_numerot()
            for numero in _PUHELINNUMERO.findall(lause)
        )
        lause = NUMERON_KORVAUS if keksitty else lause
        if lause and not (lause == NUMERON_KORVAUS and NUMERON_KORVAUS in tulos):
            tulos.append(lause)
    return " ".join(tulos).strip()


ITSETUHOKYSYMYS = re.compile(
    r"(vahingoittaa|satuttaa) itseäsi|ajatuksia (kuolla|kuolemasta|itsemurhasta)", re.IGNORECASE
)


def aloitus(teksti_: str) -> str:
    """Vastauksen kolme ensimmäistä sanaa (toistuvien aloitusten tunnistamiseen)."""
    return " ".join(re.findall(r"[\wåäöÅÄÖ']+", teksti_.lower())[:3])
