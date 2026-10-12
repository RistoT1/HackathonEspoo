"""Oirekirjasto testipankille: miten (synteettinen) vanhus puhuu oireesta.

Aiheet = kannan tarkennusaiheet (tarkennuskysymys.aihe) + Jeren tietopohjan oireita.
Jokaisella aiheella:
  avainsanat   botin kysymyksestä: kysytäänkö tästä aiheesta (pienillä, sanan alkuja)
  kerronta     oma-aloitteinen maininta ("on huimannut pari päivää")
  myonto       kyllä-vastaus suoraan kysymykseen
  kielto       ei-vastaus
  faktat       tarkennuksiin: alkoi, tilanne, voimakkuus, vaikutus, (sijainti), kuvaus
  huolestuttava  Jeren oireohjeiden mukaan (nostaa triagen vähintään keltaiseksi)
"""

import re
from dataclasses import dataclass


def osuu(sana: str, teksti: str) -> bool:
    """Avainsana sanan ALUSSA (ei keskellä): "annoksen" ei saa osua sanaan "oksen"."""
    return re.search(rf"\b{re.escape(sana)}", teksti) is not None


@dataclass(frozen=True)
class Oire:
    aihe: str
    avainsanat: tuple[str, ...]
    kerronta: tuple[str, ...]
    myonto: tuple[str, ...]
    kielto: tuple[str, ...]
    alkoi: tuple[str, ...]
    tilanne: tuple[str, ...]
    voimakkuus: tuple[str, ...]
    vaikutus: tuple[str, ...]
    kuvaus: tuple[str, ...]
    sijainti: tuple[str, ...] = ()
    huolestuttava: bool = False


YHTEISET_ALKOI = (
    "pari päivää sitten",
    "viikko sitten suunnilleen",
    "siitä asti kun aloitin sen lääkkeen",
    "en muista tarkkaan, joskus viime viikolla",
    "eilen illalla",
    "muutama päivä sitten",
)

OIREET: tuple[Oire, ...] = (
    Oire(
        "huimaus",
        ("huima", "pyörry", "tasapaino"),
        ("on vähän huimannut", "päätä on pyörryttänyt", "on ollut vähän huimausta"),
        ("joo on huimannut vähän", "kyllä, välillä huimaa", "jes, aamuisin varsinkin"),
        ("ei ole huimannut", "ei, ei yhtään", "en ole huomannut huimausta"),
        YHTEISET_ALKOI,
        (
            "kun nousen sängystä",
            "lähinnä aamuisin",
            "kun nousen tuolilta äkkiä",
            "kaupassa kävellessä",
        ),
        ("ihan lievää", "aika kovaa, piti ottaa seinästä kiinni", "vähän vaan", "joskus kovempaa"),
        ("en uskalla lähteä kauppaan", "ei se haittaa paljon", "pitää istua hetki"),
        ("semmoista pyörimistä", "musta meinaa mennä silmissä", "huojuttaa"),
        huolestuttava=True,
    ),
    Oire(
        "kaatuminen",
        ("kaatu", "kaadu", "kompastu", "kaatumaisil"),
        (
            "kaaduin tossa eilen keittiössä",
            "olin vähällä kaatua kylpyhuoneessa",
            "kompastuin matolla",
        ),
        ("joo kaaduin kerran", "kyllä, olin kaatumaisillani"),
        ("en ole kaatunut", "ei, en ole", "ei ole sattunut mitään"),
        ("eilen", "toissapäivänä", "viime viikolla", "aamulla tänään"),
        ("kun nousin yöllä vessaan", "kylpyhuoneessa", "eteisessä kengät jalassa"),
        ("polvi tuli vähän mustelmalle", "ei sattunut mitään", "lonkkaan sattuu vähän"),
        ("en uskalla nyt kävellä yksin", "pääsin itse ylös", "naapuri auttoi ylös"),
        ("jalka lähti alta", "kompastuin", "tuli heikko olo ja menin nurin"),
        huolestuttava=True,
    ),
    Oire(
        "vasymys",
        ("väsy", "unelia", "uupu", "jaksa"),
        ("on ollut kauhean väsynyt olo", "väsyttää koko ajan", "en jaksa oikein mitään"),
        ("joo on väsyttänyt", "kyllä, päivälläkin pitää nukkua", "vähän enemmän kuin ennen"),
        ("ei ole väsyttänyt", "en ole ollut väsyneempi", "ei, ihan normaalisti"),
        YHTEISET_ALKOI,
        ("iltapäivisin varsinkin", "koko päivän", "aamupäivällä"),
        ("aika paljon", "vähän", "niin että nukahdan tuoliin"),
        ("en jaksa käydä kaupassa", "ei haittaa paljon", "kotityöt jää tekemättä"),
        ("raskas olo", "uupunut olo", "voimaton olo"),
    ),
    Oire(
        "sekavuus",
        ("ajatus", "sekav", "muisti", "eri tavalla"),
        ("tytär sanoi että olen ollut vähän sekava", "muisti pätkii välillä", "ajatus katkeilee"),
        ("joo, tytär on sanonut niin", "vähän on ollut muisti huonompi"),
        ("ei ole", "ei, ajatus kulkee normaalisti", "en ole huomannut"),
        YHTEISET_ALKOI,
        ("iltaisin", "kun on paljon asioita", "aamulla herätessä"),
        ("vähän", "aika paljon, unohdin lääkkeetkin", "lievästi"),
        ("en muista ottaako lääkkeet", "ei haittaa paljon", "tytär käy nyt useammin"),
        ("unohtelua", "sekavuutta", "ajatus hyppii"),
        huolestuttava=True,
    ),
    Oire(
        "vatsa",
        ("vatsa", "ulost", "ripul", "ummet"),
        ("vatsa on ollut vähän sekaisin", "on ollut ripulia", "vatsa ei ole toiminut"),
        ("joo vatsa on ollut löysällä", "kyllä on ollut vähän vatsavaivaa"),
        ("on toiminut normaalisti", "ei ole ollut vaivaa", "ihan tavallisesti"),
        YHTEISET_ALKOI,
        ("ruokailun jälkeen", "aamuisin", "koko ajan"),
        ("lievää", "aika hankalaa", "vähän"),
        ("en uskalla lähteä kotoa", "ei haittaa paljon", "syön vähemmän"),
        ("löysää vatsaa", "ummetusta", "vatsakipua"),
        sijainti=("ylävatsassa", "alavatsassa", "napan seudulla"),
    ),
    Oire(
        "ruokahalu",
        ("ruoka", "maistu", "juo", "ruokahalu"),
        ("ruoka ei oikein maistu", "ei ole tehnyt mieli syödä", "en ole jaksanut juoda"),
        ("joo ruoka ei ole maistunut", "vähän huonommin on maistunut"),
        ("on maistunut hyvin", "ihan normaalisti syön", "kyllä on maistunut"),
        YHTEISET_ALKOI,
        ("aamuisin", "koko päivän", "lääkkeen jälkeen"),
        ("vähän", "syön puolet tavallisesta", "lähes en mitään"),
        ("paino on vähän pudonnut", "ei haittaa", "jaksaminen on huonompaa"),
        ("ruokahaluttomuutta", "pahaa oloa syödessä", "janottaa"),
    ),
    Oire(
        "turvotus",
        ("turvo", "nilk", "paksu"),
        ("nilkat on vähän turvonneet", "jalat on ollut paksut", "kengät puristaa"),
        ("joo nilkat on turvoksissa", "kyllä vähän turvottaa iltaisin"),
        ("ei ole turvotusta", "ei ole huomannut", "ei, ihan normaalit jalat"),
        YHTEISET_ALKOI,
        ("iltaisin", "kun olen istunut pitkään", "koko ajan"),
        ("vähän", "aika paljon", "toinen jalka enemmän"),
        ("kengät ei mahdu", "ei haittaa", "kävely on hankalaa"),
        ("turvotusta", "paksut nilkat"),
        sijainti=("molemmissa nilkoissa", "vasemmassa jalassa", "sääret ja nilkat"),
        huolestuttava=True,
    ),
    Oire(
        "verenvuoto",
        ("mustelm", "verenvuo", "vuota"),
        ("käsiin on tullut mustelmia", "nenästä vuoti verta eilen", "ikenistä vuotaa"),
        ("joo on tullut mustelmia", "kyllä, helposti tulee mustelma"),
        ("ei ole tullut", "ei mitään vuotoja", "ei ole huomannut"),
        YHTEISET_ALKOI,
        ("kun vähän kolhaisee", "itsestään", "hampaita pestessä"),
        ("pieniä mustelmia", "iso mustelma reidessä", "vuoti aika kauan"),
        ("ei haittaa", "vähän huolettaa", "piti pitää paperia nenässä pitkään"),
        ("mustelmia", "nenäverenvuotoa"),
        sijainti=("käsivarsissa", "jaloissa", "nenästä"),
        huolestuttava=True,
    ),
    Oire(
        "lihaskivut",
        ("lihak", "lihas", "kolot", "heikkout"),
        ("lihaksia on kolottanut", "jalat on ollut heikot", "lihakset on kipeät"),
        ("joo vähän kolottaa", "kyllä, reidet on kipeät"),
        ("ei ole ollut", "ei, lihakset on kunnossa"),
        YHTEISET_ALKOI,
        ("kävellessä", "aamuisin", "portaissa"),
        ("vähän", "aika kovaa", "jomottaa"),
        ("portaat on hankalia", "ei haittaa paljon"),
        ("kolotusta", "jomotusta", "heikkoutta"),
        sijainti=("reisissä", "pohkeissa", "hartioissa"),
        huolestuttava=True,
    ),
    Oire(
        "iho",
        ("iho", "ihottum", "kutin", "kutia"),
        ("iholle on tullut vähän ihottumaa", "kutisee käsivarsissa", "on punaisia läikkiä"),
        ("joo on vähän ihottumaa", "kyllä, kutisee"),
        ("ei ole tullut", "iho on ihan normaali"),
        YHTEISET_ALKOI,
        ("iltaisin kutisee eniten", "suihkun jälkeen", "koko ajan"),
        ("vähän", "aika paljon, raavin yöllä", "lievää"),
        ("uni häiriintyy", "ei haittaa"),
        ("punaisia läikkiä", "näppylöitä", "kutinaa"),
        sijainti=("käsivarsissa", "selässä", "vatsan päällä"),
        huolestuttava=True,
    ),
    Oire(
        "uni",
        ("nukku", "uni", "unta", "valvo"),
        ("en ole saanut nukuttua", "nukun huonosti", "herään yöllä monta kertaa"),
        ("huonosti olen nukkunut", "heräilen öisin"),
        ("hyvin olen nukkunut", "ihan normaalisti", "uni maistuu"),
        YHTEISET_ALKOI,
        ("nukahtaminen on vaikeaa", "herään kolmelta", "heräilen monta kertaa"),
        ("vähän", "tosi huonosti", "valvon tunteja"),
        ("päivällä väsyttää", "ei haittaa paljon"),
        ("unettomuutta", "heräilyä"),
    ),
    Oire(
        "kuume",
        ("kuume", "vilun", "lämpö"),
        ("on ollut vähän lämpöä", "on palellut ja ollut vilunväristyksiä", "kuumeinen olo"),
        ("joo vähän lämpöä on ollut", "kyllä, eilen oli kuumetta"),
        ("ei ole ollut kuumetta", "ei, ei ole"),
        ("eilen", "pari päivää", "tänä aamuna"),
        ("iltaisin", "koko ajan", "aamulla"),
        ("vähän, 37 ja puoli", "aika korkea", "en ole mitannut"),
        ("makaan sängyssä", "ei haittaa paljon"),
        ("vilunväristyksiä", "kuumeista oloa"),
        huolestuttava=True,
    ),
    Oire(
        "vapina",
        ("vapin", "täris", "käsi"),
        ("kädet on täristy vähän", "on ollut vapinaa käsissä"),
        ("joo vähän vapisee", "kyllä, kahvikuppi heiluu"),
        ("ei ole vapinaa", "ei ole huomannut"),
        YHTEISET_ALKOI,
        ("kun pitää jotain kädessä", "aamuisin", "koko ajan"),
        ("vähän", "aika paljon"),
        ("kirjoittaminen on vaikeaa", "ei haittaa"),
        ("vapinaa", "tärinää"),
    ),
    Oire(
        "sydamen_tykytys",
        ("sydän", "tykyt", "hakka", "muljah"),
        ("sydän on hakannut välillä", "on ollut sydämentykytystä", "sydän muljahtelee"),
        ("joo välillä hakkaa", "kyllä, iltaisin tuntuu"),
        ("ei ole", "sydän on ihan rauhallinen"),
        YHTEISET_ALKOI,
        ("illalla levätessä", "portaita noustessa", "yllättäen"),
        ("vähän", "aika voimakkaasti", "muutaman sekunnin"),
        ("pelottaa vähän", "ei haittaa"),
        ("tykytystä", "muljahtelua"),
        huolestuttava=True,
    ),
    Oire(
        "paansarky",
        ("pää", "särk"),
        ("päätä on särkenyt", "pää on ollut kipeä"),
        ("joo päätä särkee", "kyllä vähän"),
        ("ei ole särkenyt", "ei, pää on ollut ok"),
        YHTEISET_ALKOI,
        ("aamuisin", "iltapäivällä", "koko ajan"),
        ("vähän", "aika kovaa", "jomottaa"),
        ("en jaksa lukea", "ei haittaa"),
        ("jomottavaa", "painavaa"),
        sijainti=("ohimoilla", "takaraivossa", "otsalla"),
    ),
    Oire(
        "pahoinvointi",
        ("pahoin", "oksen", "huono olo", "kuvot"),
        ("on ollut huono olo", "on oksettanut", "kuvottaa aamuisin"),
        ("joo on ollut pahoinvointia", "kyllä, välillä oksettaa"),
        ("ei ole ollut", "ei, olo on ollut ok"),
        YHTEISET_ALKOI,
        ("ruokailun jälkeen", "aamuisin", "lääkkeen ottamisen jälkeen"),
        ("vähän", "aika paljon, oksensin kerran"),
        ("ruoka ei maistu", "ei haittaa paljon"),
        ("pahaa oloa", "kuvotusta"),
    ),
)

OIREET_AIHEITTAIN = {oire.aihe: oire for oire in OIREET}

# Tarkennuksen tyypit botin kysymyksestä (pienillä, sanan osia) -> mikä fakta vastataan.
TARKENNUSTYYPIT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("alkoi", ("milloin", "kauan", "alkoi", "alkanut", "aloittamisen jälkeen")),
    (
        "tilanne",
        (
            "tilantei",
            "kun nouset",
            "missä vaiheessa",
            "ruokailun",
            "jatkuvaa",
            "milloin se tulee",
            "aamuisin",
        ),
    ),
    ("voimakkuus", ("voimakas", "kova", "lievä", "kuinka paha", "paljon")),
    ("vaikutus", ("haittaa", "arke", "jaksat", "askare", "päivittäi", "vaikuttaa")),
    ("sijainti", ("missä kohtaa", "mistä kohtaa", "missä kohdassa", "kummassa", "missä")),
    ("kuvaus", ("millaista", "miltä", "kerro", "tarkemmin", "kuvailla")),
)
