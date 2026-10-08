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
