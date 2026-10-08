"""Kysymyssisalto: kysymystekstit ja laakeryhmat. Ladataan kannasta (varasto.py).

Sisaltoa ei kirjoiteta koodiin: muokkaus SQL:lla tauluihin muutoskysymys, tarkennuskysymys,
yleiskysymys, laakeryhma, laakeryhman_atc, laakeryhman_avainsana ja laakeryhman_tarkennus.

Laakeryhma tunnistetaan WHO ATC -koodista (tuotannon tapa: Kannan valmistetieto). Pisin osuva
etuliite voittaa (C01AA sydanglykosidit ennen C01 sydanlaakkeet). Jos ATC on tiedossa mutta mikaan
ryhma ei osu, ryhmaa ei ole: nimeen ei turvauduta, koska ATC on luotettavampi. Nimen avainsanat
ovat varakeino VAIN kun ATC puuttuu.

Riskiluokka (korkea_riski) ja tarkennukset EIVAT ole laaketieteellisesti tarkistettuja.
Tuotannossa riskiluokka johdetaan Fimean Laake 75+ -luokituksesta ja kliinisesta hyvaksynnasta.
"""

from dataclasses import dataclass

from app.mallit import MuutosTyyppi
from app.muutokset.laakkeet import ainesosa


@dataclass(frozen=True)
class Laakeryhma:
    nimi: str
    avainsanat: tuple[str, ...]  # varakeino: osuma ainesosaan, pienilla kirjaimilla
    tarkennusaiheet: tuple[str, ...]  # tarkeysjarjestyksessa
    korkea_riski: bool
    atc_etuliitteet: tuple[str, ...] = ()  # ensisijainen tunnistus


@dataclass(frozen=True)
class Kysymyssisalto:
    muutoskysymykset: dict[MuutosTyyppi, str]
    tarkennuskysymykset: dict[str, str]  # aihe -> teksti
    yleiskysymykset: tuple[tuple[str, str], ...]  # (tunniste, teksti) jarjestyksessa
    laakeryhmat: tuple[Laakeryhma, ...]  # jarjestyksessa: ensimmainen osuma voittaa

    def tunnista_ryhma(self, laakkeen_nimi: str, atc: str | None = None) -> Laakeryhma | None:
        if atc:
            return self._ryhma_atc_koodista(atc.strip().upper())
        return self._ryhma_nimesta(laakkeen_nimi)

    def _ryhma_atc_koodista(self, atc: str) -> Laakeryhma | None:
        paras: Laakeryhma | None = None
        paras_pituus = 0
        for ryhma in self.laakeryhmat:
            for etuliite in ryhma.atc_etuliitteet:
                osuu = atc.startswith(etuliite)
                pidempi = len(etuliite) > paras_pituus
                if osuu and pidempi:
                    paras = ryhma
                    paras_pituus = len(etuliite)
        return paras

    def _ryhma_nimesta(self, laakkeen_nimi: str) -> Laakeryhma | None:
        aine = ainesosa(laakkeen_nimi)
        for ryhma in self.laakeryhmat:
            if any(avainsana in aine for avainsana in ryhma.avainsanat):
                return ryhma
        return None
