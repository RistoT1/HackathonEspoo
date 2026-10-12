"""Saantotunnistimet (kerros 1b): ajetaan jokaiselle puheenvuorolle LLM:n rinnalla.

Deterministisia, testattavia ja LLM:sta riippumattomia. Tilakone (keskustelu.py) ajaa ne
ennen mallia: hata, hoitajapyynto, laakeneuvo ja usein kysytyt eivat kulje mallin kautta.
Jeren tietopohja (tietopohja.tarkista: kielto ennen/jalkeen, vakavat huolet, huoli,
lopetuspyynto, loukkaus, 18 oiretta) ajetaan samalla ja on kentassa Saantohavainnot.tieto.
Hata laukeaa, jos joko hatasanat.json (ei tulkitse kieltoa) tai tietopohja osuu.
"""

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.puhelu.hatasanat import normalisoi, tunnista_hatamerkki
from app.puhelu.tietopohja import Tietopohjahavainto, tarkista
from app.puhelu.vihjeet import oiresanat

# Pyyntö puhua ihmiselle: ohjataan aina hoitajalle. Lauseen osia, ei yksittäisiä sanoja,
# jotta "kävin lääkärissä" ei katkaise puhelua.
_HOITAJAPYYNNOT = (
    "puhua hoitaja",
    "puhua ihmise",
    "puhua oikea",
    "puhua lääkäri",
    "haluan hoitaja",
    "haluan ihmise",
    "oikean ihmisen",
    "hoitaja soittaa",
    "hoitaja soittais",
)
# Lääkeneuvon pyyntö: mitattu 10.10., gemma ohitti "pitäisikö lopettaa lääke?" -kysymyksen.
_LAAKENEUVOPYYNNOT = (
    "pitäisikö minun lopettaa",
    "pitäisikö lopettaa",
    "pitäiskö lopettaa",
    "voinko lopettaa",
    "saanko lopettaa",
    "lopetanko",
    "voinko jättää",
    "saanko jättää",
    "pitäisikö ottaa",
    "voinko ottaa",
    "saanko ottaa",
    "otanko",
    "kuinka monta tablettia",
    "kuinka paljon saa ottaa",
    "kuinka paljon pitää ottaa",
    "pitäisikö annosta",
    "voinko vaihtaa",
    "voiko sen lopettaa",
)
# Mieliala: ei hätämerkki, vaan ongelma, jota tarkennetaan myötätuntoisesti.
# Puhelu 10.10.: "ei ole ollut kipua, masentaa" -> malli kuittasi vain kivun.
_MIELIALA = (
    "masenta",
    "masennu",
    "ahdista",
    "alakulo",
    "yksinäi",
    "en jaksa",
    "itkettää",
    "itken",
    "surullinen",
    "suru",
    "toivoton",
    "mieli on maassa",
    "pelottaa",
    "pelkään",
    "huolestutta",
    "huolissani",
)
# Oiresanat varatoimintaan, kun LLM:ää ei ole (vihjesanat.json:n oireet + nämä).
# Puhelu 11.10.: malli aikakatkaistiin, ja "Vatsaan sattuu." kuitattiin ja ohitettiin.
_OIREEN_MERKIT = (
    "sattu",
    "kipu",
    "kipua",
    "kivu",
    "särk",
    "särky",
    "huonosti",
    "huono olo",
    "heikko",
    "heikottaa",
    "pahoin",
    "oksent",
    "ripuli",
    "ummetus",
    "löysä",
    "löysänä",
    "lämpö",
    "lämpöä",
    "kuume",
    "väsy",
    "turvo",
    "kutin",
    "huimaa",
    "huimas",
)
# Varaus: "ei ole, MUTTA ..." / "hyvin menee, MUTTA ..." on aina hälyttävä merkki.
_VARAUSSANAT = ("mutta", "paitsi", "kuitenkin", "vaikka")
_KIELTOSANAT = ("ei", "en", "eikä", "enkä", "eivät", "emme")
_KIELTAVAT_ALUT = ("ei", "en", "eipä", "eikä")
_KIELTAVAT_ILMAUKSET = (
    "myöhemmin",
    "ei nyt",
    "ei ole aikaa",
    "en ehdi",
    "ei sovi",
    "no ei",
    "no en",
)
_MYONTAVAT_ALUT = ("kyllä", "joo", "juu", "jep", "on", "voi", "voin", "sopii", "ok", "okei")
_MYONTAVAT_ILMAUKSET = (
    "kyllä",
    "joo",
    "juu",
    "okei",
    "sopii",
    "käy se",
    "totta kai",
    "mikä ettei",
    "ehdin",
    "on aikaa",
)
_KYSYMYSSANAT = (
    "kuka",
    "mikä",
    "mitä",
    "miksi",
    "mistä",
    "mihin",
    "kenen",
    "keneltä",
    "kuinka",
    "miten",
    "onko",
    "oletteko",
    "olenko",
    "häh",
    "anteeksi",
)


def _sisaltaa(normalisoitu: str, ilmaukset: tuple[str, ...]) -> str | None:
    for ilmaus in ilmaukset:
        if f" {ilmaus}" in normalisoitu:
            return ilmaus
    return None


def haluaa_hoitajan(teksti: str) -> bool:
    return _sisaltaa(normalisoi(teksti), _HOITAJAPYYNNOT) is not None


def kysyy_laakeneuvoa(teksti: str) -> bool:
    return _sisaltaa(normalisoi(teksti), _LAAKENEUVOPYYNNOT) is not None


def tunnista_mieliala(teksti: str) -> str | None:
    return _sisaltaa(normalisoi(teksti), _MIELIALA)


def tunnista_varaus(teksti: str) -> str | None:
    """Varaussanan jälkeinen osa ("ei ole kipua, mutta masentaa" -> "masentaa") tai None."""
    sanat = normalisoi(teksti).split()
    for indeksi, sana in enumerate(sanat):
        jatkuu = indeksi < len(sanat) - 1
        if sana in _VARAUSSANAT and jatkuu:
            return " ".join(sanat[indeksi + 1 :])
    return None


@lru_cache
def _fraasit() -> dict:
    return json.loads(Path(__file__).with_name("fraasit.json").read_text(encoding="utf-8"))


def usein_kysytty(teksti: str) -> str | None:
    """Vastaus tavalliseen kysymykseen ("kuka soittaa?", "mikä lääke?") tai None.
    Sääntö, ei LLM: vastaus heti ja aina oikein (puhelu 11.10.: LLM aikakatkaistiin)."""
    normalisoitu = normalisoi(teksti)
    for avain, kohta in _fraasit()["usein_kysytyt"].items():
        if avain.startswith("_"):
            continue
        if _sisaltaa(normalisoitu, tuple(kohta["tunnisteet"])):
            return kohta["vastaus"]
    return None


def hammentynyt(teksti: str) -> bool:
    tunnisteet = tuple(_fraasit()["hammennys"]["tunnisteet"])
    return _sisaltaa(normalisoi(teksti), tunnisteet) is not None


# Varatoiminnan oirekuvaus (perusmuoto) -> fraasit.json:n oirekohtaiset tarkennukset.
# Tarkimmat ensin: "nukkuu huonosti" on univaikeus, ei yleinen "huonosti" (11.10.: malli
# kaatui ja varakysymys oli "Kertoisitteko tarkemmin?" väsymyksestä).
_OIREKUVAUKSET: tuple[tuple[tuple[str, ...], str], ...] = (
    (("väsy", "en jaksa", "ei jaksa", "uupu", "voimaton"), "väsymys"),
    (("nukun huonosti", "nukkuu huonosti", "nukkunut huonosti", "uneton", "valvo"), "unettomuus"),
    (("huimaa", "huimas", "huimau"), "huimaus"),
    (("vatsa",), "vatsakipu"),
    (("pää",), "päänsärky"),
    (("selkä", "selkää"), "selkäkipu"),
    (("pahoin", "oksent"), "pahoinvointi"),
    (("turvo",), "turvotus"),
    (("kutin", "kutia"), "kutina"),
    (("kuume", "lämpö"), "kuume"),
    (("sattu", "kipu", "kipua", "kivu", "särk"), "kipu"),
)


def tunnista_oire(teksti: str) -> str | None:
    """Oireen kuvaus perusmuodossa tai None. Vain varatoimintaan: ei tulkitse kieltoa."""
    normalisoitu = normalisoi(teksti)
    oiresana = _sisaltaa(normalisoitu, (*_OIREEN_MERKIT, *oiresanat(), *_KAIKKI_OIREKUVAUSSANAT))
    if oiresana is None:
        return None
    for sanat, kuvaus in _OIREKUVAUKSET:
        if _sisaltaa(normalisoitu, sanat) is not None:
            return kuvaus
    return "vaiva"  # esim. "huono olo": "Milloin vaiva alkoi?"


_KAIKKI_OIREKUVAUSSANAT = tuple(sana for sanat, _ in _OIREKUVAUKSET for sana in sanat)


def sisaltaa_kiellon(teksti: str) -> bool:
    return any(sana in _KIELTOSANAT for sana in normalisoi(teksti).split())


def myontaa(teksti: str) -> bool:
    normalisoitu = normalisoi(teksti)
    sanat = normalisoitu.split()
    alkaa_myontavasti = bool(sanat) and sanat[0] in _MYONTAVAT_ALUT
    return alkaa_myontavasti or _sisaltaa(normalisoitu, _MYONTAVAT_ILMAUKSET) is not None


# Pohdinta ääneen ("olikohan se tiistai?") ei ole kysymys agentille (11.10.: siihen
# luvattiin "välitän kysymyksenne hoitajalle").
_POHDINTAPAATTEET = ("kohan", "köhän", "kohhan")


def on_kysymys(teksti: str) -> bool:
    """Henkilö kysyy jotain. STT ei aina lisää '?', joten myös kysymyssana missä tahansa
    ja -ko/-kö-päätteinen verbi ("soitatteko"). Väärähälytys maksaa vain yhden lisävuoron."""
    sanat = normalisoi(teksti).split()
    if any(sana.endswith(_POHDINTAPAATTEET) for sana in sanat):
        return False
    kysymyssana = any(sana in _KYSYMYSSANAT for sana in sanat)
    ko_paate = any(len(sana) >= 5 and sana.endswith(("ko", "kö")) for sana in sanat)
    return "?" in teksti or kysymyssana or ko_paate


# Henkilö ei tiedä tai osaa sanoa: hyväksytty vastaus, ei kysytä samaa uudelleen
# (11.10.: "En minä sen tarkemmin osaa sitä selittää" -> "Voitko kertoa tarkemmin..." x2).
_EI_TIEDA = (
    "en tiedä",
    "en tiiä",
    "en tie",
    "en osaa sanoa",
    "en osaa selittää",
    "en osaa sitä",
    "en osaa tarkemmin",
    "en sen tarkemmin",
    "en muista",
    "en oo varma",
    "en ole varma",
    "vaikea sanoa",
    "vaikee sanoa",
    "semmosta se on",
    "sellaista se on",
    "ei sen kummempaa",
    "ei sen ihmeempää",
)


# "Minä en kyllä muista" / "en minä tiedä": täytesanat kiellon ja verbin välissä.
_EI_TIEDA_JOUSTAVA = re.compile(r"\b(en|emme)( \w+){0,2} (muista|tiedä|tiiä|osaa|tunne)\b")


def ei_tieda(teksti: str) -> bool:
    normalisoitu = normalisoi(teksti)
    joustava = _EI_TIEDA_JOUSTAVA.search(normalisoitu) is not None
    return joustava or _sisaltaa(normalisoitu, _EI_TIEDA) is not None


# "En ole huomannut mitään" -> kunnossa avoimeen kysymykseen, kun malli ei osaa sanoa
# (11.10.: "No en minä ainakaan oo huomannut mitään ihmeellistä" -> epäselvä, hiljainen ohitus).
_EI_HUOMATTAVAA = re.compile(
    r"\b(en|ei)( \w+){0,3} (huomannut|huomattu|ollut mitään|mitään (ihmeellistä|erikoista|"
    r"uutta|eroa|muutosta))\b|\bei mitään (ihmeellistä|erikoista|uutta)\b"
)
_SELVAT_VASTAUKSET = ("kyllä", "joo", "juu", "jep", "ei", "en", "on", "ok", "okei", "ei ole")
# "Haloo"/"kuuluuko" = henkilö ei kuule tai epäilee yhteyttä: vastataan kuten tervehdykseen.
_TERVEHDYKSET = (
    "hyvää päivää",
    "päivää",
    "hei",
    "moi",
    "terve",
    "huomenta",
    "iltaa",
    "moikka",
    "haloo",
    "halloo",
    "kuuluuko",
)


def ei_huomattavaa(teksti: str) -> bool:
    return _EI_HUOMATTAVAA.search(normalisoi(teksti)) is not None


def selva_lyhyt_vastaus(teksti: str) -> bool:
    """Yksisanainen kyllä/ei: kelpaa, vaikka Twilio antaisi varmuuden 0,0 (lyhyet sanat)."""
    return normalisoi(teksti).strip() in _SELVAT_VASTAUKSET


def tervehtii(teksti: str) -> bool:
    """Kokonaiset sanat: "hei" ei saa osua sanaan "heikottaa"."""
    normalisoitu = f"{normalisoi(teksti)} "
    return any(f" {tervehdys} " in normalisoitu for tervehdys in _TERVEHDYKSET)


def kieltaytyy(teksti: str) -> bool:
    normalisoitu = normalisoi(teksti)
    sanat = normalisoitu.split()
    alkaa_kiellolla = bool(sanat) and sanat[0] in _KIELTAVAT_ALUT
    return alkaa_kiellolla or _sisaltaa(normalisoitu, _KIELTAVAT_ILMAUKSET) is not None


@dataclass(frozen=True)
class Saantohavainnot:
    """Yhden puheenvuoron saantolöydökset."""

    hatamerkki: str | None = None
    hoitajapyynto: bool = False
    laakeneuvo: bool = False
    kysymys: bool = False
    mieliala: str | None = None
    varaus: str | None = None  # varaussanan jälkeinen osa
    kielto: bool = False
    myonto: bool = False
    oire: str | None = None  # oireen kuvaus (vain varatoimintaan ilman LLM:ää)
    ei_tieda: bool = False  # "en tiedä", "en osaa sanoa": hyväksytty vastaus
    ei_huomattavaa: bool = False  # "en ole huomannut mitään": kunnossa
    tervehdys: bool = False  # "hyvää päivää"
    faq_vastaus: str | None = None  # valmis vastaus tavalliseen kysymykseen
    hammennys: bool = False  # henkilö ei ymmärtänyt ("häh", "selität jotain omaa")
    # Jeren tietopohjan löydökset (vakavat huolet, huoli, lopetuspyyntö, loukkaus, oireet).
    tieto: Tietopohjahavainto = field(default_factory=Tietopohjahavainto)

    @property
    def ongelma(self) -> bool:
        """Säännön mukaan vastauksessa on jotain, mitä pitää tarkentaa."""
        return self.mieliala is not None or self.varaus is not None or bool(self.tieto.huoli)


# hatasanat.json:n luokat tietopohjan nimille (hätäviestit ovat tietopohjassa).
_HATALUOKAT = {"halvausoire": "halvausoireet", "itsetuho": "itsetuhoisuus"}


def _hatamerkki(teksti: str, tieto: Tietopohjahavainto) -> str | None:
    """Tietopohjan tarkempi luokka ensin; hatasanat.json varmistaa (ei tulkitse kieltoa)."""
    if tieto.hata:
        return tieto.hata[0]
    luokka = tunnista_hatamerkki(teksti)
    return _HATALUOKAT.get(luokka, luokka) if luokka else None


def _oire(teksti: str, tieto: Tietopohjahavainto) -> str | None:
    """Oireen kuvaus varatoimintaan. Tietopohja ei poimi kiellettyä oiretta."""
    if tieto.oireet:
        return tieto.oireet[0]
    return tunnista_oire(teksti)


def ei_ohitettava(teksti: str) -> bool:
    """Hätämerkki tai vakava huoli (itsetuho, kaltoinkohtelu): käsitellään, vaikka puhe
    tulisi kesken vuoron, jolloin muu puhe ohitetaan (relay.py)."""
    havainnot = tunnista(teksti)
    return havainnot.hatamerkki is not None or bool(havainnot.tieto.vakavat)


def tunnista(teksti: str) -> Saantohavainnot:
    if not teksti.strip():
        return Saantohavainnot()
    tieto = tarkista(teksti)
    return Saantohavainnot(
        hatamerkki=_hatamerkki(teksti, tieto),
        hoitajapyynto=haluaa_hoitajan(teksti),
        laakeneuvo=kysyy_laakeneuvoa(teksti),
        kysymys=on_kysymys(teksti),
        mieliala=tunnista_mieliala(teksti),
        varaus=tunnista_varaus(teksti),
        kielto=sisaltaa_kiellon(teksti),
        myonto=myontaa(teksti),
        oire=_oire(teksti, tieto),
        ei_tieda=ei_tieda(teksti),
        ei_huomattavaa=ei_huomattavaa(teksti),
        tervehdys=tervehtii(teksti),
        faq_vastaus=usein_kysytty(teksti),
        hammennys=hammentynyt(teksti),
        tieto=tieto,
    )
