"""Kannan kysymysten kirjanpito puhelussa: mitä listalta on kysytty ja milloin seuraava
kysymys lisätään mallin vastauksen perään. Malli saa seuraavan kysymättömän kysymyksen
piilo-ohjeena (keskustelu._piilo_ohjeet); tämä varmistaa, ettei lista unohdu.
"""

import re

from app.mallit import PuhelunLoppu
from app.puhelu.istunto import (
    Puheluistunto,
    Vuoro,
    kirjaa_syy,
    lopetuslause,
    lopetusvuoro,
    sano,
)
from app.puhelu.lopetus import ilman_kiitoksia


def avoin_kysymys(istunto: Puheluistunto) -> str | None:
    """Seuraava kysymätön listan kysymys; None = lista on käyty."""
    if istunto.seuraava >= len(istunto.kysymykset):
        return None
    return istunto.kysymykset[istunto.seuraava].teksti


def _kirjaa_kysytyksi(istunto: Puheluistunto) -> None:
    """Seuraava listan kysymys on nyt kysytty: henkilön seuraava vastaus on vastaus siihen
    (lääkemuutoksen poikkeama, keskustelu._tunnista_laakepoikkeama)."""
    istunto.viimeksi_kysytty = istunto.kysymykset[istunto.seuraava]
    istunto.seuraava += 1


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
    if kysymys.startswith("Viime puhelussa"):
        # Malli saa muotoilla seurantakysymyksen luontevasti. Oireen vartalon osuma riittää,
        # jotta backend ei lisää samaa kysymystä uudelleen eri sanoin kysytyn perään.
        kuvaus = kysymys.split("oli", 1)[-1].split(".", 1)[0]
        return bool(_sanavartalot(kuvaus) & _sanavartalot(vastaus))
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
    while (kysymys := avoin_kysymys(istunto)) is not None and _esitetty(vastaus, kysymys):
        _kirjaa_kysytyksi(istunto)
        edistyi = True
    return edistyi


def eteneminen(istunto: Puheluistunto, puhuttava: str) -> Vuoro:
    """Kysymyslista ei saa unohtua (11.10.: malli jäi kehumaan "joo"-vastauksia eikä
    kysynyt listaa loppuun). Malli kysyi listan kysymyksen -> kirjataan. Muuten, jos
    vastauksessa ei ole kysymystä tai kolme vuoroa on mennyt ilman listaa, seuraava
    kysymys lisätään perään; kun lista on käyty, lopetetaan. Huolen ääreltä ei siirrytä."""
    if _kirjaa_esitetyt(istunto, puhuttava):
        istunto.ilman_edistysta = 0
        kirjaa_syy(istunto, f"lista:kysytty:{istunto.seuraava}/{len(istunto.kysymykset)}")
        return sano(istunto, [puhuttava])
    kysymys = avoin_kysymys(istunto)
    istunto.ilman_edistysta += 1
    jumissa = "?" not in puhuttava or istunto.ilman_edistysta >= 3
    # Oireen jatkokysymys ei ole jumissa olemista (11.10. puhelu: "Pystytkö syömään ja
    # juomaan?" korvattiin ihottumakysymyksellä, kun henkilö kertoi huimauksesta).
    oiretta_selvitetaan = istunto.oire_jatko > 0 and "?" in puhuttava
    if not jumissa or istunto.huoli_jatko > 0 or oiretta_selvitetaan:
        kirjaa_syy(istunto, "malli")
        return sano(istunto, [puhuttava])
    if kysymys is None:
        kirjaa_syy(istunto, "lista:valmis")
        loppusanat = [ilman_kiitoksia(puhuttava), lopetuslause(istunto)]
        return lopetusvuoro(istunto, PuhelunLoppu.VALMIS, loppusanat)
    _kirjaa_kysytyksi(istunto)
    istunto.ilman_edistysta = 0
    kirjaa_syy(istunto, f"lista:lisatty:{istunto.seuraava}/{len(istunto.kysymykset)}")
    if kysymys.startswith("Viime puhelussa"):
        # Muistikysymys puhutaan sellaisenaan. Mallin puolikas johdanto muodosti aiemmin
        # repliikin "Miten se on Viime puhelussa..." ja kysyi saman asian kahdesti.
        return sano(istunto, [kysymys])
    ilman_kysymysta = re.sub(r"[^.!]*\?\s*$", "", puhuttava).strip()
    return sano(istunto, [ilman_kysymysta, kysymys])


def varakysymys(istunto: Puheluistunto) -> Vuoro:
    """Ei mallia, virhe tai aikakatkaisu: kannan kysymykset järjestyksessä ja lopetus.
    Puhelu etenee aina, vaikka malli kaatuisi kesken (demo ei jää jumiin)."""
    kirjaa_syy(istunto, "varatoiminta:kysymys")
    kysymys = avoin_kysymys(istunto)
    if kysymys is None:
        return lopetusvuoro(istunto, PuhelunLoppu.VALMIS, [lopetuslause(istunto)])
    _kirjaa_kysytyksi(istunto)
    return sano(istunto, ["Kiitos.", kysymys])
