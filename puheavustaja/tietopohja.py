"""
Puheavustajan tietopohja: soittoprofiilit, keskustelusäännöt ja sääntöpohjainen
hätä- ja hälytystunnistus.

Periaate (CLAUDE.md): deterministinen logiikka erillään LLM:stä.
 - Mitä kysytään       -> soittoprofiili (rakennettu datasta, rakenna_tietopohja.py)
 - Oireet ja ohjeet     -> oireohjeet.json: säännöt tunnistavat oireen, malli saa siihen
                           valmiit jatkokysymykset ja kotihoito-ohjeet (ei keksi omia)
 - Hätämerkit / taso    -> säännöt täällä, ajetaan JOKAISELLE puheenvuorolle ennen LLM:ää
 - Vakavat huolet       -> kaltoinkohtelu, aistiharhat, toivottomuus: kiinteä empaattinen
                           repliikki + tukitila (ei lääkekysymyksiä), punainen taso
 - Huoli / pelko        -> mallille vahva ohje pysähtyä asian äärelle, ei seuraavaa kysymystä
 - Lopetuspyynnöt       -> säännöt täällä (1. kysy syy, 2. lopeta jos ei huolta, 3. lopeta aina)
 - LLM                  -> luonteva keskustelu, jatkokysymykset ja ohjeiden sanoittaminen

Mallille EI lähetetä nimeä, henkilötunnusta, diagnooseja eikä potilastunnusta.
"""

import json
import re
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from pathlib import Path

KNOWLEDGE = Path(__file__).resolve().parent / "knowledge"


def _lue_json(nimi: str) -> dict:
    with open(KNOWLEDGE / nimi, encoding="utf-8") as f:
        return json.load(f)


SAANNOT = _lue_json("keskustelusaannot.json")
OIREOHJEET = _lue_json("oireohjeet.json")
_OIREET = {o["oire"]: o for o in OIREOHJEET["oireet"]}
KIELET = _lue_json("kielet.json")
MUUT_KIELET = [k for k in KIELET["tuetut"] if k != "fi"]


def _muut(*polku):
    """Kerää englannin ja ruotsin tunnisteet samasta kohdasta (lista tai sanakirja)."""
    tulos = []
    for kieli in MUUT_KIELET:
        x = KIELET[kieli]["tunnisteet"]
        for avain in polku:
            x = x.get(avain, {}) if isinstance(x, dict) else {}
        tulos.append(x)
    return tulos


def teksti(avain: str, kieli: str = "fi") -> str:
    """Kiinteä repliikki puhelun kielellä. avain kuten 'lopetuspyynto.lopetus'.
    Suomi luetaan keskustelusaannot.json:sta, muut kielet kielet.json:sta.
    Jos käännös puuttuu, käytetään suomea (parempi kuin hiljaisuus)."""
    if kieli != "fi":
        kaannos = KIELET.get(kieli, {}).get("tekstit", {}).get(avain)
        if kaannos:
            return kaannos
    if avain == "varmistuskysymys":
        return SAANNOT["kieltomuodossa_varmistettavat"]["varmistuskysymys"]
    x = SAANNOT
    for osa in avain.split("."):
        x = x[osa]
    return x


_PIILO_FI = {
    "huoli": SAANNOT["huoli"]["ohje_mallille"],
    "huoli_jatko": SAANNOT["huoli"]["jatko_ohje"],
    "siirry": "Tähän ei saatu selvää vastausta. Siirry seuraavaan kysymykseen.",
    "ei_uudelleen": "Tätä on jo kysytty uudelleen. Älä kysy enää, siirry seuraavaan kysymykseen.",
    "kieli_lipsahti": "Edellinen vastauksesi ei ollut suomeksi. Vastaa vain suomeksi.",
    "muistutus": "",
}


def piilo_ohje(avain: str, kieli: str = "fi") -> str:
    """Vuorokohtainen piilo-ohje mallille puhelun kielellä, muodossa "\n(Ohje: ...)".
    Tyhjä merkkijono, jos ohjetta ei ole (esim. suomen muistutus)."""
    teksti_ = (KIELET["mallin_ohjeet"][kieli]["piilo"].get(avain) if kieli != "fi"
               else _PIILO_FI.get(avain))
    return f"\n(Ohje: {teksti_})" if teksti_ else ""


_KIELEN_SANAT = {k: [re.compile(p) for p in KIELET[k]["tunnisteet"]["kielen_tunnistus"]]
                 for k in MUUT_KIELET}


_SUOMEN_SANAT = re.compile(
    r"\b(minä|mä|mulla|minulla|on|ei|en|ja|kiitos|hyvin|olen|oon|se|että|mutta|joo|kyllä|nyt|vähän|"
    r"ollut|ole|oli|tää|tämä|mitä|miten|kun|niin|sitten|myös|vielä|aika|ihan|tosi|vaan|jos|"
    r"hyvä|olet|sinulla|sinä|sinun|kuulla|voinut|onko|oletko|kerro|kerrotko|ymmärrän|hoitajalle)\b")
_LYHYET_VASTAUKSET = re.compile(
    r"^(kyllä|joo|juu|ei|en|ok|okei|niin|jaa|yes|no|yeah|ja|nej|nja|mm|hmm|kiitos|tack|thanks)$")


def kielipisteet(teksti_: str) -> dict:
    """Montako tunnusomaista sanaa kustakin kielestä tekstissä on."""
    t = normalisoi(teksti_)
    pisteet = {k: sum(len(p.findall(t)) for p in kuviot) for k, kuviot in _KIELEN_SANAT.items()}
    pisteet["fi"] = len(_SUOMEN_SANAT.findall(t))
    return pisteet


def tunnista_kieli(teksti_: str, nykyinen: str = "fi") -> str:
    """Kevyt tekstipohjainen kielen arvaus yhdestä tekstistä. Palauttaa nykyisen kielen,
    jos mikään kieli ei erotu selvästi (vähintään kaksi sanaa ja enemmän kuin muilla)."""
    pisteet = kielipisteet(teksti_)
    paras = max(pisteet, key=pisteet.get)
    muut = max(v for k, v in pisteet.items() if k != paras)
    if pisteet[paras] >= 2 and pisteet[paras] > muut:
        return paras
    return nykyinen


class Kielenvalitsin:
    """Pitää puhelun kielen vakaana. Kieli vaihtuu vain, kun
    - teksti on selvästi toista kieltä (vähintään 'varma_sanamaara' sanaa eikä yhtään
      nykyisen kielen sanaa), TAI
    - kaksi peräkkäistä puheenvuoroa viittaa samaan uuteen kieleen.
    Lyhyet vastaukset ("kyllä", "ok", "yes", "ja") eivät koskaan vaihda kieltä.
    Whisperin arvaus otetaan huomioon vain, kun se on hyvin varma ja teksti ei kiistä sitä."""

    def __init__(self, kieli: str = "fi"):
        self.kieli = kieli
        self.ehdokas: str | None = None
        self.perakkain = 0
        a = KIELET["kielenvaihto"]
        self.varma = a["varma_sanamaara"]
        self.tarvitaan = a["perakkaiset_vuorot"]
        self.min_sanoja = a["min_sanoja"]

    def paivita(self, teksti_: str, whisper_kieli: str | None = None, whisper_varmuus: float = 0.0) -> str:
        t = normalisoi(teksti_).strip(" .,!?")
        sanoja = len(re.findall(r"\w+", t))
        if sanoja < self.min_sanoja or _LYHYET_VASTAUKSET.match(t):
            return self.kieli
        pisteet = kielipisteet(t)
        arvio = tunnista_kieli(t, nykyinen="")
        if not arvio and whisper_kieli in KIELET["tuetut"] and whisper_varmuus >= 0.9:
            arvio = whisper_kieli
        if not arvio or arvio == self.kieli:
            self.ehdokas, self.perakkain = None, 0
            return self.kieli
        self.perakkain = self.perakkain + 1 if arvio == self.ehdokas else 1
        self.ehdokas = arvio
        selva = pisteet.get(arvio, 0) >= self.varma and pisteet.get(self.kieli, 0) == 0
        if selva or self.perakkain >= self.tarvitaan:
            self.kieli, self.ehdokas, self.perakkain = arvio, None, 0
        return self.kieli


# ---------------------------------------------------------------- PROFIILIT
def lataa_profiilit() -> list[dict]:
    polku = KNOWLEDGE / "soittoprofiilit.json"
    if not polku.exists():
        raise FileNotFoundError(
            "knowledge/soittoprofiilit.json puuttuu. Aja ensin: python rakenna_tietopohja.py"
        )
    with open(polku, encoding="utf-8") as f:
        return json.load(f)["profiilit"]


def hae_profiili(tunnus: str | None) -> dict:
    """Palauttaa profiilin tunnuksella (tai tunnuksen alulla). Ilman tunnusta: uusin lääkealoitus."""
    profiilit = lataa_profiilit()
    if not profiilit:
        raise ValueError("Soittoprofiileja ei löytynyt. Kasvata --paivia rakenna_tietopohja.py:ssä.")
    if tunnus is None:
        return max(profiilit, key=lambda p: p["uudet_laakkeet"][0]["aloitettu"])
    osumat = [p for p in profiilit if p["tunnus"].startswith(tunnus)]
    if len(osumat) != 1:
        raise ValueError(f"Tunnuksella '{tunnus}' löytyi {len(osumat)} profiilia, tarvitaan tasan yksi.")
    return osumat[0]


# ---------------------------------------------------------------- PUHEENTUNNISTUKSEN SANASTO
def tunnistussanasto(profiili: dict) -> str:
    """Vihjesanat Whisperille: lääkkeen selkonimi ja sen seurattavat oireet.

    Koko oirelistaa EI anneta: Whisper syöttää vihjesanat mallin kehotteeseen, ja
    hiljaisuuden tai huonon äänen kohdalla se "litteroi" ne sellaisenaan. Silloin
    kaikki 18 oiretta tunnistuivat yhdestä vastauksesta (puhelu 20261008_102309).
    """
    sanat: list[str] = []
    for laake in profiili["uudet_laakkeet"][:1]:
        sanat.append(laake["selkonimi"])
        sanat.extend(laake["seurattavat_oireet"][:4])
    return ", ".join(dict.fromkeys(sanat))


_SANASTON_SANAT = sorted({
    s.lower() for o in _OIREET for s in [o, *o.split(" ja ")]
}, key=len, reverse=True)


def on_kaikuharha(teksti: str, kysymys: str = "", sanasto: str = "") -> bool:
    """Tunnistaa Whisperin "kaiun": litteroinnissa on vihjesanalista tai avustajan oma
    kysymys sellaisenaan, ei henkilön puhetta. Tällainen vastaus käsitellään epäselvänä,
    eikä siitä poimita oireita."""
    t = normalisoi(teksti).strip(" .,!?")
    if not t:
        return False
    # Pilkuilla eroteltu luettelo, jossa monta oire- tai sanastosanaa peräkkäin.
    osat = [o.strip() for o in re.split(r"[,;]", t) if o.strip()]
    sanaston = {x.strip().lower() for x in sanasto.split(",") if x.strip()} | set(_SANASTON_SANAT)
    if len(osat) >= 3 and sum(o in sanaston for o in osat) >= 3:
        return True
    eri_oireita = sum(1 for nimi, kuviot in _OIRETUNNISTEET.items() if any(k.search(t) for k in kuviot))
    if eri_oireita >= 6:
        return True
    # Avustajan kysymys toistuu (lähes) sellaisenaan.
    if kysymys:
        k = normalisoi(kysymys).strip(" .,!?")
        if k and SequenceMatcher(None, t, k).ratio() > 0.85:
            return True
    return False


# ---------------------------------------------------------------- JÄRJESTELMÄKEHOTE
def _laakkeen_kuvaus(laake: dict) -> str:
    nimi = laake["selkonimi"] if laake["kerro_kayttotarkoitus"] else "uusi lääke"
    return f"{nimi}, aloitettu noin {laake['viikkoa_sitten']} viikkoa sitten"


def puhelun_tehtava(profiili: dict, kieli: str = "fi") -> str:
    """Muodostaa mallille tehtävänannon. Sisältää vain kysymykset, ei tunnistetietoja.
    Ruotsiksi ja englanniksi koko kehys on sillä kielellä (kielet.json: mallin_ohjeet)."""
    laake = profiili["uudet_laakkeet"][0]   # yksi lääke per puhelu: lyhyt ja selkeä
    laakekysymykset = "\n".join(f"- {k}" for k in [*laake["kysymykset"], SAANNOT["yleiskysymykset"][1]])
    if kieli != "fi":
        o = KIELET["mallin_ohjeet"][kieli]
        nimi = laake["selkonimi"] if laake["kerro_kayttotarkoitus"] else "uusi lääke"
        kuvaus = o["laake_kuvaus"].format(nimi=nimi, viikot=laake["viikkoa_sitten"])
        return o["tehtava"].format(laake=kuvaus, kysymykset=laakekysymykset)
    kielletyt = "\n".join(f"- {k}" for k in SAANNOT["kielletyt_aiheet"])
    yleiset = " ".join(OIREOHJEET["yleiset_ohjeet"])
    return f"""PUHELUN TEHTÄVÄ
Soitat kuulumispuhelun henkilölle, jolle on aloitettu uusi lääke: {_laakkeen_kuvaus(laake)}.
Olet jo esitellyt itsesi tekoälyksi ja kysynyt, miten hänellä menee. Avauksessa ei
mainittu lääkettä: ota se puheeksi luontevasti vasta, kun kuulumiset on kuultu.
Puhuttele sinuttelemalla, lämpimästi ja kiireettömästi.

KESKUSTELE, ÄLÄ KUULUSTELE:
- Reagoi AINA ensin siihen, mitä henkilö juuri sanoi. Myötäelä lyhyesti ja kysy siihen
  liittyvä luonteva jatkokysymys hänen omasta kokemuksestaan: milloin, missä tilanteissa,
  kuinka paljon se haittaa arkea.
- Kun henkilön kertoma asia on käsitelty, kysy välillä yksi alla olevista lääkkeeseen
  liittyvistä kysymyksistä. Käy ne läpi puhelun aikana, mutta ei kaikkia peräkkäin.
- Yksi kysymys kerrallaan.

LÄÄKKEESEEN LIITTYVÄT KYSYMYKSET:
{laakekysymykset}

OHJEET OIREISIIN:
- Jos viestin lopussa on suluissa "Tietopohja", se kertoo tunnistetun oireen, siihen sopivat
  jatkokysymykset ja kotihoito-ohjeet. Kysy ensin yksi jatkokysymys. Kun tiedät tarpeeksi,
  kerro lyhyesti yksi tai kaksi tietopohjan kotihoito-ohjetta omin sanoin ja milloin
  hoitajaan kannattaa olla yhteydessä.
- Anna VAIN tietopohjan ohjeita. Älä keksi omia ohjeita, älä suosittele lääkkeitä, älä neuvo
  annoksista äläkä arvaa oireen syytä. {yleiset}

EPÄSELVÄT VASTAUKSET:
Jos vastaus ei liity kysymykseen, on ristiriitainen tai siitä ei saa selvää, ÄLÄ arvaa
vastausta. Aloita silloin vastauksesi sanalla UUDELLEEN ja kysy sama kysymys uudelleen
lyhyemmin ja yksinkertaisemmin, esimerkiksi niin että siihen voi vastata kyllä tai ei.
Jos viestin lopussa on suluissa ohje siirtyä eteenpäin, noudata sitä äläkä kysy uudelleen.

LOPETUS:
Kun lääkekysymykset on käyty ja henkilön kertomat asiat käsitelty, sano tasan sana
LOPETUS ja ei mitään muuta.

RAJAT:
{kielletyt}
{SAANNOT["kun_henkilo_itse_kertoo"]}
"""


def tukitilan_tehtava(syy: str, kieli: str = "fi") -> str:
    """Tehtävänanto vakavan asian jälkeen: lääkekysymykset pois, kuuntelu ja turvallisuus."""
    if kieli != "fi":
        return KIELET["mallin_ohjeet"][kieli]["tukitila"].format(syy=syy)
    return f"""PUHELUN TEHTÄVÄ NYT: TUKI
Henkilö kertoi juuri vakavasta asiasta ({syy}). Olet jo sanonut, että asia välitetään
hoitajalle tänään ja että vaaratilanteessa soitetaan 112. Lääkekysymykset jäävät pois.

Tehtäväsi on nyt kuunnella ja varmistaa turvallisuus:
- Reagoi lämpimästi siihen, mitä henkilö juuri sanoi. Älä vähättele, älä tuomitse,
  älä väittele, älä anna neuvoja äläkä kysele yksityiskohtia.
- Varmista lempeästi, onko hän nyt turvassa ja onko joku, joka voi olla hänen tukenaan.
- Jos hän on välittömässä vaarassa, kehota soittamaan 112 heti.
- Kerro tarvittaessa uudelleen, että hoitaja soittaa hänelle tänään.
- Yksi lyhyt kysymys kerrallaan. Ei puhelinnumeroita, paitsi 112.
- Kun hän on kertonut olevansa turvassa tai haluaa lopettaa, sano tasan sana LOPETUS.
"""


# ---------------------------------------------------------------- SÄÄNTÖPOHJAINEN TUNNISTUS
def normalisoi(teksti: str) -> str:
    """Pienet kirjaimet, yhdysmerkit pois ("rinta-kipu" -> "rintakipu"), välit siistiksi.
    Välimerkit säilytetään, koska niillä erotetaan lauseet kieltotarkistuksessa."""
    t = teksti.lower().replace("\u2019", "'")
    t = re.sub(r"(?<=\w)-(?=\w)", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _kaanna(kuviot: list[str]) -> list[re.Pattern]:
    return [re.compile(p) for p in kuviot]


_KIELTO = set(SAANNOT["kieltosanat"]).union(*_muut("kieltosanat"))
_HATA = {
    syy: _kaanna(kuviot + [p for m in _muut("hatamerkit") for p in m.get(syy, [])])
    for syy, kuviot in SAANNOT["hatamerkit"].items()
    if not syy.startswith("_")
}
_KELTAINEN = _kaanna(SAANNOT["hoitajalle_merkittavat"]["keltainen"])
_VAKAVAT = {
    syy: _kaanna(o["tunnisteet"] + [p for m in _muut("vakavat_huolet") for p in m.get(syy, [])])
    for syy, o in SAANNOT["vakavat_huolet"].items()
    if not syy.startswith("_")
}
_HUOLI = _kaanna(SAANNOT["huoli"]["tunnisteet"] + [p for m in _muut("huoli") for p in m])
HATA_TUKITILAAN = set(SAANNOT.get("hata_jatka_tukitilassa", []))

# Selkokieliset nimet hoitajan yhteenvetoon ja lopetusviestiin.
SELKONIMET = {
    "itsetuhoisuus": "raskaita ajatuksia",
    "yliannostus": "liian suuri lääkemäärä",
    "kaltoinkohtelu": "huono kohtelu tai väkivalta",
    "aistiharhat": "outoja aistimuksia",
    "toivottomuus": "toivottomuutta",
}


_LAUSERAJA = re.compile(r"[,.;:!?]|\b(mutta|vaan|kuitenkin|nyt|paitsi|kun taas)\b")


def _on_kielletty(teksti: str, alku: int) -> bool:
    """Onko osuman edellä samassa lauseessa (enintään 3 sanaa) kieltosana,
    esim. 'ei ole ollut rintakipua'. 'En tiedä, mutta rintaa puristaa' EI ole kielto.
    Jos osuma itse alkaa kieltosanalla ('en halua elää'), kuvio on jo huomioinut sen."""
    ennen = teksti[:alku]
    rajat = list(_LAUSERAJA.finditer(ennen))
    if rajat:
        ennen = ennen[rajat[-1].end():]
    edelliset = re.findall(r"\w+", ennen)[-3:]
    return any(s in _KIELTO for s in edelliset)


_OIRETUNNISTEET = {
    nimi: _kaanna(o["tunnisteet"] + [p for m in _muut("oireet") for p in m.get(nimi, [])])
    for nimi, o in _OIREET.items()
}
_LOPETUS = _kaanna(SAANNOT["lopetuspyynto"]["tunnisteet"] + [p for m in _muut("lopetuspyynto") for p in m])


@dataclass
class Havainto:
    hata: list[str] = field(default_factory=list)          # 112-tilanne
    hata_kielletty: list[str] = field(default_factory=list)  # mainittu, mutta kieltomuodossa
    vakavat: list[str] = field(default_factory=list)       # kaltoinkohtelu, aistiharhat, toivottomuus
    huoli: list[str] = field(default_factory=list)         # henkilö ilmaisee huolta/pelkoa/surua
    keltainen: list[str] = field(default_factory=list)
    oireet: list[str] = field(default_factory=list)        # oireohjeet.json:n oireet
    huolestuttavat: list[str] = field(default_factory=list)  # oireista ne, jotka nostavat tasoa
    lopetuspyynto: bool = False
    kaikuharha: bool = False                               # Whisperin kaiku, ei puhetta


_JALKIKIELTO = re.compile(
    r"^\w*\s+(ei|en) (ole|oo)(( enää| yhtään| ollenkaan)? (ollut|ilmennyt|tuntunut|esiintynyt)\b|\s*([,.!?]|$))")


def _kielletty_jalkeen(teksti: str, loppu: int) -> bool:
    """'Hengenahdistusta ei ole ollut': kielto osuman jälkeen samassa lauseessa."""
    return bool(_JALKIKIELTO.search(teksti[loppu:]))


def _osuma_ilman_kieltoa(teksti: str, kuviot: list[re.Pattern]) -> re.Match | None:
    for k in kuviot:
        for osuma in k.finditer(teksti):
            if not _on_kielletty(teksti, osuma.start()) and not _kielletty_jalkeen(teksti, osuma.end()):
                return osuma
    return None


def _ensimmainen_osuma(teksti: str, kuviot: list[re.Pattern]) -> re.Match | None:
    for k in kuviot:
        osuma = k.search(teksti)
        if osuma:
            return osuma
    return None


def tarkista(teksti: str, kaikuharha: bool = False) -> Havainto:
    """Ajetaan jokaiselle puheenvuorolle ENNEN LLM:ää. Ei riipu mallista.

    Hätämerkit tarkistetaan aina, myös kaikuharhasta: väärä hälytys on parempi kuin
    ohitettu hätä. Oireita ja huomiosanoja ei poimita kaiusta (ne ovat vihjesanoja)."""
    t = normalisoi(teksti)
    h = Havainto(kaikuharha=kaikuharha)
    for syy, kuviot in _HATA.items():
        osuma = _osuma_ilman_kieltoa(t, kuviot)
        if osuma:
            h.hata.append(syy)
        elif _ensimmainen_osuma(t, kuviot):
            h.hata_kielletty.append(syy)
    for syy, kuviot in _VAKAVAT.items():
        if _osuma_ilman_kieltoa(t, kuviot):
            h.vakavat.append(syy)
    h.lopetuspyynto = _osuma_ilman_kieltoa(t.strip(" .,!"), _LOPETUS) is not None
    if kaikuharha:
        return h
    for k in _HUOLI:
        osuma = k.search(t)
        if osuma and not _on_kielletty(t, osuma.start()) and osuma.group(0) not in h.huoli:
            h.huoli.append(osuma.group(0))
    for k in _KELTAINEN:
        osuma = k.search(t)
        if osuma and not _on_kielletty(t, osuma.start()):
            h.keltainen.append(osuma.group(0))
    for nimi, kuviot in _OIRETUNNISTEET.items():
        if _osuma_ilman_kieltoa(t, kuviot):
            h.oireet.append(nimi)
            if _OIREET[nimi]["huolestuttava"]:
                h.huolestuttavat.append(nimi)
    return h


def oireohje_mallille(oireet: list[str]) -> str:
    """Piilotettu vihje mallille: tunnistetun oireen jatkokysymykset ja kotihoito-ohjeet."""
    if not oireet:
        return ""
    osat = []
    for nimi in oireet:
        o = _OIREET[nimi]
        osat.append(
            f"oire '{nimi}'. Jatkokysymyksiä: {' '.join(o['lisakysymykset'])} "
            f"Kotihoito-ohjeet: {' '.join(o['kotihoito'])} "
            f"Hoitajaan yhteys: {o['ota_yhteytta']}."
        )
    return "\n(Tietopohja: henkilö mainitsi " + " | ".join(osat) + ")"


def huolet(havainnot: list[Havainto], epaselvia_sisaltoja: int = 0) -> list[str]:
    """Puhelussa ilmenneet huolestuttavat asiat selkokielellä (lopetusviestiä varten)."""
    tulos: list[str] = []
    for h in havainnot:
        for o in [*h.hata, *h.vakavat]:
            nimi = SELKONIMET.get(o, o.replace("_", " "))
            if nimi not in tulos:
                tulos.append(nimi)
        for o in h.huolestuttavat:
            if o not in tulos:
                tulos.append(o)
    if not tulos and any(h.huoli for h in havainnot):
        tulos.append("huolta voinnista")
    if not tulos and any(h.keltainen for h in havainnot):
        tulos.append("vointiin liittyviä oireita")
    if epaselvia_sisaltoja >= _UUDELLEEN_RAJA():
        tulos.append("vastauksista oli vaikea saada selvää")
    return tulos


# ---------------------------------------------------------------- VASTAUKSEN SELKEYS
_UUDELLEEN = SAANNOT["uudelleenkysymys"]
_TOISTOPYYNNOT = _kaanna(_UUDELLEEN["toistopyynnot"] + [p for m in _muut("toistopyynnot") for p in m])
_HARHAT = _UUDELLEEN["puheentunnistuksen_harhat"]


@dataclass
class Litterointilaatu:
    """faster-whisperin luottamusarvot puheenvuorolle (huonoin segmentti)."""
    min_avg_logprob: float
    max_no_speech_prob: float


def arvioi_vastaus(teksti: str, laatu: Litterointilaatu | None = None) -> str:
    """Palauttaa 'selvä', 'toistopyyntö' tai 'epäselvä'. Ei kutsu mallia.

    'epäselvä' = puheentunnistus ei saanut kunnolla selvää (huono laatu, tunnettu
    Whisper-harha tai ei yhtään kirjainta). Sisällön järkevyyden arvioi malli erikseen.
    """
    t = teksti.lower().strip(" .,!")
    if any(k.search(t) for k in _TOISTOPYYNNOT):
        return "toistopyyntö"
    if not re.search(r"[a-zåäö]", t) or any(h in t for h in _HARHAT):
        return "epäselvä"
    if laatu is not None:
        huono_varmuus = laatu.min_avg_logprob < _UUDELLEEN["min_avg_logprob"]
        todennakoisesti_hiljaisuutta = laatu.max_no_speech_prob > _UUDELLEEN["max_no_speech_prob"]
        if huono_varmuus or todennakoisesti_hiljaisuutta:
            return "epäselvä"
    return "selvä"


_MYONTAVA = re.compile(r"\b(kyllä|joo|juu|jep|on ollut|on|ehkä|vähän|välillä|joskus|totta|niin on|olen|oon|ajattelen|ajatellut)\b")
_KIELTAVA_ALKU = re.compile(r"^(ei|en|eipä|enpä|no ei|ei ole|ei oo|en ole|en oo)\b")


def on_myontava(teksti: str) -> bool:
    """Vastaus kyllä/ei-varmistukseen. Epäselvä tai myöntävä -> True (turvallisempi tulkinta)."""
    t = normalisoi(teksti).strip(" .,!?")
    if re.search(r"\b(en tiedä|en tiiä|en osaa sanoa|en ole varma|ehkä|vähän|välillä|joskus)\b", t):
        return True
    if _KIELTAVA_ALKU.search(t) and not re.search(r"\b(mutta|vaikka)\b", t):
        return False
    return bool(_MYONTAVA.search(t)) or len(t.split()) > 3


VARMISTETTAVAT = set(SAANNOT["kieltomuodossa_varmistettavat"]["merkit"])


def kaikki_sallitut_numerot() -> set[str]:
    """Puhelinnumerot, jotka avustaja saa sanoa: vain keskustelusaannot.json:n kiinteissä
    teksteissä olevat (112, 116117, kriisipuhelin...). Malli ei saa keksiä numeroita."""
    numerot: set[str] = {"112", "116117"}

    def kay(x):
        if isinstance(x, str):
            for n in re.findall(r"\d[\d ]{1,}\d", x):
                numerot.add(re.sub(r"\D", "", n))
        elif isinstance(x, dict):
            for v in x.values():
                kay(v)
        elif isinstance(x, list):
            for v in x:
                kay(v)
    kay(SAANNOT)
    kay(KIELET)
    return numerot


def viimeisin_kysymys(history: list[dict]) -> str:
    """Avustajan viimeisin kysymyslause (toistoa varten)."""
    for viesti in reversed(history):
        if viesti["role"] != "assistant":
            continue
        kysymykset = re.findall(r"[^.!?]*\?", viesti["content"])
        return kysymykset[-1].strip() if kysymykset else viesti["content"].strip()
    return ""


def kiireellisyys(havainnot: list[Havainto], epaselvia_sisaltoja: int = 0) -> str:
    """Sääntöpohjainen alaraja tasolle. LLM tai hoitaja voi vain nostaa tätä.

    Toistuvasti sisällöltään epäselvät vastaukset nostavat keltaiseksi: syy voi olla
    yhteys tai sekavuus, ja sen arvioi hoitaja.
    """
    if any(h.hata or h.vakavat for h in havainnot):
        return "punainen"
    if epaselvia_sisaltoja >= _UUDELLEEN["epaselvat_nostaa_keltaiseksi"]:
        return "keltainen"
    # Kieltomuotoinen maininta ("ei ole rintakipua") kirjataan perusteluun, ei nosta tasoa.
    if any(h.keltainen or h.huolestuttavat or h.huoli for h in havainnot):
        return "keltainen"
    return "vihreä"


def _UUDELLEEN_RAJA() -> int:
    return _UUDELLEEN["epaselvat_nostaa_keltaiseksi"]
