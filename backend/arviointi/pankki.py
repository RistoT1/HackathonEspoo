"""Testipankin generointi: keskustelupohjat (persoonat) + yksittäisten vastausten pankki.

Kysymykset ovat kannan sisältöä (muutoskysymykset, tarkennuskysymykset, yleiskysymykset,
lääkeryhmät), joten testit vastaavat oikeita soittotehtäviä. Sisältö tallennetaan pankkiin,
jolloin ajo ei tarvitse kantaa. Deterministinen siemenestä.

Yksittäiset: jokaiseen kannan kysymykseen vastaustyypit (kyllä, kyllä ja tarkennus, ei,
"ei mutta muu oire", en tiedä, epäselvä, kysymys takaisin, lääkeneuvo, hätä, lopetus, huoli,
loukkaus, toistopyyntö) usealla sanamuodolla ja puhetyylillä. Odotus = mitä botin pitää
tehdä (tarkistus.py).
"""

import random

from arviointi.oireet import OIREET, OIREET_AIHEITTAIN
from arviointi.persoonat import TAPAHTUMAT, luo_persoona, muunna

YKSITTAISTEN_TYYLIT = ("normaali", "murre", "stt", "lyhyt")


def sisalto_kannasta() -> dict:
    """Kannan kysymyssisältö JSON-muotoon (vaatii kannan: backend/.env DATABASE_URL)."""
    from app.kysymykset.varasto import lataa_sisalto
    from app.tietokanta import hae_sessiotehdas

    with hae_sessiotehdas()() as sessio:
        sisalto = lataa_sisalto(sessio)
    return {
        "muutoskysymykset": {m.value: t for m, t in sisalto.muutoskysymykset.items()},
        "tarkennuskysymykset": dict(sisalto.tarkennuskysymykset),
        "yleiskysymykset": [list(y) for y in sisalto.yleiskysymykset],
        "laakeryhmat": [
            {"nimi": r.nimi, "tarkennusaiheet": list(r.tarkennusaiheet)}
            for r in sisalto.laakeryhmat
        ],
    }


def _vastaukset_oirekysymykseen(aihe: str, rng: random.Random) -> list[tuple[str, str, str]]:
    """(tyyppi, vastaus, odotus) oirekysymykseen."""
    oire = OIREET_AIHEITTAIN.get(aihe)
    if oire is None:
        return []
    muut = [o for o in OIREET if o.aihe != aihe]
    tulos = []
    for myonto in oire.myonto:
        tulos.append(("kylla", myonto, f"tarkenna:{aihe}"))
    for _ in range(2):
        fakta = rng.choice(oire.tilanne + oire.alkoi)
        tulos.append(("kylla_tarkka", f"{rng.choice(oire.myonto)}, {fakta}", f"tarkenna:{aihe}"))
    for kielto in oire.kielto:
        tulos.append(("ei", kielto, "ei_ongelmaa"))
    for _ in range(2):
        muu = rng.choice(muut)
        teksti = f"{rng.choice(oire.kielto)}, mutta {rng.choice(muu.kerronta)}"
        tulos.append(("ei_mutta", teksti, f"tarkenna:{muu.aihe}"))
    return tulos


def _yleiset_vastaukset(kysymysaihe: str, rng: random.Random) -> list[tuple[str, str, str]]:
    """Kaikkiin kysymyksiin: epäselvät, kysymys takaisin, hätä, lopetus jne."""
    tulos = [
        ("en_tieda", "en tiedä", "ei_jumiin"),
        ("en_tieda", "en osaa sanoa", "ei_jumiin"),
        ("en_tieda", "en muista", "ei_jumiin"),
        ("epaselva", "no se on niin ja näin tuota", "kysy_uudelleen"),
        ("epaselva", "kävin eilen kaupassa", "kysy_uudelleen"),
        ("epaselva", "mm", "kysy_uudelleen"),
    ]
    for teksti in TAPAHTUMAT["faq"]:
        tulos.append(("faq", teksti, "faq"))
    for teksti in TAPAHTUMAT["laakeneuvo"]:
        tulos.append(("laakeneuvo", teksti, "laakeneuvo"))
    for teksti in TAPAHTUMAT["hata"]:
        tulos.append(("hata", teksti, "hata"))
    for teksti in TAPAHTUMAT["itsetuho"]:
        tulos.append(("itsetuho", teksti, "tukitila"))
    for teksti in TAPAHTUMAT["lopetus"]:
        tulos.append(("lopetus", teksti, "lopetus"))
    for teksti in TAPAHTUMAT["huoli"]:
        tulos.append(("huoli", teksti, "huoli"))
    for teksti in TAPAHTUMAT["loukkaus"]:
        tulos.append(("loukkaus", teksti, "loukkaus"))
    for teksti in TAPAHTUMAT["toisto"]:
        tulos.append(("toisto", teksti, "toisto"))
    if kysymysaihe in ("muutos", "vointi"):
        oire = rng.choice(OIREET)
        tulos += [
            ("hyvin", "ihan hyvin on mennyt", "ei_ongelmaa"),
            ("hyvin", "hyvin kiitos", "ei_ongelmaa"),
            ("oire", f"ihan ok, mutta {rng.choice(oire.kerronta)}", f"tarkenna:{oire.aihe}"),
            ("oire", rng.choice(oire.kerronta), f"tarkenna:{oire.aihe}"),
        ]
    return tulos


def yksittaiset(sisalto: dict, rng: random.Random) -> list[dict]:
    kysymykset: list[tuple[str, str]] = [
        ("muutos", teksti) for teksti in sisalto["muutoskysymykset"].values()
    ]
    kysymykset += list(sisalto["tarkennuskysymykset"].items())
    kysymykset += [("vointi", teksti) for _, teksti in sisalto["yleiskysymykset"]]
    tulos = []
    for aihe, kysymys in kysymykset:
        vastaukset = _vastaukset_oirekysymykseen(aihe, rng) + _yleiset_vastaukset(aihe, rng)
        for tyyppi, vastaus, odotus in vastaukset:
            # Sääntöpolut (hätä, faq...) eivät riipu tyylistä: yksi riittää.
            tyylista_kiinni = odotus.startswith(("tarkenna", "ei_", "kysy"))
            tyylit = YKSITTAISTEN_TYYLIT if tyylista_kiinni else ("normaali",)
            for tyyli in tyylit:
                teksti = muunna(vastaus, tyyli, rng) if tyyli != "normaali" else vastaus
                tulos.append(
                    {
                        "id": f"y{len(tulos) + 1:05d}",
                        "kysymysaihe": aihe,
                        "kysymys": kysymys,
                        "tyyppi": tyyppi,
                        "tyyli": tyyli,
                        "vastaus": teksti,
                        "odotus": odotus,
                    }
                )
    return tulos


def generoi(persoonia: int, siemen: int, sisalto: dict) -> dict:
    rng = random.Random(siemen)
    return {
        "siemen": siemen,
        "sisalto": sisalto,
        "persoonat": [luo_persoona(n + 1, sisalto, rng) for n in range(persoonia)],
        "yksittaiset": yksittaiset(sisalto, rng),
    }
