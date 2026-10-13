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

Osat: istunto.py (tila ja repliikkien kirjaus), kysymyslista.py (kannan kysymysten
kirjanpito), lopetus.py (milloin mallin LOPETUS ohitetaan). Ei Twiliota eikä kantaa:
testattavissa suoraan.
"""

import asyncio
import copy
import logging
import re
import time
from dataclasses import dataclass, field, fields

from app.mallit import KysymyksenVastaus, PuhelunLoppu
from app.muisti.seuranta import luokittele_seuranta, on_seurantakysymys
from app.puhelu.agentti import Keskusteluagentti
from app.puhelu.istunto import (
    Puheluistunto,
    Rajat,
    Vaihe,
    Vuoro,
    huolestuttava,
    kiintea,
    kirjaa_syy,
    lopeta,
    lopetuslause,
    lopetusvuoro,
    sano,
)
from app.puhelu.kielentarkistus import Tarkistin, korjaa_kieli
from app.puhelu.kysymyslista import avoin_kysymys, eteneminen, varakysymys
from app.puhelu.lopetus import (
    Esto,
    ilman_kiitoksia,
    lopetuksen_esto,
    yhtenaista_lopetusmerkki,
)
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
    huudahdus,
    jaa_lauseiksi,
    jarkyttava_oire,
    kotihoito_puheeksi,
    laakemuutoksen_poikkeama,
    muutoksen_jalkeen,
    oireohje_kotihoidolla,
    on_myontava,
    piilo_ohje,
    puhelun_vuororaja,
    siisti_vastaus,
    sisaltaa_kotihoidon,
    viimeisin_kysymys,
)
from app.puhelu.tietopohja import saannot as tietopohjan_saannot

# Tilakoneen rajapinta kutsujille (palvelu, relay, istunnot, lämmitys): istunnon tyypit
# ovat istunto.py:ssä, mutta kutsujat käyttävät niitä tämän moduulin kautta.
__all__ = [
    "EI_KUULUNUT",
    "HOITAJALLE",
    "LAAKENEUVO_VASTAUS",
    "Puheluistunto",
    "Rajat",
    "Vaihe",
    "Vuoro",
    "aloita",
    "ilman_lyhytta_kuittausta",
    "kasittele",
    "lopeta",
    "palauta",
    "tilannekuva",
]

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

# Peräkkäiset hiljaiset vuorot, joiden jälkeen puhelu lopetetaan.
MAX_HILJAISET = 2
# Montako viimeisintä vuoroparia mallille annetaan (Jere: MAX_TURNS).
MAX_VUOROPARIT = 10


@dataclass(frozen=True)
class Vuorokonteksti:
    """Vain tämän vuoron tiedot: lasketaan joka vuorolla, eivät jää istuntoon."""

    henkilo_kysyi: bool  # henkilön puheessa oli kysymys
    laake_poikkeama: bool  # vastaus juuri kysyttyyn muutoskysymykseen: ei noudateta
    lopuillaan: bool  # puhelun vuororaja lähellä: malli saa lopettaa listan kesken


@dataclass(frozen=True)
class Piiloohjeet:
    """Mallin ohjeet henkilön viestin perään. Tietopohja jää historiaan (malli antaa
    kotihoito-ohjeet myöhemmällä vuorolla, Jere). Hetkelliset "(Ohje: ...)" näkyvät vain
    tässä vuorossa: vanhat "kysy seuraavaksi X" kertyivät historiaan ja olivat
    ristiriidassa uusien kanssa."""

    tietopohja: str = ""
    hetkelliset: str = ""
    kotihoito_oireet: list[str] = field(default_factory=list)  # kotihoito-ohje tässä vuorossa


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


def aloita(istunto: Puheluistunto, agentti: Keskusteluagentti) -> Vuoro:
    """Avaus: AI-ilmoitus ja kuulumiset (versio vaihtelee). Malli lämpiää avauksen ajan,
    jotta järjestelmäohje on välimuistissa ensimmäiseen vastaukseen mennessä."""
    istunto.vaihe = Vaihe.KESKUSTELU
    kirjaa_syy(istunto, "avaus")
    if not istunto.lammitetty:
        try:
            asyncio.get_running_loop().create_task(agentti.lammita(istunto.lammitysviestit()))
        except RuntimeError:
            pass  # ei tapahtumasilmukkaa: lämmitys ohitetaan
    vuoro = sano(istunto, [istunto.valitse_avaus()])
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
    tieto = saannot.tieto
    istunto.tietohavainnot.append(tieto)
    laake_poikkeama = _tallenna_kysymysvastaus(istunto, teksti_)
    konteksti = Vuorokonteksti(
        henkilo_kysyi=saannot.kysymys,
        laake_poikkeama=laake_poikkeama,
        lopuillaan=raja is not None and istunto.vuoro >= raja,
    )

    turvallisuus = _turvallisuusvuoro(istunto, saannot, teksti_)
    if turvallisuus is not None:
        return turvallisuus
    if istunto.vaihe == Vaihe.TUKI:
        return await _mallin_vuoro(istunto, agentti, konteksti, Piiloohjeet(), tarkistin)
    if raja is not None and istunto.vuoro > raja:
        kirjaa_syy(istunto, f"vuororaja:{raja}")
        return lopetusvuoro(istunto, PuhelunLoppu.VALMIS, [lopetuslause(istunto)])

    saantovastaus = _saantovastaus(istunto, saannot)
    if saantovastaus is not None:
        return saantovastaus

    selkeys = _selkeys(istunto)
    if isinstance(selkeys, Vuoro):
        return selkeys
    ohjeet = _piilo_ohjeet(istunto, tieto, konteksti, selkeys)
    return await _mallin_vuoro(istunto, agentti, konteksti, ohjeet, tarkistin)


def _tallenna_kysymysvastaus(istunto: Puheluistunto, teksti_: str) -> bool:
    """Liitä vastaus backendin viimeksi kysymään muistiseuranta- tai listakysymykseen.

    Paluuarvo kertoo, poikkesiko vastaus lääkemuutoksesta. 12.10.
    puhelu: "mulla on niitä ylimääräisenä, niin mä jatkanut" -> sääntö ei tunnistanut
    (vain mallin analyysi jälkikäteen). Ei lääkeneuvoja eikä syyn kyselyä: hoitaja soittaa.
    Löydös nostaa triagen keltaiseksi (istunto.laakepoikkeamat -> saantoloydokset)."""
    kysytty = istunto.viimeksi_kysytty
    istunto.viimeksi_kysytty = None  # vain tämä vastaus on vastaus siihen
    if on_seurantakysymys(kysytty) and istunto.konteksti.edellinen_puhelu is not None:
        seuranta = luokittele_seuranta(kysytty, teksti_, istunto.konteksti.edellinen_puhelu)
        istunto.oireseurannat.append(seuranta)
        kirjaa_syy(istunto, f"muisti:seuranta:{seuranta.oire}:{seuranta.tila.value}")
        return False
    peruste = laakemuutoksen_poikkeama(kysytty.tunniste, teksti_) if kysytty else None
    if peruste is None:
        return False
    kirjaa_syy(istunto, f"laake:poikkeama:{kysytty.tunniste}")
    taman_vuoron_havainto = len(istunto.tietohavainnot) - 1
    istunto.laakepoikkeamat[taman_vuoron_havainto] = peruste
    return True


def _hiljainen_vuoro(istunto: Puheluistunto) -> Vuoro:
    istunto.hiljaisia += 1
    if istunto.hiljaisia < MAX_HILJAISET:
        kirjaa_syy(istunto, "hiljaisuus:toisto")
        kysymys = viimeisin_kysymys(istunto.historia)
        return Vuoro(sanottavat=[EI_KUULUNUT, kysymys] if kysymys else [EI_KUULUNUT], kuuntele=True)
    kirjaa_syy(istunto, "hiljaisuus:lopetus")
    return lopetusvuoro(istunto, PuhelunLoppu.EI_VASTAUSTA, [EI_VASTAUSTA_LOPETUS])


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
            kirjaa_syy(istunto, "varmistus:myonteinen")

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
        kirjaa_syy(istunto, "loukkaus:oire_ensin")
    elif tieto.loukkaus:
        return _loukkausvuoro(istunto, tieto)

    kielletty = VARMISTETTAVAT & set(tieto.hata_kielletty)
    if kielletty and not istunto.varmistettu:
        # "Ei mikään auta, haluan kuolla" voi tunnistua kielloksi: kysytään suoraan.
        istunto.varmistettu = True
        istunto.odottaa_varmistusta = True
        kirjaa_syy(istunto, "itsetuho:varmistus")
        return sano(istunto, [kiintea(istunto, "varmistuskysymys")])

    uudet = [syy for syy in tieto.vakavat if syy not in istunto.kasitellyt_vakavat]
    if uudet:
        syy = uudet[0]
        istunto.kasitellyt_vakavat.update(uudet)
        kirjaa_syy(istunto, f"vakava:{syy}")
        viesti = kiintea(istunto, f"vakavat_huolet.{syy}.viesti")
        huuto = huudahdus(syy, istunto.versiot)
        _aloita_tukitila(istunto, syy)
        return sano(istunto, [huuto, viesti])
    return None


def _hatavuoro(istunto: Puheluistunto, hatamerkki: str) -> Vuoro:
    """Hätäviesti luokittain. Itsetuhoisuus ei katkaise puhelua (tukitila); muut
    lopettavat, jotta henkilö soittaa heti hätänumeroon."""
    istunto.hatamerkki = istunto.hatamerkki or hatamerkki
    kirjaa_syy(istunto, f"hatamerkki:{hatamerkki}")
    viestit = tietopohjan_saannot()["hataviestit"]
    avain = f"hataviestit.{hatamerkki}" if hatamerkki in viestit else "hataviesti"
    sanottavat = [huudahdus(hatamerkki, istunto.versiot, hata=True), kiintea(istunto, avain)]
    if hatamerkki in HATA_TUKITILAAN:
        _aloita_tukitila(istunto, hatamerkki)
        return sano(istunto, sanottavat)
    return lopetusvuoro(istunto, PuhelunLoppu.HATAMERKKI, sanottavat)


def _aloita_tukitila(istunto: Puheluistunto, syy: str) -> None:
    """Vakavan asian jälkeen kysymykset jäävät pois: kuunnellaan ja varmistetaan
    turvallisuus. Puhelua ei katkaista ihmisen alta."""
    if istunto.tukitila is None:
        istunto.tukitila = SELKONIMET.get(syy, syy.replace("_", " "))
    if syy in ("itsetuhoisuus", "toivottomuus"):
        istunto.itsetuho = True
    istunto.vaihe = Vaihe.TUKI
    istunto.huoli_jatko = 0
    kirjaa_syy(istunto, f"tukitila:{syy}")


def _lopetuspyynto(istunto: Puheluistunto) -> Vuoro:
    """Ihmistä ei pidetä puhelimessa vastoin tahtoa: lopetetaan heti ensimmäiseen pyyntöön
    (päätetty 12.10.; aiemmin ensin kuulumis- tai turvallisuuskysymys). Huolien jälkeen
    lopetusviestissä on hoitajan soitto ja 112. Poikkeus: itsetuhoisen puheen jälkeen
    kysytään kerran turvallisuudesta ennen lopetusta (Jere)."""
    istunto.lopetuspyyntoja += 1
    kirjaa_syy(istunto, f"lopetuspyynto:{istunto.lopetuspyyntoja}")
    if istunto.itsetuho and not istunto.turvallisuus_kysytty:
        istunto.turvallisuus_kysytty = True
        istunto.lopettamassa = True
        return sano(istunto, [kiintea(istunto, "lopetuspyynto.turvallisuuskysymys")])
    return _lopeta_pyynnosta(istunto)


def _lopeta_pyynnosta(istunto: Puheluistunto) -> Vuoro:
    avain = (
        "lopetuspyynto.lopetus_huolestuttava" if huolestuttava(istunto) else "lopetuspyynto.lopetus"
    )
    kirjaa_syy(istunto, "lopetuspyynto:lopetus")
    return lopetusvuoro(istunto, PuhelunLoppu.LOPETTI, [kiintea(istunto, avain)])


def _loukkausvuoro(istunto: Puheluistunto, tieto: Tietopohjahavainto) -> Vuoro:
    """Ei loukkaannuta eikä moralisoida: 1. kysytään, onko kaikki hyvin, 2. tarjotaan
    lopettamista tai hoitajan soittoa, 3. lopetetaan kohteliaasti."""
    istunto.loukkauksia += 1
    kerta = istunto.loukkauksia
    kirjaa_syy(istunto, f"{'uhkaus' if tieto.uhkaus else 'loukkaus'}:{kerta}")
    if kerta >= tietopohjan_saannot()["loukkaus"]["lopeta_kerralla"]:
        return lopetusvuoro(istunto, PuhelunLoppu.LOPETTI, [kiintea(istunto, "loukkaus.lopetus")])
    if kerta >= 2:
        avain = "loukkaus.viesti_2"
    else:
        avain = "loukkaus.uhkaus_1" if tieto.uhkaus else "loukkaus.viesti_1"
    return sano(istunto, [kiintea(istunto, avain)])


def _saantovastaus(istunto: Puheluistunto, saannot: Saantohavainnot) -> Vuoro | None:
    """Hoitajapyyntö, lääkeneuvo ja usein kysytyt: vastaus säännöllä (ei mallia)."""
    if saannot.hoitajapyynto:
        kirjaa_syy(istunto, "hoitajapyynto")
        return lopetusvuoro(istunto, PuhelunLoppu.HALUAA_HOITAJAN, [HOITAJALLE])
    kysymys = viimeisin_kysymys(istunto.historia)
    if saannot.laakeneuvo:
        kirjaa_syy(istunto, "laakeneuvo:kiintea")
        istunto.vastaukset[-1].kysymys_hoitajalle = True
        return sano(istunto, [LAAKENEUVO_VASTAUS, kysymys])
    if saannot.faq_vastaus:
        kirjaa_syy(istunto, "usein_kysytty")
        return sano(istunto, [saannot.faq_vastaus, kysymys])
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
    kirjaa_syy(istunto, f"selkeys:{arvio}")
    vastaus.epavarma = True
    if istunto.kerrat < max_kerrat:
        istunto.kerrat += 1
        istunto.uudelleenkysymyksia += 1
        avain = (
            "uudelleenkysymys.toistoviesti"
            if arvio == "toistopyyntö"
            else "uudelleenkysymys.epaselva_viesti"
        )
        return sano(istunto, [kiintea(istunto, avain), kysymys])
    # Raja täynnä: ei jäädä jumiin, malli siirtyy seuraavaan kysymykseen.
    istunto.ohitetut.append(kysymys)
    istunto.kerrat = 0
    istunto.historia[-1]["content"] = "(ei selvää vastausta)"
    return piilo_ohje("siirry")


def _piilo_ohjeet(
    istunto: Puheluistunto,
    tieto: Tietopohjahavainto,
    konteksti: Vuorokonteksti,
    selkeys: str,
) -> Piiloohjeet:
    """Mallin vuorokohtaiset ohjeet: oire, huoli, lopettelu ja vaihtelu. Selkeyden ohje
    (_selkeys) on hetkellisistä ensimmäisenä."""
    uudet = [oire for oire in tieto.oireet if oire not in istunto.kasitellyt_oireet]
    istunto.kasitellyt_oireet.update(uudet)
    if uudet:
        kirjaa_syy(istunto, f"tietopohja:{','.join(uudet)}")
    tyypit = [muutos.tyyppi for muutos in istunto.konteksti.muutokset]
    henkilon_teksti = istunto.vastaukset[-1].vastaus
    tietopohja = oireohje_kotihoidolla(uudet, muutoksen_jalkeen(tyypit), henkilon_teksti)
    ohje = ""
    if jarkyttava_oire(uudet):
        ohje = piilo_ohje("jarkytys") + ohje
    # Jere: "kysy ensin yksi jatkokysymys, kun tiedät tarpeeksi, kerro kotihoito-ohjeet".
    # Listan ohje ohitti tämän (11.10. arviointi): oireen jälkeen kaksi vuoroa oireelle.
    oire_kesken = istunto.oire_jatko > 0
    kotihoito_oireet: list[str] = []
    if uudet:
        # Useampi oire kerralla: kaikki nimetään, ettei yksi jää käsittelemättä (11.10.
        # testipankki: pahoinvointi ohitettiin, kun huimaus mainittiin samassa lauseessa).
        istunto.avoimet_oireet = [*istunto.avoimet_oireet, *uudet] if oire_kesken else uudet
        istunto.oire_jatko = OIREEN_VUOROT + len(istunto.avoimet_oireet) - 1
    elif oire_kesken:
        istunto.oire_jatko -= 1
        if istunto.oire_jatko == 0:
            ohje += OIRE_KOTIHOITO_OHJE.format(oireet=", ".join(istunto.avoimet_oireet))
            kotihoito_oireet = istunto.avoimet_oireet
            istunto.avoimet_oireet = []

    toistuva_huoli = tieto.huoli and istunto.huolia_kuunneltu >= HUOLEN_KUUNTELUT
    if toistuva_huoli:
        # Sama pelko joka vastauksessa piti huolen auki loputtomiin (12.10. testipankki:
        # puhelu ei päättynyt 17 vuorossa). Huoli on jo kirjattu triageen.
        kirjaa_syy(istunto, f"huoli:toistuu:{','.join(tieto.huoli)}")
    elif tieto.huoli:
        kirjaa_syy(istunto, f"huoli:{','.join(tieto.huoli)}")
        ohje = piilo_ohje("huoli") + ohje
        istunto.huoli_jatko = tietopohjan_saannot()["huoli"]["jatkovuoroja"]
        istunto.huolia_kuunneltu += 1
    elif istunto.huoli_jatko > 0:
        istunto.huoli_jatko -= 1
        ohje += piilo_ohje("huoli_jatko")

    if konteksti.laake_poikkeama:
        poikkeama = tietopohjan_saannot()["laakemuutoksen_poikkeama"]["ohje_mallille"]
        ohje = f"\n(Ohje: {poikkeama})" + ohje
    if konteksti.lopuillaan:
        ohje += piilo_ohje("lopettelu")
    kysymys = avoin_kysymys(istunto)
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
    hetkelliset = selkeys + ohje
    return Piiloohjeet(tietopohja, hetkelliset, kotihoito_oireet)


# --- 4) Malli ---


def _mallille(viesti: dict[str, str]) -> dict[str, str]:
    """Historian viesti mallille: henkilön viestin perään sen vuoron tietopohja."""
    sisalto = viesti["content"] + viesti.get("tietopohja", "")
    return {"role": viesti["role"], "content": sisalto}


async def _mallin_vuoro(
    istunto: Puheluistunto,
    agentti: Keskusteluagentti,
    konteksti: Vuorokonteksti,
    ohjeet: Piiloohjeet,
    tarkistin: Tarkistin,
) -> Vuoro:
    if ohjeet.tietopohja:
        istunto.historia[-1]["tietopohja"] = ohjeet.tietopohja
    nykyinen = _mallille(istunto.historia[-1])
    nykyinen["content"] += ohjeet.hetkelliset
    viestit = [
        {"role": "system", "content": istunto.ohje()},
        *[_mallille(viesti) for viesti in istunto.historia[-MAX_VUOROPARIT * 2 : -1]],
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
            kirjaa_syy(istunto, "malli:lammitys_kesken")
            return varakysymys(istunto)
    alku = time.perf_counter()
    try:
        raaka = await agentti.vastaa(viestit)
    except Exception as virhe:
        logger.exception("Mallin vastaus epäonnistui")
        kirjaa_syy(istunto, f"agentti_virhe:{type(virhe).__name__}:{str(virhe)[:120]}")
        raaka = None
    istunto.mittaukset["malli_ms"] = round((time.perf_counter() - alku) * 1000)
    if raaka is not None:
        istunto.mittaukset["malli"] = raaka
        raaka = yhtenaista_lopetusmerkki(raaka)

    if istunto.vaihe == Vaihe.TUKI:
        return _tukivastaus(istunto, raaka)
    if raaka is None or not raaka.strip():
        return varakysymys(istunto)

    if ITSETUHOKYSYMYS.search(raaka):
        istunto.odottaa_varmistusta = True  # seuraava vastaus kuin varmistuskysymykseen
    vastaus = raaka.strip()
    if vastaus.startswith(UUDELLEENMERKKI):
        istunto.kerrat += 1
        istunto.uudelleenkysymyksia += 1
        istunto.epaselvia += 1
        istunto.vastaukset[-1].epavarma = True
        vastaus = vastaus[len(UUDELLEENMERKKI) :].lstrip(":,. ")
        kirjaa_syy(istunto, "malli:uudelleen")
    else:
        istunto.kerrat = 0

    lopettaa = LOPETUSMERKKI in vastaus
    esto = _lopetuksen_esto(istunto, konteksti) if lopettaa else None
    if esto is not None:
        kirjaa_syy(istunto, esto.syy)
        lopettaa = False
        vastaus = vastaus.replace(LOPETUSMERKKI, "")
        if esto.jatko and "?" not in vastaus:
            vastaus = f"{vastaus.strip()} {esto.jatko}"
        if esto.vain_kerran:
            istunto.lista_lopetus_ohitettu = True
    puhuttava = _puhuttavaksi(istunto, vastaus.replace(LOPETUSMERKKI, ""))
    if puhuttava and not tarkistin(puhuttava):
        kirjaa_syy(istunto, "kieli:epavarma")  # kirjataan, ei estetä (Jere: ei tarkistusta)
    if puhuttava:
        istunto.aloitukset.append(aloitus(puhuttava))
    if lopettaa:
        kirjaa_syy(istunto, "malli:lopetus")
        puhuttava = ilman_kiitoksia(puhuttava)
        puhuttava = _varmista_kotihoito(istunto, ohjeet.kotihoito_oireet, puhuttava)
        return lopetusvuoro(istunto, PuhelunLoppu.VALMIS, [puhuttava, lopetuslause(istunto)])
    if not puhuttava:
        return varakysymys(istunto)
    yksi = _yksi_kysymys(istunto, puhuttava)
    return eteneminen(istunto, _varmista_kotihoito(istunto, ohjeet.kotihoito_oireet, yksi))


def _lopetuksen_esto(istunto: Puheluistunto, konteksti: Vuorokonteksti) -> Esto | None:
    """Mallin LOPETUS tämän vuoron tiedoilla (lopetus.lopetuksen_esto)."""
    listan_kysymys = (
        istunto.kysymykset[max(0, istunto.seuraava - 1)].teksti if istunto.kysymykset else ""
    )
    saa_lopettaa_kesken = (
        konteksti.lopuillaan or konteksti.laake_poikkeama or istunto.lista_lopetus_ohitettu
    )
    return lopetuksen_esto(
        henkilon_teksti=istunto.historia[-1]["content"],
        henkilo_kysyi=konteksti.henkilo_kysyi,
        oire_kesken=istunto.oire_jatko > 0,
        listan_kysymys=listan_kysymys,
        lista_kesken=avoin_kysymys(istunto) is not None,
        saa_lopettaa_kesken=saa_lopettaa_kesken,
    )


def _puhuttavaksi(istunto: Puheluistunto, raaka: str) -> str:
    """Mallin teksti puheeksi: merkinnät pois ja toistuvat suomen virheet korjattuina."""
    puhuttava, korjatut = korjaa_kieli(siisti_vastaus(raaka))
    if korjatut:
        kirjaa_syy(istunto, f"kieli:korjattu:{','.join(korjatut)}")
    return puhuttava


def _yksi_kysymys(istunto: Puheluistunto, puhuttava: str) -> str:
    """Vain ensimmäinen kysymys sanotaan (Jeren ohje: yksi kysymys kerrallaan). Pieni malli
    kysyi kolme asiaa kerralla, ja henkilö kysyi "kumpaan vastaan?" (11.10.). Kysymyksen
    jälkeinen "Välitän tämän hoitajalle" siirretään ennen kysymystä: leikkaus pudotti sen
    (12.10. testipankki), muut kysymyksen jälkeiset lauseet ovat seuraavan kysymyksen alkua."""
    lauseet = jaa_lauseiksi(puhuttava)
    eka = next((i for i, lause in enumerate(lauseet) if lause.endswith("?")), None)
    if eka is None or eka == len(lauseet) - 1:
        return puhuttava
    lupaukset = [
        lause
        for lause in lauseet[eka + 1 :]
        if not lause.endswith("?") and "hoitaja" in lause.lower()
    ]
    kirjaa_syy(istunto, "malli:yksi_kysymys")
    return " ".join([*lauseet[:eka], *lupaukset, lauseet[eka]])


def _varmista_kotihoito(istunto: Puheluistunto, oireet: list[str], puhuttava: str) -> str:
    """Kotihoito-ohjeen vuoro: malli ohitti ohjeen 14 kertaa 16:sta (12.10. testipankki),
    joten tietopohjan ohje lisätään ennen kysymystä jokaiselle oireelle, jonka ohje puuttuu
    mallin vastauksesta (varmistusajo: kahdesta oireesta malli kertoi vain toisen ohjeen)."""
    puuttuvat = [oire for oire in oireet if not sisaltaa_kotihoidon(puhuttava, [oire])]
    ohje = kotihoito_puheeksi(puuttuvat)
    if not ohje:
        return puhuttava
    kirjaa_syy(istunto, f"kotihoito:lisatty:{','.join(puuttuvat)}")
    lauseet = jaa_lauseiksi(puhuttava)
    if lauseet and lauseet[-1].endswith("?"):
        return " ".join([*lauseet[:-1], ohje, lauseet[-1]])
    return f"{puhuttava} {ohje}".strip()


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
        kirjaa_syy(istunto, "tuki:lopetus_ohitettu")
        return sano(istunto, [kiintea(istunto, "tuki_jatko")])
    if lopettaa:
        kirjaa_syy(istunto, "tuki:lopetus")
        return lopetusvuoro(istunto, PuhelunLoppu.TUKI, [puhuttava, lopetuslause(istunto)])
    if not puhuttava:
        kirjaa_syy(istunto, "tuki:kiintea")
        return sano(istunto, [kiintea(istunto, "tuki_jatko")])
    kirjaa_syy(istunto, "tuki:malli")
    return sano(istunto, [puhuttava])


def ilman_lyhytta_kuittausta(vuoro: Vuoro) -> Vuoro:
    """Välitäyte ("Okei, merkitsen tämän.") toimi jo kuittauksena: lyhyt kuittaus pois."""
    lyhyet = {"Kiitos.", "Selvä.", "Ymmärrän.", "Okei."}
    if len(vuoro.sanottavat) > 1 and vuoro.sanottavat[0] in lyhyet:
        keskeytymaton = max(0, vuoro.keskeytymaton - 1)
        return Vuoro(
            sanottavat=vuoro.sanottavat[1:], kuuntele=vuoro.kuuntele, keskeytymaton=keskeytymaton
        )
    return vuoro
