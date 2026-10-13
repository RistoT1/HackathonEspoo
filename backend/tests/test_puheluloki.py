"""scripts/puheluloki.py --teksti: keskustelu tekstinä puhelulokista (synteettinen loki)."""

from scripts.puheluloki import keskustelu

ISTUNTO = "abcd1234-0000-4000-8000-000000000000"
AIKA = "2026-10-12T19:27:32.784+00:00"


def tapahtuma(tapahtuma: str, **tiedot: object) -> dict:
    return {"aika": AIKA, "istunto": ISTUNTO, "tapahtuma": tapahtuma, **tiedot}


def vuoro(vaihe_ennen: str, kuultu: str | None, sanottu: list[str], **tiedot: object) -> dict:
    return tapahtuma(
        "vuoro",
        vaihe_ennen=vaihe_ennen,
        kuultu=kuultu,
        sanottu=sanottu,
        syyt=tiedot.pop("syyt", []),
        loppu=tiedot.pop("loppu", None),
        **tiedot,
    )


def test_keskustelu_samassa_muodossa_kuin_paattyessa_kirjoitettu_teksti():
    rivit = [
        tapahtuma("kuultu", vaihe="ALKU", kuultu=None),
        vuoro("ALKU", None, ["Hei! Olen tekoälyavustaja. Miten olet voinut?"]),
        tapahtuma("puhe_ai_puhuessa", kuultu="Haloo"),  # ohitettu: ei keskustelussa
        tapahtuma("kuultu", vaihe="KESKUSTELU", kuultu="No ihan"),
        tapahtuma("jatkettu_puhe", kuultu="hyvin kai"),
        tapahtuma("vuoro_alusta", kuultu="No ihan hyvin kai", jatkoja=1),
        tapahtuma("valivastaus", sanottu="Okei, merkitsen tämän."),
        vuoro("KESKUSTELU", "No ihan hyvin kai", ["Onko huimannut?"], valivastattu=True),
        tapahtuma("hiljaisuus", odotettu_s=15.0),
        vuoro("KESKUSTELU", "", ["En kuullut vastausta.", "Onko huimannut?"]),
        tapahtuma("kuultu", vaihe="KESKUSTELU", kuultu="Vähän huimaa"),
        tapahtuma("kiireellinen_puhe", kuultu="rintaan sattuu"),
        tapahtuma("vuoro_keskeytetty", syy="kiireellinen puhe"),
        tapahtuma("kuultu", vaihe="KESKUSTELU", kuultu="rintaan sattuu"),
        vuoro(
            "KESKUSTELU",
            "rintaan sattuu",
            ["Voi ei!", "Soita heti 112."],
            syyt=["hatamerkki:rintakipu"],
            loppu="HATAMERKKI",
            kuuntele=False,
        ),
        tapahtuma("puhelu_paattyi", twilio_tila="completed", kesto_s="60"),
    ]

    assert keskustelu(rivit) == (
        f"Puhelu {ISTUNTO}, loppu HATAMERKKI, hätämerkki rintakipu\n"
        "\n"
        "Avustaja: Hei! Olen tekoälyavustaja. Miten olet voinut?\n"
        "Henkilö: No ihan hyvin kai\n"
        "Avustaja: Onko huimannut?\n"
        "Avustaja: En kuullut vastausta. Onko huimannut?\n"
        "Henkilö: Vähän huimaa\n"
        "Henkilö: rintaan sattuu\n"
        "Avustaja: Voi ei! Soita heti 112."
    )


def test_keskustelu_ilman_loppua_ja_hatamerkkia():
    """Twilion tila päätti puhelun (ei vuoroa, jossa loppu): loppu puuttuu otsikosta."""
    rivit = [
        vuoro("ALKU", None, ["Hei!"]),
        tapahtuma("relay_katkesi"),
    ]

    assert keskustelu(rivit) == f"Puhelu {ISTUNTO}, hätämerkki None\n\nAvustaja: Hei!"
