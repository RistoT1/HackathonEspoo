"""Puhelun istunto: tila puhelun ajan sekä repliikkien ja syiden kirjaus. Tilakone on
keskustelu.py:ssä; se ja kysymyslista.py kirjaavat repliikit tämän kautta.

Ei Twiliota eikä kantaa: testattavissa suoraan.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from app.mallit import (
    KysymyksenVastaus,
    Kysymys,
    MuutosTyyppi,
    OireenSeurannanTila,
    Oireseuranta,
    Puhelukonteksti,
    PuhelunLoppu,
    PuhelunTulos,
)
from app.muisti.seuranta import MUISTIKYSYMYS_ETULIITE, luo_seurantakysymys
from app.puhelu.tietopohja import (
    Tietopohjahavainto,
    huolet,
    jarjestelmaohje,
    puhelutyypit,
    teksti,
    tukitilan_ohje,
)
from app.triage.analyysi import saantoyhteenveto
from app.triage.saannot import saantoloydokset

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
    seurantapuhelu: bool = False  # lääkeasiat on jo kysytty; vain lyhyt vointitarkistus
    rajat: Rajat = field(default_factory=Rajat)
    puhelu_sid: str | None = None  # Twilion CallSid: relay-websocket tarkistaa sen
    vaihe: Vaihe = Vaihe.ALKU
    # Keskustelu viesteinä (Jere: history). Vain muistissa; analyysi lukee sen ja se häviää.
    # content on sanottu sellaisenaan; henkilön viestissä voi olla lisäksi "tietopohja",
    # piilo-ohje, joka liitetään sen perään vain mallille (keskustelu._mallille).
    historia: list[dict[str, str]] = field(default_factory=list)
    vastaukset: list[KysymyksenVastaus] = field(default_factory=list)
    tietohavainnot: list[Tietopohjahavainto] = field(default_factory=list)
    # Lääkemuutoksen poikkeama (ei noudateta) triageen: tietohavainnot-indeksi -> peruste.
    laakepoikkeamat: dict[int, str] = field(default_factory=dict)
    oireseurannat: list[Oireseuranta] = field(default_factory=list)
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
    # Listan kysymys, joka kysyttiin henkilön edellisen vastauksen jälkeen: seuraava vastaus
    # on vastaus siihen (lääkemuutoksen poikkeama). Tyhjenee, kun henkilö vastaa.
    viimeksi_kysytty: Kysymys | None = None
    ilman_edistysta: int = 0  # mallin vuorot ilman listan kysymystä
    valivastauksia: int = 0  # sanotut välivastaukset (vaihtelu)
    lammitetty: bool = False  # järjestelmäohje lämmitettiin jo soiton alkaessa
    avausteksti: str = ""  # valittu avauksen versio (sama lämmityksessä ja puhelussa)
    oire_jatko: int = 0  # vuorot, joina oiretta käsitellään ennen listan jatkamista
    avoimet_oireet: list[str] = field(default_factory=list)  # käsiteltävänä olevat oireet
    malli_lammin: bool = False  # lämmitys valmis (siihen asti varatoiminta, ei odotusta)
    lista_lopetus_ohitettu: bool = False  # mallin LOPETUS ohitettiin kerran (lista kesken)
    # Lokia varten: tämän vuoron mittaukset ja päätösten syyt.
    mittaukset: dict = field(default_factory=dict)
    syyt: list[str] = field(default_factory=list)
    viimeksi_vastattu: float | None = None
    viimeksi_sanottu_ms: int = 0
    loppu: PuhelunLoppu | None = None
    hatamerkki: str | None = None
    paattyi: datetime | None = None

    def __post_init__(self) -> None:
        """Edellisen oireen seuranta on oikea listakysymys, ei vain mallille annettu toive."""
        seurantakysymys = luo_seurantakysymys(self.konteksti.edellinen_puhelu)
        jo_listalla = any(
            kysymys.tunniste.startswith(MUISTIKYSYMYS_ETULIITE) for kysymys in self.kysymykset
        )
        if seurantakysymys is not None and not jo_listalla:
            self.kysymykset = [seurantakysymys, *self.kysymykset]

    def merkitse_malli_lammin(self) -> None:
        self.malli_lammin = True

    def valitse_avaus(self) -> str:
        """Avauksen versio valitaan kerran (soiton alkaessa), jotta lämmitys ja puhelu
        käyttävät samaa tekstiä."""
        if not self.avausteksti:
            if self.seurantapuhelu and self.kysymykset:
                kysymys = self.kysymykset[0]
                self.viimeksi_kysytty = kysymys
                self.seuraava = 1
                self.avausteksti = "Hei, olen hyvinvointialueen tekoälyavustaja. " + kysymys.teksti
            else:
                self.avausteksti = kiintea(self, "avauslause")
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
        return jarjestelmaohje(
            self.konteksti.ika, muutokset, kysymykset, self.konteksti.edellinen_puhelu
        )

    def tulos(self) -> PuhelunTulos:
        """Tulos ja sääntöjen triage heti (LLM-analyysi päivittää yhteenvedon myöhemmin)."""
        edellinen = self.konteksti.edellinen_puhelu
        saanto = saantoloydokset(
            self.tietohavainnot,
            self.vastaukset,
            self.loppu,
            self.hatamerkki,
            self.tukitila,
            self.laakepoikkeamat,
            edelliset_oireet=edellinen.oireet if edellinen else (),
            oireseurannat=self.oireseurannat,
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
        """Puhelu tekstinä analyysia varten: sanottu ilman piilo-ohjeita. Vain muistissa."""
        rivit = []
        for viesti in self.historia:
            puhuja = "Avustaja" if viesti["role"] == "assistant" else "Henkilö"
            rivit.append(f"{puhuja}: {viesti['content']}")
        return "\n".join(rivit)


def lopeta(istunto: Puheluistunto, loppu: PuhelunLoppu) -> None:
    """Merkitsee puhelun paattyneeksi. Ensimmainen syy jaa voimaan."""
    if istunto.loppu is not None:
        return
    istunto.loppu = loppu
    istunto.vaihe = Vaihe.LOPPU
    istunto.paattyi = datetime.now(UTC)


def kirjaa_syy(istunto: Puheluistunto, syy: str) -> None:
    istunto.syyt.append(syy)


def kiintea(istunto: Puheluistunto, avain: str) -> str:
    return teksti(avain, istunto.versiot)


def sano(istunto: Puheluistunto, sanottavat: list[str], kuuntele: bool = True) -> Vuoro:
    """Repliikki historiaan ja puhuttavaksi (Jere: puhu)."""
    ei_tyhjat = [osa for osa in sanottavat if osa and osa.strip()]
    if ei_tyhjat:
        istunto.historia.append({"role": "assistant", "content": " ".join(ei_tyhjat)})
    return Vuoro(sanottavat=ei_tyhjat, kuuntele=kuuntele)


def lopetusvuoro(istunto: Puheluistunto, loppu: PuhelunLoppu, sanottavat: list[str]) -> Vuoro:
    lopeta(istunto, loppu)
    return sano(istunto, sanottavat, kuuntele=False)


def huolestuttava(istunto: Puheluistunto) -> bool:
    """Puhelussa on huolestuttava löydös: lopetusviesti lupaa hoitajan soiton tänään."""
    loydetyt = huolet(istunto.tietohavainnot, istunto.epaselvia)
    seuranta_vaatii_hoitajan = any(
        seuranta.tila
        in (
            OireenSeurannanTila.ENNALLAAN,
            OireenSeurannanTila.PAHENTUNUT,
            OireenSeurannanTila.EPASELVA,
        )
        for seuranta in istunto.oireseurannat
    )
    return bool(loydetyt or istunto.laakepoikkeamat or seuranta_vaatii_hoitajan)


def lopetuslause(istunto: Puheluistunto) -> str:
    """Huolestuttava löydös (keltainen säännöillä): hoitaja soittaa tänään, ei "tarvittaessa"
    (12.10. puhelu: lopetetun lääkkeen jatkaminen sai yleisen lopetuksen)."""
    if istunto.tukitila:
        return kiintea(istunto, "lopetuslause_tuki")
    avain = "lopetuslause_huolestuttava" if huolestuttava(istunto) else "lopetuslause"
    return kiintea(istunto, avain)
