"""Pydantic-DTO:t. Tama tiedosto on tiimin valinen sopimus: lisaa kenttia, ala nimea vanhoja."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

# --- Adapterin palauttamat (sisaiset) ---


class Vanhus(BaseModel):
    """Adapterin raakamuoto. Ei palauteta API:sta sellaisenaan (ks. VanhusLyhyt)."""

    vanhus_id: str
    syntymapaiva: date
    kuolinpaiva: date | None
    sukupuoli: str


class Laakejakso(BaseModel):
    """Yksi laakityksen jakso datassa. paattyi=None tarkoittaa etta kaytto jatkuu."""

    koodi: str
    nimi: str
    alkoi: datetime
    paattyi: datetime | None
    syy: str | None
    # WHO ATC -koodi (yleensa 7 merkkia = vaikuttava aine). Tuotannossa valmisteen tieto
    # Kannasta; demossa adapterin vastaavuustaulusta. None = ei tiedossa.
    atc: str | None = None


# --- API:n palauttamat ---


class VanhusLyhyt(BaseModel):
    """Pseudonymisoitu: ei nimea, henkilotunnusta eika osoitetta."""

    vanhus_id: str
    ika: int
    sukupuoli: str


class Laake(Laakejakso):
    kaytossa: bool


class MuutosTyyppi(StrEnum):
    UUSI_LAAKE = "UUSI_LAAKE"
    LOPETETTU_LAAKE = "LOPETETTU_LAAKE"
    ANNOSMUUTOS = "ANNOSMUUTOS"
    LYHYT_KUURI = "LYHYT_KUURI"  # alkoi ja paattyi ikkunassa, esim. antibioottikuuri


class Laakemuutos(BaseModel):
    tyyppi: MuutosTyyppi
    laake: Laake
    aiempi_laake: Laake | None = None  # vain ANNOSMUUTOS
    tapahtui: datetime
    paivia_sitten: int
    kuvaus: str


class PotilaanLaakemuutokset(BaseModel):
    vanhus_id: str
    viitepaiva: datetime
    ikkuna_paivia: int
    aktiiviset_laakkeet: list[Laake]
    muutokset: list[Laakemuutos]
    ika: int | None = None  # viitepaivana


# --- Dev: testimuutokset demodataan ---


class Testimuutos(BaseModel):
    """Dev-pyynto: tee kantaan laakemuutos, jonka tunnistuksen pitaa huomata."""

    __test__ = False  # pytest: ei testiluokka, vaikka nimi alkaa "Test"

    vanhus_id: str
    tyyppi: MuutosTyyppi
    nimi: str | None = Field(None, description="Uuden laakkeen nimi (UUSI_LAAKE, ANNOSMUUTOS)")
    koodi: str | None = Field(None, description="Uuden laakkeen koodi; oletus generoidaan")
    lopetettava_koodi: str | None = Field(
        None, description="Kaytossa olevan laakkeen koodi (LOPETETTU_LAAKE, ANNOSMUUTOS)"
    )
    paivia_sitten: int = Field(3, ge=0, le=3650, description="Muutoksen paiva viitepaivasta")

    @model_validator(mode="after")
    def tarkista_tyypin_kentat(self) -> "Testimuutos":
        if self.tyyppi == MuutosTyyppi.LYHYT_KUURI:
            raise ValueError(
                "LYHYT_KUURI ei ole testimuutos: tee UUSI_LAAKE ja LOPETETTU_LAAKE erikseen"
            )
        tarvitsee_nimen = self.tyyppi in (MuutosTyyppi.UUSI_LAAKE, MuutosTyyppi.ANNOSMUUTOS)
        tarvitsee_lopetettavan = self.tyyppi in (
            MuutosTyyppi.LOPETETTU_LAAKE,
            MuutosTyyppi.ANNOSMUUTOS,
        )
        if tarvitsee_nimen and not self.nimi:
            raise ValueError(f"{self.tyyppi} vaatii kentan 'nimi'")
        if tarvitsee_lopetettavan and not self.lopetettava_koodi:
            raise ValueError(f"{self.tyyppi} vaatii kentan 'lopetettava_koodi'")
        return self


class TestimuutostenNollaus(BaseModel):
    __test__ = False

    palautettuja_lopetuksia: int
    poistettuja_laakkeita: int


# --- Kysymykset (kysymykset/-moduulin sopimus) ---


class Kysymys(BaseModel):
    """Kysyttava asia. LLM muotoilee lauseen ja kay keskustelun omien ohjeidensa mukaan;
    backend ei ohjaa keskustelun kulkua eika kasittele vastauksia."""

    tunniste: str  # vakaa avain, esim. "UUSI_LAAKE", "tarkennus.huimaus", "vointi"
    teksti: str
    muutos_tyyppi: MuutosTyyppi | None = None  # None = yleinen, ei sidottu muutokseen


class Muutoskonteksti(BaseModel):
    tyyppi: MuutosTyyppi
    laakeryhma: str | None  # esim. "verenohennus"; None = ryhma ei tiedossa
    paivia_sitten: int


class Puhelukonteksti(BaseModel):
    """LLM:lle annettava tausta henkilosta. Datan minimointi: ei nimea, ei laakkeen nimea,
    ei diagnoosia; vain mita kysymysten ymmartaminen vaatii."""

    ika: int | None
    aktiivisia_laakkeita: int
    muutokset: list[Muutoskonteksti]  # uusin ensin


# --- Puhelutehtavat (soittojono) ---


class PuhelutehtavanTila(StrEnum):
    ODOTTAA = "ODOTTAA"
    SOITETAAN = "SOITETAAN"
    VALMIS = "VALMIS"
    EI_VASTANNUT = "EI_VASTANNUT"
    PERUTTU = "PERUTTU"


class Puhelutehtava(BaseModel):
    """Soittojonon rivi: kenelle (pseudonyymi), miksi, missa jarjestyksessa ja milla kysymyksilla.

    Ei puhelinnumeroa: se annetaan vain puheluosalle soiton ajaksi (ks. Soittotiedot).
    """

    id: str
    vanhus_id: str
    tila: PuhelutehtavanTila
    prioriteetti: int  # soittojarjestys, suurin ensin. EI triage-taso.
    perusteet: list[str]  # miksi prioriteetti on tama (hoitajalle)
    ika: int | None
    aktiivisia_laakkeita: int
    viitepaiva: datetime
    yritykset: int
    muutokset: list[Laakemuutos]  # uusin ensin (hoitajalle, sisaltaa laakkeiden nimet)
    kysymykset: list[Kysymys]  # LLM:lle
    konteksti: Puhelukonteksti  # LLM:lle
    luotu: datetime
    paivitetty: datetime


class Soittotiedot(BaseModel):
    """Vain puheluosalle soiton ajaksi: numero + LLM:lle menevat kysymykset ja konteksti."""

    tehtava_id: str
    puhelinnumero: str
    kysymykset: list[Kysymys]
    konteksti: Puhelukonteksti


class PuhelunAloitus(BaseModel):
    """Kaynnistetty soitto. Ei puhelinnumeroa (datan minimointi); repliikit debuggausta varten."""

    puhelu_sid: str  # Twilion CallSid, loytyy Twilio Consolen lokeista
    tehtava_id: str
    repliikit: list[str]  # mita puhelussa sanotaan, jarjestyksessa


class PuhelunLoppu(StrEnum):
    VALMIS = "VALMIS"  # kaikki kysymykset kayty
    HATAMERKKI = "HATAMERKKI"  # saanto tunnisti hatamerkin, ohjattiin 112:een
    HALUAA_HOITAJAN = "HALUAA_HOITAJAN"  # vanhus pyysi ihmista
    EI_LUPAA = "EI_LUPAA"  # ei aikaa nyt
    EI_VASTAUSTA = "EI_VASTAUSTA"  # vastasi puhelimeen, mutta ei sanonut mitaan
    KESKEYTYI = "KESKEYTYI"  # puhelu katkesi kesken
    EI_VASTANNUT = "EI_VASTANNUT"  # ei vastannut puhelimeen (no-answer, busy, failed)
    LOPETTI = "LOPETTI"  # henkilö halusi lopettaa kesken (lopetuspyyntö tai loukkaukset)
    TUKI = "TUKI"  # vakava asia: tukitila (kuuntelu ja turvallisuus), hoitaja soittaa tänään


class Havainto(BaseModel):
    """Vastauksesta loytynyt asia triagelle: oire, mieliala tai muu huoli tarkennuksineen.
    lahde "malli" = LLM:n arvio, "saanto" = saantotunnistin (malli ei loytanyt)."""

    tyyppi: str  # oire | mieliala | toimintakyky | laakkeenotto | turvallisuus | muu
    kuvaus: str  # perusmuodossa, esim. "vatsakipu"
    kielletty: bool = False  # "ei ole vatsakipua"
    sijainti: str | None = None
    alkoi: str | None = None
    voimakkuus: str | None = None
    tilanne: str | None = None
    vaikutus: str | None = None
    lahde: str = "malli"


class Tarkennus(BaseModel):
    kysymys: str
    vastaus: str = ""  # tyhja = ei vastattu (hiljaisuus tai puhelu paattyi)


class KysymyksenVastaus(BaseModel):
    tunniste: str
    kysymys: str  # sellaisena kuin se sanottiin
    vastaus: str  # puheentunnistuksen tulos; tyhja = ei vastausta
    varmuus: float | None = None  # Twilion Confidence 0..1
    # Matalan varmuuden vastaus pyydetaan toistamaan kerran; toisto tallentuu tahan.
    selvennys: str | None = None
    # True = tunnistus jai epavarmaksi (tai vastausta ei saatu). Triagen pitaa nostaa
    # vahintaan keltaiseksi: epavarmuus siirtyy aina ihmiselle.
    epavarma: bool = False
    # True = henkilo kysyi laakeneuvoa (esim. "pitaisiko lopettaa"); agentti ei vastannut,
    # vaan lupasi valittaa hoitajalle. Hoitajan on vastattava.
    kysymys_hoitajalle: bool = False
    # Arvio + paatos (kerrokset 1-2): mita vastauksesta loytyi ja miten se tulkittiin.
    # tulkinta: "ongelma" | "kunnossa" | "epaselva"; None = ei arvioitu (esim. hiljaisuus).
    havainnot: list["Havainto"] = Field(default_factory=list)
    tulkinta: str | None = None
    # Agentin tarkennukset samaan aiheeseen (enintaan MAX_TARKENNUKSET per kysymys):
    # epaselva vastaus, oire jota tarkennetaan tai henkilon oma kysymys valissa.
    tarkennukset: list["Tarkennus"] = Field(default_factory=list)


class Kiireellisyys(StrEnum):
    VIHREA = "vihreä"  # kirjataan
    KELTAINEN = "keltainen"  # hoitaja soittaa tänään
    PUNAINEN = "punainen"  # heti


class OireYhteenvedossa(BaseModel):
    oire: str
    voimakkuus: str = "ei tiedossa"  # lievä | kohtalainen | voimakas | ei tiedossa
    alkoi_laakkeen_jalkeen: str = "ei tiedossa"  # kyllä | ei | ei tiedossa
    tilanne: str = ""
    varmuus: str = "epäselvä"  # selvä | epäselvä


class Saantoloydokset(BaseModel):
    """Mitä säännöt tunnistivat puhelusta (deterministinen, ei LLM:ää)."""

    taso: Kiireellisyys
    perusteet: list[str] = Field(default_factory=list)  # miksi tämä taso, selkokielellä
    hatamerkit: list[str] = Field(default_factory=list)
    hatamerkit_kieltomuodossa: list[str] = Field(default_factory=list)
    vakavat_huolet: list[str] = Field(default_factory=list)
    huolen_ilmaukset: list[str] = Field(default_factory=list)
    huomiosanat: list[str] = Field(default_factory=list)
    tunnistetut_oireet: list[str] = Field(default_factory=list)
    huolestuttavat_oireet: list[str] = Field(default_factory=list)
    loukkaavia_puheenvuoroja: int = 0
    uhkauksia: int = 0


class Yhteenveto(BaseModel):
    """Hoitajalle: puhelun triage (Jeren analyysi). Taso = korkeampi säännön ja mallin
    tasosta (sääntö voi vain nostaa). Tämä yhteenveto tallennetaan; litterointia ei."""

    taso: Kiireellisyys
    saanto: Saantoloydokset
    mallin_taso: Kiireellisyys | None = None  # None = analyysi kesken tai epäonnistui
    analyysi_valmis: bool = False
    varayhteenveto: bool = False  # malli epäonnistui: yhteenveto pelkillä säännöillä
    yleisvointi: str = ""
    oireet: list[OireYhteenvedossa] = Field(default_factory=list)
    turvallisuushuolet: list[str] = Field(default_factory=list)
    tunnetila: str = ""
    annetut_ohjeet: list[str] = Field(default_factory=list)
    laakkeen_kaytto: str = "ei tiedossa"  # otettu ohjeen mukaan: kyllä | ei | ei tiedossa
    laakkeen_huomiot: str = ""
    lopettamisen_syy: str = ""
    huolet: list[str] = Field(default_factory=list)
    muuta_hoitajalle: str = ""
    perustelu: str = ""


class PuhelunTulos(BaseModel):
    """Puhelun tulos; vastaukset ovat muistissa, mutta kantaan tallentuu vain yhteenveto."""

    istunto_id: str
    tehtava_id: str
    loppu: PuhelunLoppu | None = None  # None = puhelu kesken
    hatamerkki: str | None = None  # hatamerkin luokka, esim. "rintakipu"
    vastaukset: list[KysymyksenVastaus] = Field(default_factory=list)
    paattyi: datetime | None = None
    tukitila: str | None = None  # vakava asia, jonka takia puhelu jäi tukitilaan
    yhteenveto: Yhteenveto | None = None  # triage; sääntötaso heti, mallin analyysi perässä


class TallennettuTulos(BaseModel):
    """Puhelun tulos kannasta hoitajan näkymälle: vain yhteenveto, ei vastauksia."""

    istunto_id: str
    tehtava_id: str
    loppu: PuhelunLoppu | None = None
    paattyi: datetime | None = None  # None = ei tiedossa, tehtävän tuloksista vanhin
    yhteenveto: Yhteenveto | None = None  # None = yhteenveto ei vielä saatavilla


class KaikuTulos(BaseModel):
    """VAIN DEV: puheentunnistuksen testi. Mita Twilio kuuli ja osuiko hatasana."""

    kierros: int
    kuultu: str
    varmuus: float | None
    hatamerkki: str | None
    stt_malli: str | None = None  # millä mallilla tunnistettiin (vertailua varten)


class Yhteystieto(BaseModel):
    puhelinnumero: str = Field(
        pattern=r"^\+[1-9]\d{6,14}$", description="E.164, esim. +358401234567"
    )


class TunnistusPyynto(BaseModel):
    viitepaiva: datetime | None = Field(None, description="Oletus: datan uusin paiva")
    ikkuna_paivia: int = Field(90, ge=1, le=3650)
    min_ika: int = Field(65, ge=0, le=120)


class TunnistuksenTulos(BaseModel):
    viitepaiva: datetime
    luotuja: int
    paivitettyja: int
    uusia_muutoksia: int


class TilanMuutos(BaseModel):
    tila: PuhelutehtavanTila


class PuhelutehtavienNollaus(BaseModel):
    poistettuja_puhelutehtavia: int
