"""Luo kolme synteettistä puheluyhteenvetoa hoitajan käyttöliittymän tarkasteluun.

Aja backend-kansiosta komennolla `uv run python scripts/luo_demo_yhteenvedot.py`.
Skripti lisää vain omilla demo-tunnisteillaan olevat rivit eikä poista muuta dataa.
"""

from datetime import UTC, datetime, timedelta

from app.config import hae_asetukset
from app.mallit import (
    Kiireellisyys,
    Kysymys,
    Laake,
    Laakemuutos,
    Muutoskonteksti,
    MuutosTyyppi,
    OireYhteenvedossa,
    Puhelukonteksti,
    PuhelunLoppu,
    PuhelutehtavanTila,
    Saantoloydokset,
    Yhteenveto,
)
from app.tietokanta import (
    PuhelunTulosRivi,
    PuhelutehtavanMuutosRivi,
    PuhelutehtavaRivi,
    hae_sessiotehdas,
)

ESIMERKIT = (
    {
        "tunniste": "demo-yhteenveto-vihrea",
        "istunto": "demo-istunto-vihrea",
        "taso": Kiireellisyys.VIHREA,
        "peruste": "Synteettinen esimerkki: ei tunnistettu kiireellistä huolta.",
        "yleisvointi": "Esimerkkihenkilö kertoo voivansa hyvin.",
        "oireet": [],
        "hatamerkit": [],
        "nimi": "Esimerkkivalmiste A",
        "muutos": MuutosTyyppi.UUSI_LAAKE,
        "kysymys": "Miten uusi lääke on sopinut?",
    },
    {
        "tunniste": "demo-yhteenveto-keltainen",
        "istunto": "demo-istunto-keltainen",
        "taso": Kiireellisyys.KELTAINEN,
        "peruste": "Synteettinen esimerkki: hoitajan on hyvä soittaa tänään.",
        "yleisvointi": "Esimerkkihenkilö kuvaa vointiaan tavallista heikommaksi.",
        "oireet": [
            OireYhteenvedossa(
                oire="huimaus",
                voimakkuus="kohtalainen",
                alkoi_laakkeen_jalkeen="kyllä",
                tilanne="Esimerkkitieto: huimausta noustessa.",
                varmuus="selvä",
            )
        ],
        "hatamerkit": [],
        "nimi": "Esimerkkivalmiste B",
        "muutos": MuutosTyyppi.ANNOSMUUTOS,
        "kysymys": "Onko muuttunut annos sopinut?",
    },
    {
        "tunniste": "demo-yhteenveto-punainen",
        "istunto": "demo-istunto-punainen",
        "taso": Kiireellisyys.PUNAINEN,
        "peruste": "Synteettinen esimerkki: sääntö tunnisti hätämerkin; hoitajan välitön arvio.",
        "yleisvointi": "Synteettinen esimerkkitapaus vaatii välitöntä huomiota.",
        "oireet": [
            OireYhteenvedossa(
                oire="rintakipu",
                voimakkuus="voimakas",
                alkoi_laakkeen_jalkeen="ei tiedossa",
                tilanne="Synteettinen esimerkkitieto.",
                varmuus="selvä",
            )
        ],
        "hatamerkit": ["rintakipu"],
        "nimi": "Esimerkkivalmiste C",
        "muutos": MuutosTyyppi.UUSI_LAAKE,
        "kysymys": "Miten uusi lääke on sopinut?",
    },
)


def _luo_muutos(esimerkki: dict, nyt: datetime) -> Laakemuutos:
    laake = Laake(
        koodi=f"demo-{esimerkki['tunniste']}",
        nimi=esimerkki["nimi"],
        alkoi=nyt - timedelta(days=4),
        paattyi=None,
        syy="Synteettinen käyttöliittymäesimerkki",
        atc=None,
        kaytossa=True,
    )
    return Laakemuutos(
        tyyppi=esimerkki["muutos"],
        laake=laake,
        tapahtui=nyt - timedelta(days=3),
        paivia_sitten=3,
        kuvaus="Synteettinen esimerkkimuutos käyttöliittymän tarkasteluun.",
    )


def _luo_yhteenveto(esimerkki: dict) -> Yhteenveto:
    taso = esimerkki["taso"]
    hatamerkit = esimerkki["hatamerkit"]
    return Yhteenveto(
        taso=taso,
        saanto=Saantoloydokset(
            taso=taso,
            perusteet=[esimerkki["peruste"]],
            hatamerkit=hatamerkit,
            tunnistetut_oireet=[oire.oire for oire in esimerkki["oireet"]],
            huolestuttavat_oireet=[oire.oire for oire in esimerkki["oireet"]] if hatamerkit else [],
        ),
        mallin_taso=taso,
        analyysi_valmis=True,
        yleisvointi=esimerkki["yleisvointi"],
        oireet=esimerkki["oireet"],
        turvallisuushuolet=[],
        tunnetila="",
        annetut_ohjeet=[],
        laakkeen_kaytto="kyllä",
        laakkeen_huomiot="Synteettinen esimerkkitieto.",
        huolet=[],
        perustelu=esimerkki["peruste"],
    )


def main() -> None:
    asetukset = hae_asetukset()
    if not asetukset.dev_tila:
        raise RuntimeError("Demoesimerkit voi luoda vain, kun DEV_TILA=true.")

    nyt = datetime.now(UTC)
    tallennetut = 0
    with hae_sessiotehdas()() as sessio:
        for indeksi, esimerkki in enumerate(ESIMERKIT):
            muutos = _luo_muutos(esimerkki, nyt)
            tehtava = sessio.get(PuhelutehtavaRivi, esimerkki["tunniste"])
            if tehtava is None:
                tehtava = PuhelutehtavaRivi(id=esimerkki["tunniste"])
                sessio.add(tehtava)
            tehtava.vanhus_id = f"demo-{esimerkki['taso'].value}"
            tehtava.tila = PuhelutehtavanTila.VALMIS.value
            tehtava.prioriteetti = 30 - indeksi * 10
            tehtava.perusteet = [esimerkki["peruste"]]
            tehtava.ika = 78 + indeksi
            tehtava.aktiivisia_laakkeita = 3
            tehtava.viitepaiva = nyt
            tehtava.yritykset = 1
            tehtava.kysymykset = [
                Kysymys(
                    tunniste=esimerkki["muutos"].value,
                    teksti=esimerkki["kysymys"],
                    muutos_tyyppi=esimerkki["muutos"],
                ).model_dump(mode="json")
            ]
            tehtava.konteksti = Puhelukonteksti(
                ika=78 + indeksi,
                aktiivisia_laakkeita=3,
                muutokset=[
                    Muutoskonteksti(
                        tyyppi=muutos.tyyppi,
                        laakeryhma=None,
                        paivia_sitten=3,
                    )
                ],
            ).model_dump(mode="json")
            tehtava.muutokset.clear()
            sessio.flush()
            tehtava.muutokset.append(
                PuhelutehtavanMuutosRivi(
                    muutosavain=f"{esimerkki['tunniste']}-muutos",
                    tyyppi=muutos.tyyppi.value,
                    tapahtui=muutos.tapahtui,
                    tiedot=muutos.model_dump(mode="json"),
                )
            )
            sessio.flush()

            tulos = sessio.get(PuhelunTulosRivi, esimerkki["istunto"])
            if tulos is None:
                tulos = PuhelunTulosRivi(istunto_id=esimerkki["istunto"])
                sessio.add(tulos)
            tulos.tehtava_id = esimerkki["tunniste"]
            tulos.loppu = PuhelunLoppu.VALMIS.value
            tulos.paattyi = nyt - timedelta(minutes=indeksi * 5)
            tulos.yhteenveto = _luo_yhteenveto(esimerkki).model_dump(mode="json")
            tallennetut += 1

        sessio.commit()

    print(f"Tallennettiin {tallennetut} synteettistä esimerkkitapausta (luonti tai päivitys).")
    print("Avaa käyttöliittymä: http://127.0.0.1:8000/hoitaja/")


if __name__ == "__main__":
    main()
