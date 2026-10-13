"""VAIN DEV: puheentunnistuksen kaikutesti. Toistaa kuullun, varmuuden ja hätämerkin;
ei tilakonetta eikä mallia."""

from app.mallit import KaikuTulos
from app.puhelu.hatasanat import tunnista_hatamerkki
from app.puhelu.istunto import Vuoro

KAIUN_KIERROKSET = 3
KAIUN_AVAUS = (
    "Tämä on puheentunnistuksen testi. Olen tekoäly. Sano jotain, niin kerron mitä kuulin."
)


def kaiun_vuoro(
    kierros: int, puhe: str | None, varmuus: float | None, stt_malli: str | None = None
) -> tuple[Vuoro, KaikuTulos | None]:
    """Kierros 0 = avaus. Sen jalkeen toistaa kuullun, varmuuden ja mahdollisen hatamerkin."""
    if kierros == 0:
        return Vuoro(sanottavat=[KAIUN_AVAUS], kuuntele=True), None

    kuultu = (puhe or "").strip()
    hatamerkki = tunnista_hatamerkki(kuultu) if kuultu else None
    tulos = KaikuTulos(
        kierros=kierros, kuultu=kuultu, varmuus=varmuus, hatamerkki=hatamerkki, stt_malli=stt_malli
    )

    sanottavat = [f"Kuulin: {kuultu}." if kuultu else "En kuullut mitään."]
    if varmuus is not None and kuultu:
        sanottavat.append(f"Varmuus {round(varmuus * 100)} prosenttia.")
    if hatamerkki is not None:
        sanottavat.append(f"Tunnistin hätämerkin: {hatamerkki}.")

    viimeinen = kierros >= KAIUN_KIERROKSET
    sanottavat.append("Testi päättyi. Hei hei." if viimeinen else "Sano jotain uutta.")
    return Vuoro(sanottavat=sanottavat, kuuntele=not viimeinen), tulos
