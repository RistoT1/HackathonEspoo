"""VAIN DEV: synteettinen puheluhistoria yhdelle henkilölle tapaussivun oirekaaviota varten.

Lisää tehtävän henkilölle kuusi aiempaa puhelua (yksi tavoittamaton) ennen hänen ensimmäistä
oikeaa puheluaan: pahoinvointi alkaa uuden lääkkeen jälkeen, lievittyy ja poistuu; mukana
ruokahaluttomuus, huimaus ja unettomuus. Oirenimet tarkoituksella eri sanoin (yhtenäistys).
Rivit liitetään tehtävään, joten ne näkyvät myös muistille (edellinen puhelu) kuten oikeat.

--maara N lisää näiden eteen N generoitua puhelua noin 2-4 päivän välein (100 = noin 8 kk):
oireet kulkevat jaksoina (alkaa, voimistuu tai lievittyy, poistuu), osa puheluista jää
tavoittamatta. Kiinteä siemen: sama komento tuottaa aina saman historian.

Aja backend-kansiosta:
  PYTHONPATH=. uv run python scripts/luo_demo_oirehistoria.py <tehtava_id>              # 6 puhelua
  PYTHONPATH=. uv run python scripts/luo_demo_oirehistoria.py <tehtava_id> --maara 100  # + 100
  ... <tehtava_id> --maara 100 --uusimpaan 50   # + 50 uusimpaan kuukauteen (vaakavieritys)
  PYTHONPATH=. uv run python scripts/luo_demo_oirehistoria.py <tehtava_id> --poista     # poista
"""

import argparse
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from app.config import hae_asetukset
from app.hoitaja.ajat import suomen_aika
from app.hoitaja.historia import yhtenaista
from app.mallit import (
    Kiireellisyys,
    OireenSeurannanTila,
    Oireseuranta,
    OireYhteenvedossa,
    PuhelunLoppu,
    Saantoloydokset,
    Yhteenveto,
)
from app.tietokanta import PuhelunTulosRivi, PuhelutehtavaRivi, hae_sessiotehdas

K = Kiireellisyys

# (päiviä ennen ensimmäistä oikeaa puhelua, kellonaika, loppu, taso, oireet, lääke otettu,
#  oireseurannat, yleisvointi). Oire = (sanamuoto, voimakkuus, alkoi lääkkeen jälkeen).
PUHELUT = (
    (
        18,
        (10, 5),
        PuhelunLoppu.VALMIS,
        K.VIHREA,
        (("vähän väsyttää", "lievä", "ei tiedossa"),),
        "kyllä",
        (),
        "Vointi tavallinen, hieman väsynyt.",
    ),
    (
        14,
        (10, 12),
        PuhelunLoppu.VALMIS,
        K.KELTAINEN,
        (("oksettaa aamuisin", "kohtalainen", "kyllä"), ("ruoka ei maistu", "lievä", "kyllä")),
        "kyllä",
        (),
        "Pahoinvointia aamuisin uuden lääkkeen aloittamisen jälkeen.",
    ),
    (10, (9, 30), PuhelunLoppu.EI_VASTANNUT, None, (), "ei tiedossa", (), ""),
    (
        9,
        (9, 48),
        PuhelunLoppu.VALMIS,
        K.KELTAINEN,
        (
            ("pahoinvointia", "kohtalainen", "kyllä"),
            ("ruoka ei maistu", "kohtalainen", "kyllä"),
            ("pyörryttää noustessa", "lievä", "ei tiedossa"),
        ),
        "ei",
        (
            Oireseuranta(
                oire="pahoinvointi",
                aiempi_voimakkuus="kohtalainen",
                nykyinen_voimakkuus="kohtalainen",
                tila=OireenSeurannanTila.ENNALLAAN,
            ),
        ),
        "Pahoinvointi jatkuu; jättänyt aamuannoksen väliin.",
    ),
    (
        6,
        (10, 30),
        PuhelunLoppu.VALMIS,
        K.KELTAINEN,
        (
            ("pahoinvointia", "lievä", "kyllä"),
            ("huimausta", "kohtalainen", "ei tiedossa"),
            ("en saa unta", "lievä", "ei tiedossa"),
        ),
        "kyllä",
        (
            Oireseuranta(
                oire="pahoinvointi",
                aiempi_voimakkuus="kohtalainen",
                nykyinen_voimakkuus="lievä",
                tila=OireenSeurannanTila.LIEVITTYNYT,
            ),
        ),
        "Pahoinvointi helpottanut, huimausta noustessa.",
    ),
    (
        3,
        (11, 2),
        PuhelunLoppu.VALMIS,
        K.VIHREA,
        (("huimausta", "lievä", "ei tiedossa"), ("vähän väsyttää", "lievä", "ei tiedossa")),
        "kyllä",
        (
            Oireseuranta(
                oire="pahoinvointi",
                aiempi_voimakkuus="lievä",
                nykyinen_voimakkuus="ei tiedossa",
                tila=OireenSeurannanTila.POISTUNUT,
            ),
        ),
        "Vointi parempi, pahoinvointi ohi.",
    ),
)


# Generoitujen puhelujen oireet: sanamuodot, jotka tietopohja yhtenäistää oireeksi.
SANAMUODOT = {
    "väsymys": ("väsyttää", "olen ollut väsynyt", "vähän väsyttää"),
    "huimaus": ("huimausta", "pyörryttää noustessa", "huimaa aamuisin"),
    "pahoinvointi": ("pahoinvointia", "oksettaa aamuisin", "kuvottaa"),
    "unettomuus": ("en saa unta", "huonosti nukuttu"),
    "päänsärky": ("päänsärkyä", "päätä särkee"),
    "ruokahaluttomuus ja juominen": ("ruoka ei maistu",),
    "alakulo ja yksinäisyys": ("yksinäistä", "alakuloinen olo"),
    "lihaskipu": ("lihaksia kolottaa",),
    "turvotus": ("jalat turvoksissa",),
}
VOIMAKKUUDET = ("lievä", "kohtalainen", "voimakas")
SIEMEN = 2026
EI_VASTANNUT_OSUUS = 0.08
UUSI_OIRE_OSUUS = 0.3
MAX_OIREITA = 3


def _kellonaikaan(arpa: random.Random, paiva: datetime, alin: int, ylin: int) -> datetime:
    """Päivä Suomen aikaan, kellonaika väliltä alin..ylin, takaisin UTC:ksi."""
    paikallinen = suomen_aika(paiva).replace(
        hour=arpa.randint(alin, ylin), minute=arpa.randrange(0, 60, 5), second=0, microsecond=0
    )
    return paikallinen.astimezone(UTC)


def harvat_ajat(arpa: random.Random, maara: int, ennen: datetime) -> list[datetime]:
    """Taaksepäin 2-4 päivän välein ennen hetkeä `ennen`, klo 9-14. Vanhin ensin."""
    ajat: list[datetime] = []
    paiva = ennen
    for _ in range(maara):
        paiva -= timedelta(days=arpa.choice((2, 3, 3, 4)))
        ajat.append(_kellonaikaan(arpa, paiva, 9, 14))
    return sorted(ajat)


def tiheat_ajat(arpa: random.Random, maara: int, alku: datetime, loppu: datetime) -> list[datetime]:
    """Satunnaisesti väliltä alku..loppu, klo 8-19 Suomen aikaa. Vanhin ensin."""
    paivia = max(1, (loppu - alku).days + 1)
    ajat: list[datetime] = []
    while len(ajat) < maara:
        aika = _kellonaikaan(arpa, alku + timedelta(days=arpa.randrange(paivia)), 8, 19)
        if alku <= aika < loppu:
            ajat.append(aika)
    return sorted(ajat)


def generoi_puhelut(ajat: list[datetime], arpa: random.Random) -> list[tuple]:
    """Puhelut annettuihin aikoihin (vanhin ensin). Sama muoto kuin PUHELUT, mutta
    ensimmäisenä päättymisaika (UTC) päivämäärän sijaan."""
    aktiiviset: dict[str, int] = {}  # oire -> voimakkuus 1..3
    puhelut: list[tuple] = []
    for paattyi in ajat:
        if arpa.random() < EI_VASTANNUT_OSUUS:
            puhelut.append((paattyi, PuhelunLoppu.EI_VASTANNUT, None, (), "ei tiedossa", (), ""))
            continue

        seurannat: list[Oireseuranta] = []
        for oire, taso in list(aktiiviset.items()):
            uusi = taso + arpa.choices((-1, 0, 1), weights=(45, 40, 15))[0]
            if uusi <= 0:
                del aktiiviset[oire]
                seurannat.append(
                    Oireseuranta(
                        oire=oire,
                        aiempi_voimakkuus=VOIMAKKUUDET[taso - 1],
                        tila=OireenSeurannanTila.POISTUNUT,
                    )
                )
                continue
            aktiiviset[oire] = min(uusi, len(VOIMAKKUUDET))
        if len(aktiiviset) < MAX_OIREITA and arpa.random() < UUSI_OIRE_OSUUS:
            vapaat = [oire for oire in SANAMUODOT if oire not in aktiiviset]
            aktiiviset[arpa.choice(vapaat)] = arpa.choice((1, 1, 2))

        oireet = tuple(
            (arpa.choice(SANAMUODOT[oire]), VOIMAKKUUDET[taso - 1], "ei tiedossa")
            for oire, taso in aktiiviset.items()
        )
        kaytto = "ei" if arpa.random() < EI_VASTANNUT_OSUUS else "kyllä"
        voimakas = any(taso == 3 for taso in aktiiviset.values())
        kohtalaisia = sum(taso >= 2 for taso in aktiiviset.values())
        huolestuttava = voimakas or kohtalaisia >= 2 or kaytto == "ei"
        taso = K.KELTAINEN if huolestuttava else K.VIHREA
        kuvaus = ", ".join(sanat for sanat, _, _ in oireet)
        yleisvointi = f"Kertoi: {kuvaus}." if oireet else "Vointi tavallinen, ei oireita."
        puhelut.append(
            (paattyi, PuhelunLoppu.VALMIS, taso, oireet, kaytto, tuple(seurannat), yleisvointi)
        )
    return puhelut


def _etuliite(tehtava_id: str) -> str:
    return f"demo-oirehistoria-{tehtava_id[:8]}-"


def _yhteenveto(taso, oireet, kaytto, seurannat, yleisvointi) -> Yhteenveto:
    oireyhteenvedot = [
        OireYhteenvedossa(
            oire=sanat, voimakkuus=voimakkuus, alkoi_laakkeen_jalkeen=alkoi, varmuus="selvä"
        )
        for sanat, voimakkuus, alkoi in oireet
    ]
    perusteet = [f"Synteettinen demohistoria: {yleisvointi}"]
    if kaytto == "ei":
        perusteet.append("Lääkettä ei ole otettu ohjeen mukaan.")
    return Yhteenveto(
        taso=taso,
        saanto=Saantoloydokset(
            taso=taso,
            perusteet=perusteet,
            tunnistetut_oireet=[yhtenaista(sanat) for sanat, _, _ in oireet],
            oireseurannat=list(seurannat),
        ),
        mallin_taso=taso,
        analyysi_valmis=True,
        yleisvointi=yleisvointi,
        oireet=oireyhteenvedot,
        laakkeen_kaytto=kaytto,
        perustelu=perusteet[0],
    )


def main() -> None:
    jasennin = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    jasennin.add_argument("tehtava_id")
    jasennin.add_argument("--poista", action="store_true", help="poista tämän tehtävän demorivit")
    jasennin.add_argument("--maara", type=int, default=0, help="generoitujen puhelujen määrä")
    jasennin.add_argument(
        "--uusimpaan",
        type=int,
        default=0,
        help="lisää N puhelua uusimpaan kuukauteen ennen henkilön viimeisintä oikeaa puhelua",
    )
    argumentit = jasennin.parse_args()

    if not hae_asetukset().dev_tila:
        raise SystemExit("Demohistorian voi luoda vain, kun DEV_TILA=true.")

    etuliite = _etuliite(argumentit.tehtava_id)
    with hae_sessiotehdas()() as sessio:
        tehtava = sessio.get(PuhelutehtavaRivi, argumentit.tehtava_id)
        if tehtava is None:
            raise SystemExit(f"Tehtävää {argumentit.tehtava_id} ei löydy.")
        poistettu = sessio.execute(
            delete(PuhelunTulosRivi).where(PuhelunTulosRivi.istunto_id.startswith(etuliite))
        ).rowcount
        if argumentit.poista:
            sessio.commit()
            print(f"Poistettiin {poistettu} demopuhelua.")
            return

        # Historia ennen henkilön ensimmäistä oikeaa puhelua, jotta oikeat jäävät uusimmiksi.
        ensimmainen = sessio.scalar(
            select(func.min(PuhelunTulosRivi.paattyi))
            .join(PuhelutehtavaRivi, PuhelutehtavaRivi.id == PuhelunTulosRivi.tehtava_id)
            .where(PuhelutehtavaRivi.vanhus_id == tehtava.vanhus_id)
        )
        viimeisin = sessio.scalar(
            select(func.max(PuhelunTulosRivi.paattyi))
            .join(PuhelutehtavaRivi, PuhelutehtavaRivi.id == PuhelunTulosRivi.tehtava_id)
            .where(PuhelutehtavaRivi.vanhus_id == tehtava.vanhus_id)
        )
        perusta = ensimmainen or datetime.now(UTC)
        if perusta.tzinfo is None:
            perusta = perusta.replace(tzinfo=UTC)
        viimeisin_aika = viimeisin or datetime.now(UTC)
        if viimeisin_aika.tzinfo is None:
            viimeisin_aika = viimeisin_aika.replace(tzinfo=UTC)

        for numero, (
            paivia,
            (tunti, minuutti),
            loppu,
            taso,
            oireet,
            kaytto,
            seurannat,
            yleisvointi,
        ) in enumerate(PUHELUT, start=1):
            paiva = perusta - timedelta(days=paivia)
            paattyi = paiva.replace(hour=tunti, minute=minuutti, second=0, microsecond=0)
            yhteenveto = _yhteenveto(taso, oireet, kaytto, seurannat, yleisvointi) if taso else None
            sessio.add(
                PuhelunTulosRivi(
                    istunto_id=f"{etuliite}{numero}",
                    tehtava_id=tehtava.id,
                    loppu=loppu.value,
                    paattyi=paattyi,
                    yhteenveto=yhteenveto.model_dump(mode="json") if yhteenveto else None,
                )
            )

        # Generoidut käsin kirjoitettujen eteen (vanhin käsin kirjoitettu: 18 pv ennen).
        arpa = random.Random(SIEMEN)
        harvat = harvat_ajat(arpa, argumentit.maara, perusta - timedelta(days=21))
        generoidut = [("g", puhelu) for puhelu in generoi_puhelut(harvat, arpa)]

        # Uusimman kuukauden tihennys: kuun alusta (Suomen aikaa) viimeisimpään oikeaan puheluun.
        tihea_arpa = random.Random(SIEMEN + 1)
        kuun_alku = suomen_aika(viimeisin_aika).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        tiheat = tiheat_ajat(
            tihea_arpa, argumentit.uusimpaan, kuun_alku.astimezone(UTC), viimeisin_aika
        )
        generoidut += [("u", puhelu) for puhelu in generoi_puhelut(tiheat, tihea_arpa)]

        for numero, (sarja, puhelu) in enumerate(generoidut, start=1):
            paattyi, tulos, taso, oireet, kaytto, seurannat, yleisvointi = puhelu
            yhteenveto = _yhteenveto(taso, oireet, kaytto, seurannat, yleisvointi) if taso else None
            sessio.add(
                PuhelunTulosRivi(
                    istunto_id=f"{etuliite}{sarja}{numero}",
                    tehtava_id=tehtava.id,
                    loppu=tulos.value,
                    paattyi=paattyi,
                    yhteenveto=yhteenveto.model_dump(mode="json") if yhteenveto else None,
                )
            )
        sessio.commit()
        vanhus_id = tehtava.vanhus_id

    lisatty = len(PUHELUT) + len(generoidut)
    print(f"Lisättiin {lisatty} demopuhelua henkilölle {vanhus_id[:8]}.")
    print(f"Avaa: http://127.0.0.1:8000/hoitaja/potilaat/{vanhus_id}")


if __name__ == "__main__":
    main()
