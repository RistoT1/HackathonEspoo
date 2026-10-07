"""
Puheavustajan tietopohja: soittoprofiilit, keskustelusäännöt ja sääntöpohjainen
hätä- ja hälytystunnistus.

Periaate (CLAUDE.md): deterministinen logiikka erillään LLM:stä.
 - Mitä kysytään       -> soittoprofiili (rakennettu datasta, rakenna_tietopohja.py)
 - Oireet ja ohjeet     -> oireohjeet.json: säännöt tunnistavat oireen, malli saa siihen
                           valmiit jatkokysymykset ja kotihoito-ohjeet (ei keksi omia)
 - Hätämerkit / taso    -> säännöt täällä, ajetaan JOKAISELLE puheenvuorolle ennen LLM:ää
 - Lopetuspyynnöt       -> säännöt täällä (1. kysy syy, 2. lopeta jos ei huolta, 3. lopeta aina)
 - LLM                  -> luonteva keskustelu, jatkokysymykset ja ohjeiden sanoittaminen

Mallille EI lähetetä nimeä, henkilötunnusta, diagnooseja eikä potilastunnusta.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

KNOWLEDGE = Path(__file__).resolve().parent / "knowledge"


def _lue_json(nimi: str) -> dict:
    with open(KNOWLEDGE / nimi, encoding="utf-8") as f:
        return json.load(f)


SAANNOT = _lue_json("keskustelusaannot.json")
OIREOHJEET = _lue_json("oireohjeet.json")
_OIREET = {o["oire"]: o for o in OIREOHJEET["oireet"]}


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
    """Vihjesanat Whisperille: oireet ja lääkkeen selkonimi. Ei nimiä eikä tunnisteita."""
    sanat: list[str] = []
    for laake in profiili["uudet_laakkeet"]:
        sanat.append(laake["selkonimi"])
        sanat.extend(laake["seurattavat_oireet"])
    sanat.extend(_OIREET)
    ainutkertaiset = list(dict.fromkeys(sanat))
    return ", ".join(ainutkertaiset)


# ---------------------------------------------------------------- JÄRJESTELMÄKEHOTE
def _laakkeen_kuvaus(laake: dict) -> str:
    nimi = laake["selkonimi"] if laake["kerro_kayttotarkoitus"] else "uusi lääke"
    return f"{nimi}, aloitettu noin {laake['viikkoa_sitten']} viikkoa sitten"


def puhelun_tehtava(profiili: dict) -> str:
    """Muodostaa mallille tehtävänannon. Sisältää vain kysymykset, ei tunnistetietoja."""
    laake = profiili["uudet_laakkeet"][0]   # yksi lääke per puhelu: lyhyt ja selkeä
    laakekysymykset = "\n".join(f"- {k}" for k in [*laake["kysymykset"], SAANNOT["yleiskysymykset"][1]])
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

ÄLÄ KOSKAAN puhu näistä:
{kielletyt}
"""


# ---------------------------------------------------------------- SÄÄNTÖPOHJAINEN TUNNISTUS
_KIELTO = set(SAANNOT["kieltosanat"])
_HATA = {
    syy: [re.compile(p) for p in kuviot]
    for syy, kuviot in SAANNOT["hatamerkit"].items()
    if not syy.startswith("_")
}
_KELTAINEN = [re.compile(p) for p in SAANNOT["hoitajalle_merkittavat"]["keltainen"]]


_LAUSERAJA = re.compile(r"[,.;:!?]|\b(mutta|ja|vaan|kuitenkin|nyt)\b")


def _on_kielletty(teksti: str, alku: int) -> bool:
    """Onko osuman edellä samassa lauseessa (enintään 3 sanaa) kieltosana,
    esim. 'ei ole ollut rintakipua'. 'En tiedä, mutta rintaa puristaa' EI ole kielto."""
    ennen = teksti[:alku]
    rajat = list(_LAUSERAJA.finditer(ennen))
    if rajat:
        ennen = ennen[rajat[-1].end():]
    edelliset = re.findall(r"\w+", ennen)[-3:]
    return any(s in _KIELTO for s in edelliset)


_OIRETUNNISTEET = {
    nimi: [re.compile(p) for p in o["tunnisteet"]] for nimi, o in _OIREET.items()
}
_LOPETUS = [re.compile(p) for p in SAANNOT["lopetuspyynto"]["tunnisteet"]]


@dataclass
class Havainto:
    hata: list[str] = field(default_factory=list)          # 112-tilanne
    hata_kielletty: list[str] = field(default_factory=list)  # mainittu, mutta kieltomuodossa
    keltainen: list[str] = field(default_factory=list)
    oireet: list[str] = field(default_factory=list)        # oireohjeet.json:n oireet
    huolestuttavat: list[str] = field(default_factory=list)  # oireista ne, jotka nostavat tasoa
    lopetuspyynto: bool = False


def _osuma_ilman_kieltoa(teksti: str, kuviot: list[re.Pattern]) -> re.Match | None:
    for k in kuviot:
        for osuma in k.finditer(teksti):
            if not _on_kielletty(teksti, osuma.start()):
                return osuma
    return None


def tarkista(teksti: str) -> Havainto:
    """Ajetaan jokaiselle puheenvuorolle ENNEN LLM:ää. Ei riipu mallista."""
    t = teksti.lower()
    h = Havainto()
    for syy, kuviot in _HATA.items():
        for k in kuviot:
            osuma = k.search(t)
            if not osuma:
                continue
            kohde = h.hata_kielletty if _on_kielletty(t, osuma.start()) else h.hata
            if syy not in kohde:
                kohde.append(syy)
            break
    for k in _KELTAINEN:
        osuma = k.search(t)
        if osuma and not _on_kielletty(t, osuma.start()):
            h.keltainen.append(osuma.group(0))
    for nimi, kuviot in _OIRETUNNISTEET.items():
        if _osuma_ilman_kieltoa(t, kuviot):
            h.oireet.append(nimi)
            if _OIREET[nimi]["huolestuttava"]:
                h.huolestuttavat.append(nimi)
    h.lopetuspyynto = _osuma_ilman_kieltoa(t.strip(" .,!"), _LOPETUS) is not None
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
        for o in h.huolestuttavat:
            if o not in tulos:
                tulos.append(o)
    if not tulos and any(h.keltainen for h in havainnot):
        tulos.append("vointiin liittyviä oireita")
    if epaselvia_sisaltoja >= _UUDELLEEN_RAJA():
        tulos.append("vastauksista oli vaikea saada selvää")
    return tulos


# ---------------------------------------------------------------- VASTAUKSEN SELKEYS
_UUDELLEEN = SAANNOT["uudelleenkysymys"]
_TOISTOPYYNNOT = [re.compile(p) for p in _UUDELLEEN["toistopyynnot"]]
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
    if any(h.hata for h in havainnot):
        return "punainen"
    if epaselvia_sisaltoja >= _UUDELLEEN["epaselvat_nostaa_keltaiseksi"]:
        return "keltainen"
    # Kieltomuotoinen maininta ("ei ole rintakipua") kirjataan perusteluun, ei nosta tasoa.
    if any(h.keltainen or h.huolestuttavat for h in havainnot):
        return "keltainen"
    return "vihreä"


def _UUDELLEEN_RAJA() -> int:
    return _UUDELLEEN["epaselvat_nostaa_keltaiseksi"]
