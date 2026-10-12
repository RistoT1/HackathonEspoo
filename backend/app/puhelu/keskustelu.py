"""Puhelun tilakone Jeren mallin mukaan (puheavustaja/puheavustaja.py: main-silmukka).

Avaus (AI-ilmoitus, AI Act art. 50) on kiinteä. Sen jälkeen jokainen puheenvuoro:

  1) Säännöt ENNEN mallia (tietopohja.py + hatasanat.json), ei LLM:ää:
     hätämerkki -> hätäviesti (itsetuhoisuus -> tukitila, muut lopettavat puhelun),
     lopetuspyyntö (1. kuulumiset tai turvallisuuskysymys, 2. lopetus), loukkaus
     (3. kerralla lopetus), kieltomuotoinen itsetuho -> varmistuskysymys, vakava huoli ->
     kiinteä repliikki + tukitila, hoitajapyyntö, usein kysytty (kuka soittaa, mikä lääke),
     lääkeneuvon pyyntö -> kiinteä vastaus.
  2) Saatiinko vastauksesta selvää (toistopyyntö, puheentunnistuksen varmuus): kysymys
     toistetaan enintään max_per_kysymys kertaa, sitten siirrytään eteenpäin.
  3) Piilo-ohjeet mallille: uusi oire -> tietopohjan jatkokysymykset ja kotihoito-ohjeet,
     huoli -> pysähdy asian äärelle, lopuillaan -> lopettele, toistuva aloitus -> vaihtele.
  4) Malli keskustelee vapaasti (UUDELLEEN = kysyy uudelleen, LOPETUS = puhelu valmis).

Ei Twiliota eikä kantaa: testattavissa suoraan.
"""

import asyncio
import copy
import logging
import re
import time
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from enum import StrEnum

from app.mallit import (
    KaikuTulos,
    KysymyksenVastaus,
    Kysymys,
    MuutosTyyppi,
    Puhelukonteksti,
    PuhelunLoppu,
    PuhelunTulos,
)
from app.puhelu.agentti import Keskusteluagentti
from app.puhelu.hatasanat import tunnista_hatamerkki
from app.puhelu.kielentarkistus import Tarkistin, korjaa_kieli
from app.puhelu.saannot import Saantohavainnot, kieltaytyy, tunnista
from app.puhelu.tietopohja import (
    HATA_TUKITILAAN,
    ITSETUHOKYSYMYS,
    LOPETUSMERKKI,
    SELKONIMET,
    UUDELLEENMERKKI,
    VARMISTETTAVAT,
    Tietopohjahavainto,
    aloitus,
    arvioi_vastaus,
    huolet,
    huudahdus,
    jarjestelmaohje,
    jarkyttava_oire,
    kotihoito_puheeksi,
    oireohje_kotihoidolla,
    on_myontava,
    piilo_ohje,
    puhelun_vuororaja,
    puhelutyypit,
    siisti_vastaus,
    sisaltaa_kotihoidon,
    teksti,
    tukitilan_ohje,
    viimeisin_kysymys,
)
from app.puhelu.tietopohja import saannot as tietopohjan_saannot
from app.triage.analyysi import saantoyhteenveto
from app.triage.saannot import saantoloydokset

logger = logging.getLogger(__name__)

HOITAJALLE = "Selvä. Välitän pyyntösi hoitajalle, ja hoitaja soittaa sinulle. Kiitos."
EI_KUULUNUT = "En kuullut vastausta."
EI_VASTAUSTA_LOPETUS = "En kuule sinua. Soitamme myöhemmin uudelleen. Näkemiin."
LAAKENEUVO_VASTAUS = (
    "Hyvä kysymys, mutta en voi neuvoa lääkkeissä. Välitän kysymyksesi hoitajalle, "
    "ja hoitaja vastaa siihen. Älä muuta lääkitystä sitä ennen omin päin."
)

# Ensimmäinen vuoro odottaa soiton aikana alkanutta lämmitystä enintään näin kauan.
LAMMITYKSEN_ODOTUS_S = 12
OIREEN_VUOROT = 2
# Huolen kuuntelu (piilo-ohje + jatkovuorot) enintään näin monta kertaa puhelussa.
HUOLEN_KUUNTELUT = 3
OIRE_JATKO_OHJE = (
    "\n(Ohje: Henkilö kertoi oireista: {oireet}. Jatka niiden selvittämistä tietopohjan "
    "jatkokysymyksellä, yksi kerrallaan, älä ohita yhtäkään. Älä vielä kysy listan kysymystä.)"
)
OIRE_KOTIHOITO_OHJE = (
    "\n(Ohje: Kerro nyt lyhyesti tietopohjan kotihoito-ohjeet omin sanoin näihin: {oireet}, "
    "ja milloin hoitajaan kannattaa olla yhteydessä. Kysy sitten listan seuraava kysymys.)"
)

# Kaatumisen jälkeen: ei pääse ylös (hätä, ks. _turvallisuusvuoro).
EI_PAASE_YLOS = re.compile(r"\b(en|enkä) (pääse|pysty|jaksa) (nousemaan|ylös|pystyyn)")

# Huono vointi henkilön viimeisessä vastauksessa: malli ei saa lopettaa siihen (11.10.).
HUONO_VOINTI = re.compile(
    r"\b(huonosti|heikosti|kurjasti|surkeasti|paskasti|huono olo|paha olo"
    r"|ei (kovin |oikein )?hyvin)\b"
)
HUONON_VOINNIN_JATKO = "Kerrotko vähän tarkemmin, mikä oloasi vaivaa?"
OIREEN_TARKENNUS = "Kerrotko siitä vähän tarkemmin?"

# Peräkkäiset hiljaiset vuorot, joiden jälkeen puhelu lopetetaan.
MAX_HILJAISET = 2
# Montako viimeisintä vuoroparia mallille annetaan (Jere: MAX_TURNS).
MAX_VUOROPARIT = 10

_MUUTOKSEN_NIMI = {
    MuutosTyyppi.UUSI_LAAKE: "uusi lääke",
    MuutosTyyppi.LOPETETTU_LAAKE: "lääke lopetettu",
    MuutosTyyppi.ANNOSMUUTOS: "annosta muutettu",
    MuutosTyyppi.LYHYT_KUURI: "lyhyt lääkekuuri",
}


class Vaihe(StrEnum):
    ALKU = "ALKU"
    KESKUSTELU = "KESKUSTELU"
    TUKI = "TUKI"  # vakava asia: kuunnellaan ja varmistetaan turvallisuus, ei kysymyksiä
    LOPPU = "LOPPU"


@dataclass(frozen=True)
class Rajat:
    varmuusraja: float = 0.6  # puheentunnistuksen varmuus, jonka alla kysytään uudelleen
    max_vuorot: int = puhelutyypit()["kontekstit"]["laakeseuranta"]["max_vuorot"]


@dataclass(frozen=True)
class Vuoro:
    """Mita sanotaan ja jatketaanko kuuntelua. kuuntele=False = puhelu lopetetaan."""

    sanottavat: list[str]
    kuuntele: bool
    keskeytymaton: int = 0  # näin monta ensimmäistä lausetta ei voi keskeyttää puheella


@dataclass
class Puheluistunto:
    istunto_id: str
    tehtava_id: str
    kysymykset: list[Kysymys]
    konteksti: Puhelukonteksti
    paivita_jono: bool  # False = dev-testi, jonon tilaa ei muuteta
    rajat: Rajat = field(default_factory=Rajat)
    puhelu_sid: str | None = None  # Twilion CallSid: relay-websocket tarkistaa sen
    vaihe: Vaihe = Vaihe.ALKU
    # Keskustelu viesteinä (Jere: history). Vain muistissa; analyysi lukee sen ja se häviää.
    historia: list[dict[str, str]] = field(default_factory=list)
    vastaukset: list[KysymyksenVastaus] = field(default_factory=list)
    tietohavainnot: list[Tietopohjahavainto] = field(default_factory=list)
    versiot: dict[str, str] = field(default_factory=dict)  # kiinteiden repliikkien vaihtelu
    vuoro: int = 0
    hiljaisia: int = 0
    kerrat: int = 0  # montako kertaa nykyinen kysymys on kysytty uudelleen
    uudelleenkysymyksia: int = 0
    epaselvia: int = 0  # sisällöltään epäselvät (malli sanoi UUDELLEEN)
    ohitetut: list[str] = field(default_factory=list)
    kasitellyt_oireet: set[str] = field(default_factory=set)
    kasitellyt_vakavat: set[str] = field(default_factory=set)
    huoli_jatko: int = 0
    huolia_kuunneltu: int = 0  # huolen piilo-ohje annettu (HUOLEN_KUUNTELUT)
    aloitukset: list[str] = field(default_factory=list)
    tukitila: str | None = None
    itsetuho: bool = False
    odottaa_varmistusta: bool = False
    varmistettu: bool = False
    lopetuspyyntoja: int = 0
    turvallisuus_kysytty: bool = False
    lopettamassa: bool = False
    loukkauksia: int = 0
    # Kannan kysymysten kirjanpito: seuraavan kysymättömän indeksi. Malli saa sen
    # piilo-ohjeena; jos keskustelu ei etene, se lisätään mallin vastauksen perään.
    seuraava: int = 0
    ilman_edistysta: int = 0  # mallin vuorot ilman listan kysymystä
    valivastauksia: int = 0  # sanotut välivastaukset (vaihtelu)
    lammitetty: bool = False  # järjestelmäohje lämmitettiin jo soiton alkaessa
    avausteksti: str = ""  # valittu avauksen versio (sama lämmityksessä ja puhelussa)
    oire_jatko: int = 0  # vuorot, joina oiretta käsitellään ennen listan jatkamista
    avoimet_oireet: list[str] = field(default_factory=list)  # käsiteltävänä olevat oireet
    kotihoito_oireet: list[str] = field(default_factory=list)  # tämän vuoron kotihoito-ohjeet
    henkilo_kysyi: bool = False  # tämän vuoron puheessa oli kysymys
    malli_lammin: bool = False  # lämmitys valmis (siihen asti varatoiminta, ei odotusta)
    # Lokia varten: tämän vuoron mittaukset ja päätösten syyt.
    mittaukset: dict = field(default_factory=dict)
    syyt: list[str] = field(default_factory=list)
    viimeksi_vastattu: float | None = None
    viimeksi_sanottu_ms: int = 0
    loppu: PuhelunLoppu | None = None
    hatamerkki: str | None = None
    paattyi: datetime | None = None

    def merkitse_malli_lammin(self) -> None:
        self.malli_lammin = True

    def valitse_avaus(self) -> str:
        """Avauksen versio valitaan kerran (soiton alkaessa), jotta lämmitys ja puhelu
        käyttävät samaa tekstiä."""
        if not self.avausteksti:
            self.avausteksti = _kiintea(self, "avauslause")
        return self.avausteksti

    def lammitysviestit(self) -> list[dict[str, str]]:
        """Ensimmäisen mallikutsun alku sellaisenaan: järjestelmäohje + avaus."""
        return [
            {"role": "system", "content": self.ohje()},
            {"role": "assistant", "content": self.valitse_avaus()},
        ]

    def ohje(self) -> str:
        """Järjestelmäohje: tukitilassa kuuntelu ja turvallisuus, muuten puhelun tehtävä."""
        if self.tukitila is not None:
            return tukitilan_ohje(self.tukitila, self.itsetuho)
        muutokset = [
            _MUUTOKSEN_NIMI[muutos.tyyppi]
            + (f" (ryhmä {muutos.laakeryhma})" if muutos.laakeryhma else "")
            for muutos in self.konteksti.muutokset
        ]
        kysymykset = [kysymys.teksti for kysymys in self.kysymykset]
        return jarjestelmaohje(self.konteksti.ika, muutokset, kysymykset)

    def tulos(self) -> PuhelunTulos:
        """Tulos ja sääntöjen triage heti (LLM-analyysi päivittää yhteenvedon myöhemmin)."""
        saanto = saantoloydokset(
            self.tietohavainnot, self.vastaukset, self.loppu, self.hatamerkki, self.tukitila
        )
        return PuhelunTulos(
            istunto_id=self.istunto_id,
            tehtava_id=self.tehtava_id,
            loppu=self.loppu,
            hatamerkki=self.hatamerkki,
            vastaukset=list(self.vastaukset),
            paattyi=self.paattyi,
            tukitila=self.tukitila,
            yhteenveto=saantoyhteenveto(saanto),
        )

    def litterointi(self) -> str:
        """Puhelu tekstinä analyysia varten. Piilo-ohjeet pois. Vain muistissa."""
        rivit = []
        for viesti in self.historia:
            puhuja = "Avustaja" if viesti["role"] == "assistant" else "Henkilö"
            sisalto = viesti["content"].split("\n(")[0]
            rivit.append(f"{puhuja}: {sisalto}")
        return "\n".join(rivit)


# Soiton taustatyöt (lämmitys, välitäytteet) päivittävät näitä kesken vuoron: ei palauteta.
_EI_PALAUTETA = frozenset({"malli_lammin", "lammitetty", "valivastauksia", "puhelu_sid"})


def tilannekuva(istunto: Puheluistunto) -> Puheluistunto:
    """Istunnon tila ennen vuoroa, jotta kesken jäänyt vuoro voidaan perua (palauta)."""
    return copy.deepcopy(istunto)


def palauta(istunto: Puheluistunto, kuva: Puheluistunto) -> None:
    """Peruu vuoron muutokset: relay aloittaa vuoron alusta, kun henkilö jatkoi puhettaan
    mallin miettiessä. Kopio, jotta samaa kuvaa voi käyttää uudelleen."""
    for kentta in fields(istunto):
        if kentta.name not in _EI_PALAUTETA:
            setattr(istunto, kentta.name, copy.deepcopy(getattr(kuva, kentta.name)))


def lopeta(istunto: Puheluistunto, loppu: PuhelunLoppu) -> None:
    """Merkitsee puhelun paattyneeksi. Ensimmainen syy jaa voimaan."""
    if istunto.loppu is not None:
        return
    istunto.loppu = loppu
    istunto.vaihe = Vaihe.LOPPU
    istunto.paattyi = datetime.now(UTC)


def _syy(istunto: Puheluistunto, syy: str) -> None:
    istunto.syyt.append(syy)


def _kiintea(istunto: Puheluistunto, avain: str) -> str:
    return teksti(avain, istunto.versiot)


def _sano(istunto: Puheluistunto, sanottavat: list[str], kuuntele: bool = True) -> Vuoro:
    """Repliikki historiaan ja puhuttavaksi (Jere: puhu)."""
    ei_tyhjat = [osa for osa in sanottavat if osa and osa.strip()]
    if ei_tyhjat:
        istunto.historia.append({"role": "assistant", "content": " ".join(ei_tyhjat)})
    return Vuoro(sanottavat=ei_tyhjat, kuuntele=kuuntele)


def _lopetusvuoro(istunto: Puheluistunto, loppu: PuhelunLoppu, sanottavat: list[str]) -> Vuoro:
    lopeta(istunto, loppu)
    return _sano(istunto, sanottavat, kuuntele=False)


def _lopetuslause(istunto: Puheluistunto) -> str:
    return _kiintea(istunto, "lopetuslause_tuki" if istunto.tukitila else "lopetuslause")


def aloita(istunto: Puheluistunto, agentti: Keskusteluagentti) -> Vuoro:
    """Avaus: AI-ilmoitus ja kuulumiset (versio vaihtelee). Malli lämpiää avauksen ajan,
    jotta järjestelmäohje on välimuistissa ensimmäiseen vastaukseen mennessä."""
    istunto.vaihe = Vaihe.KESKUSTELU
    _syy(istunto, "avaus")
    if not istunto.lammitetty:
        try:
            asyncio.get_running_loop().create_task(agentti.lammita(istunto.lammitysviestit()))
        except RuntimeError:
            pass  # ei tapahtumasilmukkaa: lämmitys ohitetaan
    vuoro = _sano(istunto, [istunto.valitse_avaus()])
    # AI-ilmoitus (AI Act art. 50) ei saa katketa vastaajan "Haloo?":on.
    return Vuoro(sanottavat=vuoro.sanottavat, kuuntele=True, keskeytymaton=1)


def _hyvaksy_kaikki(teksti_: str) -> bool:
    return True


async def kasittele(
    istunto: Puheluistunto,
    puhe: str | None,
    varmuus: float | None,
    agentti: Keskusteluagentti,
    tarkistin: Tarkistin = _hyvaksy_kaikki,
) -> Vuoro:
    """Yksi vuoro: henkilön puhe sisään, seuraava repliikki ulos.
    tarkistin: mallin suomen tarkistus; hylkäys kirjataan lokiin (ei estä puhetta)."""
    istunto.syyt = []
    istunto.mittaukset = {}
    if istunto.vaihe == Vaihe.ALKU:
        return aloita(istunto, agentti)
    if istunto.vaihe == Vaihe.LOPPU:
        return Vuoro(sanottavat=[], kuuntele=False)

    teksti_ = (puhe or "").strip()
    if len(teksti_) < 2:
        return _hiljainen_vuoro(istunto)

    istunto.hiljaisia = 0
    istunto.vuoro += 1
    raja = puhelun_vuororaja(
        istunto.tietohavainnot,
        istunto.rajat.max_vuorot,
        istunto.vuoro,
        istunto.tukitila is not None,
        istunto.itsetuho,
    )
    istunto.vastaukset.append(
        KysymyksenVastaus(
            tunniste=f"vuoro-{istunto.vuoro}",
            kysymys=viimeisin_kysymys(istunto.historia),
            vastaus=teksti_,
            varmuus=varmuus,
        )
    )
    istunto.historia.append({"role": "user", "content": teksti_})
    saannot = tunnista(teksti_)
    istunto.henkilo_kysyi = saannot.kysymys
    tieto = saannot.tieto
    istunto.tietohavainnot.append(tieto)

    turvallisuus = _turvallisuusvuoro(istunto, saannot, teksti_)
    if turvallisuus is not None:
        return turvallisuus
    if istunto.vaihe == Vaihe.TUKI:
        return await _mallin_vuoro(istunto, agentti, "", tarkistin)
    if raja is not None and istunto.vuoro > raja:
        _syy(istunto, f"vuororaja:{raja}")
        return _lopetusvuoro(istunto, PuhelunLoppu.VALMIS, [_lopetuslause(istunto)])

    saantovastaus = _saantovastaus(istunto, saannot)
    if saantovastaus is not None:
        return saantovastaus

    selkeys = _selkeys(istunto)
    if isinstance(selkeys, Vuoro):
        return selkeys
    lopuillaan = raja is not None and istunto.vuoro >= raja
    ohje = selkeys + _piilo_ohjeet(istunto, tieto, lopuillaan)
    return await _mallin_vuoro(istunto, agentti, ohje, tarkistin)


def _hiljainen_vuoro(istunto: Puheluistunto) -> Vuoro:
    istunto.hiljaisia += 1
    if istunto.hiljaisia < MAX_HILJAISET:
        _syy(istunto, "hiljaisuus:toisto")
        kysymys = viimeisin_kysymys(istunto.historia)
        return Vuoro(sanottavat=[EI_KUULUNUT, kysymys] if kysymys else [EI_KUULUNUT], kuuntele=True)
    _syy(istunto, "hiljaisuus:lopetus")
    return _lopetusvuoro(istunto, PuhelunLoppu.EI_VASTAUSTA, [EI_VASTAUSTA_LOPETUS])


# --- 1) Säännöt ennen mallia ---


def _turvallisuusvuoro(
    istunto: Puheluistunto, saannot: Saantohavainnot, teksti_: str
) -> Vuoro | None:
    """Hätä, lopetuspyyntö, loukkaus, kieltomuotoinen itsetuho ja vakavat huolet."""
    tieto = saannot.tieto
    hatamerkki = saannot.hatamerkki

    # Vastaus suoraan varmistuskysymykseen ("onko ollut ajatuksia vahingoittaa itseäsi?").
    if istunto.odottaa_varmistusta:
        istunto.odottaa_varmistusta = False
        if hatamerkki is None and on_myontava(teksti_):
            hatamerkki = "itsetuhoisuus"
            tieto.hata.append(hatamerkki)
            _syy(istunto, "varmistus:myonteinen")

    # "En pääse ylös" eri vuorossa kuin kaatuminen (12.10.: kaatunut, verta päässä ja
    # lattialla -> vain keltainen). Sama lause ilman kaatumista ("sängystä aamuisin") ei hälytä.
    kaatui_aiemmin = any("kaatuminen" in havainto.oireet for havainto in istunto.tietohavainnot)
    if hatamerkki is None and kaatui_aiemmin and EI_PAASE_YLOS.search(teksti_.lower()):
        hatamerkki = "kaatunut_lattialla"
        tieto.hata.append(hatamerkki)

    # Tukitilassa jo käsitelty itsetuhoisuus ei toista pitkää viestiä joka vuorolla.
    jo_tuettu = istunto.tukitila is not None and hatamerkki in HATA_TUKITILAAN
    if hatamerkki is not None and not jo_tuettu:
        return _hatavuoro(istunto, hatamerkki)

    if istunto.lopettamassa:
        return _lopeta_pyynnosta(istunto)
    # Avauksen jälkeen "en ehdi nyt" on lopetuspyyntö (meillä ei ole erillistä lupavaihetta).
    if tieto.lopetuspyynto or (istunto.vuoro == 1 and kieltaytyy(teksti_)):
        return _lopetuspyynto(istunto)
    # Kiroilu oireen kertomisen yhteydessä on turhautumista: oire ensin (11.10.: "aikamoista
    # ripulia kun v**** turpa kiinni" -> kiinteä loukkausrepliikki, ripuli jäi käsittelemättä).
    if tieto.loukkaus and tieto.oireet and not tieto.uhkaus:
        _syy(istunto, "loukkaus:oire_ensin")
    elif tieto.loukkaus:
        return _loukkausvuoro(istunto, tieto)

    kielletty = VARMISTETTAVAT & set(tieto.hata_kielletty)
    if kielletty and not istunto.varmistettu:
        # "Ei mikään auta, haluan kuolla" voi tunnistua kielloksi: kysytään suoraan.
        istunto.varmistettu = True
        istunto.odottaa_varmistusta = True
        _syy(istunto, "itsetuho:varmistus")
        return _sano(istunto, [_kiintea(istunto, "varmistuskysymys")])

    uudet = [syy for syy in tieto.vakavat if syy not in istunto.kasitellyt_vakavat]
    if uudet:
        syy = uudet[0]
        istunto.kasitellyt_vakavat.update(uudet)
        _syy(istunto, f"vakava:{syy}")
        viesti = _kiintea(istunto, f"vakavat_huolet.{syy}.viesti")
        huuto = huudahdus(syy, istunto.versiot)
        _aloita_tukitila(istunto, syy)
        return _sano(istunto, [huuto, viesti])
    return None


def _hatavuoro(istunto: Puheluistunto, hatamerkki: str) -> Vuoro:
    """Hätäviesti luokittain. Itsetuhoisuus ei katkaise puhelua (tukitila); muut
    lopettavat, jotta henkilö soittaa heti hätänumeroon."""
    istunto.hatamerkki = istunto.hatamerkki or hatamerkki
    _syy(istunto, f"hatamerkki:{hatamerkki}")
    viestit = tietopohjan_saannot()["hataviestit"]
    avain = f"hataviestit.{hatamerkki}" if hatamerkki in viestit else "hataviesti"
    sanottavat = [huudahdus(hatamerkki, istunto.versiot, hata=True), _kiintea(istunto, avain)]
    if hatamerkki in HATA_TUKITILAAN:
        _aloita_tukitila(istunto, hatamerkki)
        return _sano(istunto, sanottavat)
    return _lopetusvuoro(istunto, PuhelunLoppu.HATAMERKKI, sanottavat)


def _aloita_tukitila(istunto: Puheluistunto, syy: str) -> None:
    """Vakavan asian jälkeen kysymykset jäävät pois: kuunnellaan ja varmistetaan
    turvallisuus. Puhelua ei katkaista ihmisen alta."""
    if istunto.tukitila is None:
        istunto.tukitila = SELKONIMET.get(syy, syy.replace("_", " "))
    if syy in ("itsetuhoisuus", "toivottomuus"):
        istunto.itsetuho = True
    istunto.vaihe = Vaihe.TUKI
    istunto.huoli_jatko = 0
    _syy(istunto, f"tukitila:{syy}")


def _lopetuspyynto(istunto: Puheluistunto) -> Vuoro:
    """Ihmistä ei pidetä puhelimessa vastoin tahtoa: lopetetaan heti ensimmäiseen pyyntöön
    (päätetty 12.10.; aiemmin ensin kuulumis- tai turvallisuuskysymys). Huolien jälkeen
    lopetusviestissä on hoitajan soitto ja 112. Poikkeus: itsetuhoisen puheen jälkeen
    kysytään kerran turvallisuudesta ennen lopetusta (Jere)."""
    istunto.lopetuspyyntoja += 1
    _syy(istunto, f"lopetuspyynto:{istunto.lopetuspyyntoja}")
    if istunto.itsetuho and not istunto.turvallisuus_kysytty:
        istunto.turvallisuus_kysytty = True
        istunto.lopettamassa = True
        return _sano(istunto, [_kiintea(istunto, "lopetuspyynto.turvallisuuskysymys")])
    return _lopeta_pyynnosta(istunto)


def _lopeta_pyynnosta(istunto: Puheluistunto) -> Vuoro:
    loydetyt = huolet(istunto.tietohavainnot, istunto.epaselvia)
    avain = "lopetuspyynto.lopetus_huolestuttava" if loydetyt else "lopetuspyynto.lopetus"
    _syy(istunto, "lopetuspyynto:lopetus")
    return _lopetusvuoro(istunto, PuhelunLoppu.LOPETTI, [_kiintea(istunto, avain)])


def _loukkausvuoro(istunto: Puheluistunto, tieto: Tietopohjahavainto) -> Vuoro:
    """Ei loukkaannuta eikä moralisoida: 1. kysytään, onko kaikki hyvin, 2. tarjotaan
    lopettamista tai hoitajan soittoa, 3. lopetetaan kohteliaasti."""
    istunto.loukkauksia += 1
    kerta = istunto.loukkauksia
    _syy(istunto, f"{'uhkaus' if tieto.uhkaus else 'loukkaus'}:{kerta}")
    if kerta >= tietopohjan_saannot()["loukkaus"]["lopeta_kerralla"]:
        return _lopetusvuoro(istunto, PuhelunLoppu.LOPETTI, [_kiintea(istunto, "loukkaus.lopetus")])
    if kerta >= 2:
        avain = "loukkaus.viesti_2"
    else:
        avain = "loukkaus.uhkaus_1" if tieto.uhkaus else "loukkaus.viesti_1"
    return _sano(istunto, [_kiintea(istunto, avain)])


def _saantovastaus(istunto: Puheluistunto, saannot: Saantohavainnot) -> Vuoro | None:
    """Hoitajapyyntö, lääkeneuvo ja usein kysytyt: vastaus säännöllä (ei mallia)."""
    if saannot.hoitajapyynto:
        _syy(istunto, "hoitajapyynto")
        return _lopetusvuoro(istunto, PuhelunLoppu.HALUAA_HOITAJAN, [HOITAJALLE])
    kysymys = viimeisin_kysymys(istunto.historia)
    if saannot.laakeneuvo:
        _syy(istunto, "laakeneuvo:kiintea")
        istunto.vastaukset[-1].kysymys_hoitajalle = True
        return _sano(istunto, [LAAKENEUVO_VASTAUS, kysymys])
    if saannot.faq_vastaus:
        _syy(istunto, "usein_kysytty")
        return _sano(istunto, [saannot.faq_vastaus, kysymys])
    return None


# --- 2-3) Selkeys ja piilo-ohjeet ---


def _selkeys(istunto: Puheluistunto) -> Vuoro | str:
    """Saatiinko vastauksesta selvää (säännöt, ei mallia). Vuoro = kysytään uudelleen;
    merkkijono = piilo-ohje mallille (siirry eteenpäin / älä kysy enää)."""
    vastaus = istunto.vastaukset[-1]
    arvio = arvioi_vastaus(vastaus.vastaus, vastaus.varmuus, istunto.rajat.varmuusraja)
    max_kerrat = tietopohjan_saannot()["uudelleenkysymys"]["max_per_kysymys"]
    kysymys = viimeisin_kysymys(istunto.historia)
    if arvio == "selvä":
        return piilo_ohje("ei_uudelleen") if istunto.kerrat >= max_kerrat else ""
    _syy(istunto, f"selkeys:{arvio}")
    vastaus.epavarma = True
    if istunto.kerrat < max_kerrat:
        istunto.kerrat += 1
        istunto.uudelleenkysymyksia += 1
        avain = (
            "uudelleenkysymys.toistoviesti"
            if arvio == "toistopyyntö"
            else "uudelleenkysymys.epaselva_viesti"
        )
        return _sano(istunto, [_kiintea(istunto, avain), kysymys])
    # Raja täynnä: ei jäädä jumiin, malli siirtyy seuraavaan kysymykseen.
    istunto.ohitetut.append(kysymys)
    istunto.kerrat = 0
    istunto.historia[-1]["content"] = "(ei selvää vastausta)"
    return piilo_ohje("siirry")


def _piilo_ohjeet(istunto: Puheluistunto, tieto: Tietopohjahavainto, lopuillaan: bool) -> str:
    """Mallin vuorokohtaiset ohjeet: oire, huoli, lopettelu ja vaihtelu."""
    ohje = ""
    uudet = [oire for oire in tieto.oireet if oire not in istunto.kasitellyt_oireet]
    istunto.kasitellyt_oireet.update(uudet)
    if uudet:
        _syy(istunto, f"tietopohja:{','.join(uudet)}")
    ohje += oireohje_kotihoidolla(uudet)
    if jarkyttava_oire(uudet):
        ohje = piilo_ohje("jarkytys") + ohje
    # Jere: "kysy ensin yksi jatkokysymys, kun tiedät tarpeeksi, kerro kotihoito-ohjeet".
    # Listan ohje ohitti tämän (11.10. arviointi): oireen jälkeen kaksi vuoroa oireelle.
    oire_kesken = istunto.oire_jatko > 0
    if uudet:
        # Useampi oire kerralla: kaikki nimetään, ettei yksi jää käsittelemättä (11.10.
        # testipankki: pahoinvointi ohitettiin, kun huimaus mainittiin samassa lauseessa).
        istunto.avoimet_oireet = [*istunto.avoimet_oireet, *uudet] if oire_kesken else uudet
        istunto.oire_jatko = OIREEN_VUOROT + len(istunto.avoimet_oireet) - 1
    elif oire_kesken:
        istunto.oire_jatko -= 1
        if istunto.oire_jatko == 0:
            ohje += OIRE_KOTIHOITO_OHJE.format(oireet=", ".join(istunto.avoimet_oireet))
            istunto.kotihoito_oireet = istunto.avoimet_oireet
            istunto.avoimet_oireet = []

    toistuva_huoli = tieto.huoli and istunto.huolia_kuunneltu >= HUOLEN_KUUNTELUT
    if toistuva_huoli:
        # Sama pelko joka vastauksessa piti huolen auki loputtomiin (12.10. testipankki:
        # puhelu ei päättynyt 17 vuorossa). Huoli on jo kirjattu triageen.
        _syy(istunto, f"huoli:toistuu:{','.join(tieto.huoli)}")
    elif tieto.huoli:
        _syy(istunto, f"huoli:{','.join(tieto.huoli)}")
        ohje = piilo_ohje("huoli") + ohje
        istunto.huoli_jatko = tietopohjan_saannot()["huoli"]["jatkovuoroja"]
        istunto.huolia_kuunneltu += 1
    elif istunto.huoli_jatko > 0:
        istunto.huoli_jatko -= 1
        ohje += piilo_ohje("huoli_jatko")

    if lopuillaan:
        ohje += piilo_ohje("lopettelu")
    kysymys = _avoin_kysymys(istunto)
    jaljella = len(istunto.kysymykset) - istunto.seuraava
    if istunto.oire_jatko > 0:
        ohje += OIRE_JATKO_OHJE.format(oireet=", ".join(istunto.avoimet_oireet))
    elif kysymys is not None:
        ohje += (
            "\n(Ohje: Kun henkilön kertoma asia on käsitelty, kysy seuraavaksi listalta: "
            f'"{kysymys}" Kysymättä vielä {jaljella}.)'
        )
    else:
        ohje += (
            "\n(Ohje: Kaikki listan kysymykset on kysytty. Kun henkilön kertoma asia on "
            "käsitelty, kiitä lyhyesti ja sano LOPETUS.)"
        )
    viime = istunto.aloitukset[-4:]
    toistuvat = [alku for alku in dict.fromkeys(viime) if viime.count(alku) >= 2]
    if toistuvat or len(istunto.aloitukset) >= 3:
        aiemmat = ", ".join(f'"{alku}"' for alku in dict.fromkeys(istunto.aloitukset[-3:]))
        ohje += piilo_ohje("vaihtele", aloitukset=aiemmat)
    return ohje


# --- 4) Malli ---


async def _mallin_vuoro(
    istunto: Puheluistunto, agentti: Keskusteluagentti, ohje: str, tarkistin: Tarkistin
) -> Vuoro:
    # Historiaan jää vain tietopohjan oiretieto (malli antaa kotihoito-ohjeet myöhemmällä
    # vuorolla, Jere). Hetkelliset "(Ohje: ...)" näkyvät vain tässä vuorossa: vanhat
    # "kysy seuraavaksi X" kertyivät historiaan ja olivat ristiriidassa uusien kanssa.
    kotihoito_oireet, istunto.kotihoito_oireet = istunto.kotihoito_oireet, []
    tietopohja = "".join(re.findall(r"\n\(Tietopohja: [^\n]*", ohje))
    hetkelliset = ohje.replace(tietopohja, "") if tietopohja else ohje
    istunto.historia[-1]["content"] += tietopohja
    nykyinen = {"role": "user", "content": istunto.historia[-1]["content"] + hetkelliset}
    viestit = [
        {"role": "system", "content": istunto.ohje()},
        *istunto.historia[-MAX_VUOROPARIT * 2 : -1],
        nykyinen,
    ]
    if istunto.lammitetty and not istunto.malli_lammin:
        # Odotetaan lämmitystä hetki (tarkkuus ennen nopeutta), mutta ei minuuttia: 11.10.
        # puhelu kuunteli välitäytteitä 70 s. Sitten listan kysymys säännöllä ja malli jatkaa
        # seuraavasta vuorosta (lämmitys valmistuu taustalla).
        for _ in range(int(LAMMITYKSEN_ODOTUS_S * 2)):
            if istunto.malli_lammin:
                break
            await asyncio.sleep(0.5)
        if not istunto.malli_lammin and istunto.vaihe != Vaihe.TUKI:
            _syy(istunto, "malli:lammitys_kesken")
            return _varakysymys(istunto)
    alku = time.perf_counter()
    try:
        raaka = await agentti.vastaa(viestit)
    except Exception as virhe:
        logger.exception("Mallin vastaus epäonnistui")
        _syy(istunto, f"agentti_virhe:{type(virhe).__name__}:{str(virhe)[:120]}")
        raaka = None
    istunto.mittaukset["malli_ms"] = round((time.perf_counter() - alku) * 1000)
    if raaka is not None:
        istunto.mittaukset["malli"] = raaka

    if istunto.vaihe == Vaihe.TUKI:
        return _tukivastaus(istunto, raaka)
    if raaka is None or not raaka.strip():
        return _varakysymys(istunto)

    if ITSETUHOKYSYMYS.search(raaka):
        istunto.odottaa_varmistusta = True  # seuraava vastaus kuin varmistuskysymykseen
    vastaus = raaka.strip()
    if vastaus.startswith(UUDELLEENMERKKI):
        istunto.kerrat += 1
        istunto.uudelleenkysymyksia += 1
        istunto.epaselvia += 1
        istunto.vastaukset[-1].epavarma = True
        vastaus = vastaus[len(UUDELLEENMERKKI) :].lstrip(":,. ")
        _syy(istunto, "malli:uudelleen")
    else:
        istunto.kerrat = 0

    lopettaa = LOPETUSMERKKI in vastaus
    huono_vointi = HUONO_VOINTI.search(istunto.historia[-1]["content"].split("\n(")[0].lower())
    # Oire käsittelyssä (jatkokysymykset, kotihoito-ohjeet kesken): malli ohitti ohjeen ja
    # lopetti (12.10. testipankki: "vapinaa käsissä" viimeiseen kysymykseen -> lopetus).
    oire_kesken = istunto.oire_jatko > 0
    if lopettaa and (huono_vointi or oire_kesken) and not istunto.henkilo_kysyi:
        # "No huonosti" ei ole käsitelty asia (11.10.: malli kiitti ja lopetti puhelun).
        _syy(istunto, f"malli:lopetus_ohitettu_{'oire' if oire_kesken else 'huono_vointi'}")
        lopettaa = False
        vastaus = vastaus.replace(LOPETUSMERKKI, "")
        if "?" not in vastaus:
            jatko = OIREEN_TARKENNUS if oire_kesken else HUONON_VOINNIN_JATKO
            vastaus = f"{vastaus.strip()} {jatko}"
    if lopettaa and istunto.henkilo_kysyi:
        # Henkilö kysyi juuri jotain: ei lopeteta hänen alta (11.10. arviointi: "Kuka sinä
        # olet?" -> vastaus ja lopetus, vaikka lääkekysymykseen ei ollut vastattu).
        _syy(istunto, "malli:lopetus_ohitettu_kysymys")
        lopettaa = False
        vastaus = vastaus.replace(LOPETUSMERKKI, "")
        kysymys = istunto.kysymykset[max(0, istunto.seuraava - 1)].teksti
        if "?" not in vastaus and istunto.kysymykset:
            vastaus = f"{vastaus.strip()} {kysymys}"
    puhuttava = _puhuttavaksi(istunto, vastaus.replace(LOPETUSMERKKI, ""))
    if puhuttava and not tarkistin(puhuttava):
        _syy(istunto, "kieli:epavarma")  # kirjataan, ei estetä (Jere: ei tarkistusta)
    if puhuttava:
        istunto.aloitukset.append(aloitus(puhuttava))
    if lopettaa:
        _syy(istunto, "malli:lopetus")
        puhuttava = _varmista_kotihoito(istunto, kotihoito_oireet, puhuttava)
        return _lopetusvuoro(istunto, PuhelunLoppu.VALMIS, [puhuttava, _lopetuslause(istunto)])
    if not puhuttava:
        return _varakysymys(istunto)
    yksi = _yksi_kysymys(istunto, puhuttava)
    return _eteneminen(istunto, _varmista_kotihoito(istunto, kotihoito_oireet, yksi))


def _puhuttavaksi(istunto: Puheluistunto, raaka: str) -> str:
    """Mallin teksti puheeksi: merkinnät pois ja toistuvat suomen virheet korjattuina."""
    puhuttava, korjatut = korjaa_kieli(siisti_vastaus(raaka))
    if korjatut:
        _syy(istunto, f"kieli:korjattu:{','.join(korjatut)}")
    return puhuttava


def _lauseet(teksti: str) -> list[str]:
    return [lause.strip() for lause in re.findall(r"[^.!?]+[.!?]*", teksti) if lause.strip()]


def _yksi_kysymys(istunto: Puheluistunto, puhuttava: str) -> str:
    """Vain ensimmäinen kysymys sanotaan (Jeren ohje: yksi kysymys kerrallaan). Pieni malli
    kysyi kolme asiaa kerralla, ja henkilö kysyi "kumpaan vastaan?" (11.10.). Kysymyksen
    jälkeinen "Välitän tämän hoitajalle" siirretään ennen kysymystä: leikkaus pudotti sen
    (12.10. testipankki), muut kysymyksen jälkeiset lauseet ovat seuraavan kysymyksen alkua."""
    lauseet = _lauseet(puhuttava)
    eka = next((i for i, lause in enumerate(lauseet) if lause.endswith("?")), None)
    if eka is None or eka == len(lauseet) - 1:
        return puhuttava
    lupaukset = [
        lause
        for lause in lauseet[eka + 1 :]
        if not lause.endswith("?") and "hoitaja" in lause.lower()
    ]
    _syy(istunto, "malli:yksi_kysymys")
    return " ".join([*lauseet[:eka], *lupaukset, lauseet[eka]])


def _varmista_kotihoito(istunto: Puheluistunto, oireet: list[str], puhuttava: str) -> str:
    """Kotihoito-ohjeen vuoro: malli ohitti ohjeen 14 kertaa 16:sta (12.10. testipankki),
    joten tietopohjan ohje lisätään ennen kysymystä, jos se puuttuu mallin vastauksesta."""
    if not oireet or sisaltaa_kotihoidon(puhuttava, oireet):
        return puhuttava
    ohje = kotihoito_puheeksi(oireet)
    if not ohje:
        return puhuttava
    _syy(istunto, f"kotihoito:lisatty:{','.join(oireet)}")
    lauseet = _lauseet(puhuttava)
    if lauseet and lauseet[-1].endswith("?"):
        return " ".join([*lauseet[:-1], ohje, lauseet[-1]])
    return f"{puhuttava} {ohje}".strip()


def _varakysymys(istunto: Puheluistunto) -> Vuoro:
    """Ei mallia, virhe tai aikakatkaisu: kannan kysymykset järjestyksessä ja lopetus.
    Puhelu etenee aina, vaikka malli kaatuisi kesken (demo ei jää jumiin)."""
    _syy(istunto, "varatoiminta:kysymys")
    kysymys = _avoin_kysymys(istunto)
    if kysymys is None:
        return _lopetusvuoro(istunto, PuhelunLoppu.VALMIS, [_lopetuslause(istunto)])
    istunto.seuraava += 1
    return _sano(istunto, ["Kiitos.", kysymys])


def _avoin_kysymys(istunto: Puheluistunto) -> str | None:
    if istunto.seuraava >= len(istunto.kysymykset):
        return None
    return istunto.kysymykset[istunto.seuraava].teksti


# Kysymysten täytesanat: "Onko uusi lääke sopinut sinulle?" ja "Onko sinua huimannut?"
# eivät ole sama kysymys, vaikka "onko" ja "sinu" osuvat (11.10. arviointi: lista ohitti).
_TAYTESANAT = frozenset(
    {
        "onko",
        "olet",
        "olle",
        "sinu",
        "sinä",
        "mite",
        "mitä",
        "voit",
        "tava",
        "tapa",
        "kuin",
        "tänä",
        "viim",
        "aika",
        "kyll",
        "eikö",
        "että",
        "sitä",
        "sen",
        "jälk",
        "tiet",
        "muka",
    }
)


def _sanavartalot(teksti_: str) -> set[str]:
    """Sisältösanojen alut ("lääke" ja "lääkkeen" ovat sama), täytesanat pois."""
    vartalot = {sana[:4] for sana in re.findall(r"[a-zåäö]+", teksti_.lower()) if len(sana) >= 4}
    return vartalot - _TAYTESANAT


def _kysymyslause(teksti_: str) -> str:
    """Viimeinen kysymyslause: johdanto ("Tietojemme mukaan ...") ei ratkaise vertailua."""
    lauseet = re.findall(r"[^.!?]*\?", teksti_)
    return lauseet[-1] if lauseet else teksti_


def _esitetty(vastaus: str, kysymys: str) -> bool:
    """Kysyikö malli listan kysymyksen omin sanoin: vähintään puolet kysymyslauseen
    sisältösanoista (11.10.: "oletko aloittanut sen käytön?" jäi tunnistamatta)."""
    if "?" not in vastaus:
        return False
    kysymyslause = _kysymyslause(kysymys)
    tarvittavat = _sanavartalot(kysymyslause)
    if not tarvittavat:
        # Pelkkiä täytesanoja ("Miten voit tänään?"): kysymyksen pitää olla mukana sellaisenaan
        # (11.10. testipankki: tunnistamatta jäänyt kysymys kysyttiin kahdesti).
        return _normalisoi(kysymyslause) in _normalisoi(vastaus)
    yhteiset = tarvittavat & _sanavartalot(vastaus)
    return bool(yhteiset) and len(yhteiset) * 2 >= len(tarvittavat)


def _normalisoi(teksti_: str) -> str:
    return " ".join(re.findall(r"[a-zåäö]+", teksti_.lower()))


def _kirjaa_esitetyt(istunto: Puheluistunto, vastaus: str) -> bool:
    """Siirtää kirjanpitoa niin monta kysymystä kuin malli on jo kysynyt (myös useamman
    kerralla tai aiemmin omin sanoin). True = vähintään yksi."""
    edistyi = False
    while (kysymys := _avoin_kysymys(istunto)) is not None and _esitetty(vastaus, kysymys):
        istunto.seuraava += 1
        edistyi = True
    return edistyi


def _eteneminen(istunto: Puheluistunto, puhuttava: str) -> Vuoro:
    """Kysymyslista ei saa unohtua (11.10.: malli jäi kehumaan "joo"-vastauksia eikä
    kysynyt listaa loppuun). Malli kysyi listan kysymyksen -> kirjataan. Muuten, jos
    vastauksessa ei ole kysymystä tai kolme vuoroa on mennyt ilman listaa, seuraava
    kysymys lisätään perään; kun lista on käyty, lopetetaan. Huolen ääreltä ei siirrytä."""
    if _kirjaa_esitetyt(istunto, puhuttava):
        istunto.ilman_edistysta = 0
        _syy(istunto, f"lista:kysytty:{istunto.seuraava}/{len(istunto.kysymykset)}")
        return _sano(istunto, [puhuttava])
    kysymys = _avoin_kysymys(istunto)
    istunto.ilman_edistysta += 1
    jumissa = "?" not in puhuttava or istunto.ilman_edistysta >= 3
    # Oireen jatkokysymys ei ole jumissa olemista (11.10. puhelu: "Pystytkö syömään ja
    # juomaan?" korvattiin ihottumakysymyksellä, kun henkilö kertoi huimauksesta).
    oiretta_selvitetaan = istunto.oire_jatko > 0 and "?" in puhuttava
    if not jumissa or istunto.huoli_jatko > 0 or oiretta_selvitetaan:
        _syy(istunto, "malli")
        return _sano(istunto, [puhuttava])
    if kysymys is None:
        _syy(istunto, "lista:valmis")
        return _lopetusvuoro(istunto, PuhelunLoppu.VALMIS, [puhuttava, _lopetuslause(istunto)])
    istunto.seuraava += 1
    istunto.ilman_edistysta = 0
    _syy(istunto, f"lista:lisatty:{istunto.seuraava}/{len(istunto.kysymykset)}")
    ilman_kysymysta = re.sub(r"[^.!]*\?\s*$", "", puhuttava).strip()
    return _sano(istunto, [ilman_kysymysta, kysymys])


def _tukivastaus(istunto: Puheluistunto, raaka: str | None) -> Vuoro:
    """Tukitila: malli kuuntelee. LOPETUS ohitetaan, jos henkilö puhuu yhä raskaista
    asioista itsetuhoisen puheen jälkeen (Jere)."""
    viimeisin = istunto.tietohavainnot[-1] if istunto.tietohavainnot else Tietopohjahavainto()
    yha_raskasta = bool(
        viimeisin.hata or viimeisin.vakavat or viimeisin.huoli or viimeisin.hata_kielletty
    )
    lopettaa = raaka is not None and LOPETUSMERKKI in raaka
    puhuttava = _puhuttavaksi(istunto, (raaka or "").replace(LOPETUSMERKKI, ""))
    if lopettaa and istunto.itsetuho and yha_raskasta:
        _syy(istunto, "tuki:lopetus_ohitettu")
        return _sano(istunto, [_kiintea(istunto, "tuki_jatko")])
    if lopettaa:
        _syy(istunto, "tuki:lopetus")
        return _lopetusvuoro(istunto, PuhelunLoppu.TUKI, [puhuttava, _lopetuslause(istunto)])
    if not puhuttava:
        _syy(istunto, "tuki:kiintea")
        return _sano(istunto, [_kiintea(istunto, "tuki_jatko")])
    _syy(istunto, "tuki:malli")
    return _sano(istunto, [puhuttava])


def ilman_lyhytta_kuittausta(vuoro: Vuoro) -> Vuoro:
    """Välitäyte ("Okei, merkitsen tämän.") toimi jo kuittauksena: lyhyt kuittaus pois."""
    lyhyet = {"Kiitos.", "Selvä.", "Ymmärrän.", "Okei."}
    if len(vuoro.sanottavat) > 1 and vuoro.sanottavat[0] in lyhyet:
        keskeytymaton = max(0, vuoro.keskeytymaton - 1)
        return Vuoro(
            sanottavat=vuoro.sanottavat[1:], kuuntele=vuoro.kuuntele, keskeytymaton=keskeytymaton
        )
    return vuoro


# --- VAIN DEV: puheentunnistuksen kaikutesti ---

KAIUN_KIERROKSET = 3
KAIUN_AVAUS = (
    "Tämä on puheentunnistuksen testi. Olen tekoäly. Sano jotain, niin kerron mitä kuulin."
)


def kaiun_vuoro(
    kierros: int, puhe: str | None, varmuus: float | None, stt_malli: str | None = None
) -> tuple[Vuoro, KaikuTulos | None]:
    """Kierros 0 = avaus. Sen jalkeen toistaa kuullun, varmuuden ja mahdollisen hatamerkin."""
    if kierros == 0:
        return Vuoro(sanottavat=[KAIUN_AVAUS], kuuntele=True), None

    kuultu = (puhe or "").strip()
    hatamerkki = tunnista_hatamerkki(kuultu) if kuultu else None
    tulos = KaikuTulos(
        kierros=kierros, kuultu=kuultu, varmuus=varmuus, hatamerkki=hatamerkki, stt_malli=stt_malli
    )

    sanottavat = [f"Kuulin: {kuultu}." if kuultu else "En kuullut mitään."]
    if varmuus is not None and kuultu:
        sanottavat.append(f"Varmuus {round(varmuus * 100)} prosenttia.")
    if hatamerkki is not None:
        sanottavat.append(f"Tunnistin hätämerkin: {hatamerkki}.")

    viimeinen = kierros >= KAIUN_KIERROKSET
    sanottavat.append("Testi päättyi. Hei hei." if viimeinen else "Sano jotain uutta.")
    return Vuoro(sanottavat=sanottavat, kuuntele=not viimeinen), tulos
