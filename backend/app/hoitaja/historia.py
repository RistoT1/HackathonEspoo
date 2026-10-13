"""Tapaussivun oirehistoria: mistä henkilö on kertonut puheluissa. Puhtaat funktiot.

Lähde on sama kuin muistilla: saman henkilön (vanhus_id) tallennetut puhelun tulokset
(puhelun_tulos), vain hoitajan yhteenveto, ei vastauksia eikä litterointia.

Mallin oirenimet ovat vapaata tekstiä ("tosi vaikeat päänkiput"), joten ne yhtenäistetään
tietopohjan sääntötunnistuksella (sama kuin puhelussa): sama vaiva eri sanoin osuu samalle
riville. Tunnistamaton nimi jää mallin sanoiksi.
"""

from collections import Counter

from app.hoitaja.ajat import suomen_aika
from app.mallit import (
    OireenSeurannanTila,
    Oirehistoria,
    OirehistorianPuhelu,
    Oirekuukausi,
    Oiremerkinta,
    Oiresivu,
    PuhelunLoppu,
    TallennettuTulos,
    Yhteenveto,
)
from app.puhelu import tietopohja

SIVUN_KOKO = 10  # puhelua taulukon sivulla; kuukausi jaetaan, jos puheluita on enemmän
EI_TIEDOSSA = "ei tiedossa"
POISTUNUT = "poistunut"
VOIMAKKUUDEN_JARJESTYS = {POISTUNUT: 0, EI_TIEDOSSA: 1, "lievä": 2, "kohtalainen": 3, "voimakas": 4}
KUUKAUDET = (
    "tammikuu", "helmikuu", "maaliskuu", "huhtikuu", "toukokuu", "kesäkuu",
    "heinäkuu", "elokuu", "syyskuu", "lokakuu", "marraskuu", "joulukuu",
)  # fmt: skip
# Puhelut, joissa ei keskusteltu (sama rajaus kuin muistissa).
_EI_KESKUSTELUA = frozenset({PuhelunLoppu.EI_VASTANNUT, PuhelunLoppu.EI_VASTAUSTA})


def yhtenaista(nimi: str) -> str:
    """Tietopohjan oire, jos sääntö tunnistaa sen; muuten nimi sellaisenaan (pienin)."""
    tunnistetut = tietopohja.tarkista(nimi).oireet
    if tunnistetut:
        return tunnistetut[0]
    return " ".join(nimi.lower().split())


def _voimakkaampi(a: str, b: str) -> str:
    return max(a, b, key=lambda v: VOIMAKKUUDEN_JARJESTYS.get(v, 1))


def _lisaa(
    merkinnat: dict[str, Oiremerkinta], nimi: str, voimakkuus: str, alkoi: str = EI_TIEDOSSA
) -> None:
    """Saman puhelun saman oireen maininnat yhteen: voimakkain voimakkuus, kaikki sanamuodot."""
    oire = yhtenaista(nimi)
    voimakkuus = voimakkuus or EI_TIEDOSSA
    aiempi = merkinnat.get(oire)
    if aiempi is None:
        merkinnat[oire] = Oiremerkinta(
            oire=oire, voimakkuus=voimakkuus, kuvaukset=[nimi], alkoi_laakkeen_jalkeen=alkoi
        )
        return
    aiempi.voimakkuus = _voimakkaampi(aiempi.voimakkuus, voimakkuus)
    if nimi not in aiempi.kuvaukset:
        aiempi.kuvaukset.append(nimi)
    if aiempi.alkoi_laakkeen_jalkeen == EI_TIEDOSSA:
        aiempi.alkoi_laakkeen_jalkeen = alkoi


def oiremerkinnat(yhteenveto: Yhteenveto) -> list[Oiremerkinta]:
    """Mallin analyysin oireet; ilman analyysia sääntöjen oireet. Edellisestä puhelusta
    seurattu oire, joka on poistunut, kirjataan voimakkuudella "poistunut"."""
    merkinnat: dict[str, Oiremerkinta] = {}
    for oire in yhteenveto.oireet:
        _lisaa(merkinnat, oire.oire, oire.voimakkuus, oire.alkoi_laakkeen_jalkeen)
    if not merkinnat:
        for nimi in yhteenveto.saanto.tunnistetut_oireet:
            _lisaa(merkinnat, nimi, EI_TIEDOSSA)
    for seuranta in yhteenveto.saanto.oireseurannat:
        poistunut = seuranta.tila == OireenSeurannanTila.POISTUNUT
        if poistunut and yhtenaista(seuranta.oire) not in merkinnat:
            _lisaa(merkinnat, seuranta.oire, POISTUNUT)
    return list(merkinnat.values())


def _puhelun_nimi(tulos: TallennettuTulos) -> str:
    aika = suomen_aika(tulos.paattyi)
    return f"{aika.day}.{aika.month}. klo {aika.hour:02d}.{aika.minute:02d}"


def oirehistoria(
    tulokset: list[TallennettuTulos], tehtava_id: str | None = None
) -> Oirehistoria | None:
    """None = henkilölle ei ole puheluita. Tulos ilman päättymisaikaa ohitetaan (ei voi
    sijoittaa aikajanalle)."""
    ajalliset = sorted((t for t in tulokset if t.paattyi is not None), key=lambda t: t.paattyi)
    if not ajalliset:
        return None

    puhelut: list[OirehistorianPuhelu] = []
    for tulos in ajalliset:
        yhteenveto = tulos.yhteenveto
        tavoitettu = tulos.loppu is not None and tulos.loppu not in _EI_KESKUSTELUA
        puhelut.append(
            OirehistorianPuhelu(
                nimi=_puhelun_nimi(tulos),
                paattyi=tulos.paattyi,
                taso=yhteenveto.taso if yhteenveto else None,
                tavoitettu=tavoitettu,
                tehtava_id=tulos.tehtava_id,
                loppu=tulos.loppu,
                tama_tehtava=tulos.tehtava_id == tehtava_id,
                oireet=oiremerkinnat(yhteenveto) if yhteenveto else [],
            )
        )

    mainintoja = Counter(
        merkinta.oire
        for puhelu in puhelut
        for merkinta in puhelu.oireet
        if merkinta.voimakkuus != POISTUNUT
    )
    ensimmainen: dict[str, int] = {}
    for indeksi, puhelu in enumerate(puhelut):
        for merkinta in puhelu.oireet:
            ensimmainen.setdefault(merkinta.oire, indeksi)
    oireet = sorted(ensimmainen, key=lambda oire: (-mainintoja[oire], ensimmainen[oire]))

    arvioidut = [puhelu for puhelu in puhelut if puhelu.taso is not None]
    viimeisin = arvioidut[-1] if arvioidut else None
    return Oirehistoria(
        puhelut=puhelut,
        oireet=oireet,
        mainintoja=dict(mainintoja),
        tavoitettu=sum(puhelu.tavoitettu for puhelu in puhelut),
        viimeisin_taso=viimeisin.taso if viimeisin else None,
        viimeisin_aika=viimeisin.paattyi if viimeisin else None,
        toistuvin_tila=oireen_tila(oireet[0], puhelut) if oireet else "",
        kuukaudet=kuukausittain(puhelut, oireet),
    )


def kuukausittain(puhelut: list[OirehistorianPuhelu], oireet: list[str]) -> list[Oirekuukausi]:
    """Taulukon sivut kalenterikuukausittain, vanhin ensin. Vain kuukaudet, joilla on
    puheluita: näkymä avautuu uusimman puhelun kuukauteen, joten kuun alussa näkyy edellinen
    kuukausi, kunnes uuden kuun ensimmäinen puhelu tulee. Rivit samassa järjestyksessä kuin
    koko historiassa (oireet), jotta sama oire on sivulta toiselle samalla kohdalla."""
    ryhmat: dict[str, list[OirehistorianPuhelu]] = {}
    for puhelu in puhelut:
        paikallinen = suomen_aika(puhelu.paattyi)  # kuun vaihde Suomen ajassa
        avain = f"{paikallinen.year}-{paikallinen.month:02d}"
        ryhmat.setdefault(avain, []).append(puhelu)

    kuukaudet: list[Oirekuukausi] = []
    for avain, kuukauden_puhelut in sorted(ryhmat.items()):
        vuosi, kuukausi = (int(osa) for osa in avain.split("-"))
        mainitut = {m.oire for puhelu in kuukauden_puhelut for m in puhelu.oireet}
        kuukaudet.append(
            Oirekuukausi(
                avain=avain,
                nimi=f"{KUUKAUDET[kuukausi - 1].capitalize()} {vuosi}",
                puhelut=kuukauden_puhelut,
                oireet=[oire for oire in oireet if oire in mainitut],
                sivut=sivuiksi(kuukauden_puhelut, oireet),
            )
        )
    return kuukaudet


def sivuiksi(puhelut: list[OirehistorianPuhelu], oireet: list[str]) -> list[Oiresivu]:
    """Kuukauden puhelut enintään SIVUN_KOKO kerrallaan, vanhin sivu ensin. Jako tehdään
    lopusta: viimeisellä sivulla on aina uusimmat SIVUN_KOKO puhelua (avautuu ensin), ja vajaa
    sivu jää kuun alkuun."""
    sivut: list[Oiresivu] = []
    loppu = len(puhelut)
    while loppu > 0:
        alku = max(0, loppu - SIVUN_KOKO)
        sivun_puhelut = puhelut[alku:loppu]
        mainitut = {m.oire for puhelu in sivun_puhelut for m in puhelu.oireet}
        sivut.append(
            Oiresivu(
                alku=alku + 1,
                loppu=loppu,
                puhelut=sivun_puhelut,
                oireet=[oire for oire in oireet if oire in mainitut],
            )
        )
        loppu = alku
    return list(reversed(sivut))


def oireen_tila(oire: str, puhelut: list[OirehistorianPuhelu]) -> str:
    """Oireen viimeisin tieto, esim. "poistunut 9.10." tai "viimeksi 6.10., lievä"."""
    for puhelu in reversed(puhelut):
        merkinta = next((m for m in puhelu.oireet if m.oire == oire), None)
        if merkinta is None:
            continue
        paikallinen = suomen_aika(puhelu.paattyi)
        paiva = f"{paikallinen.day}.{paikallinen.month}."
        if merkinta.voimakkuus == POISTUNUT:
            return f"poistunut {paiva}"
        if merkinta.voimakkuus == EI_TIEDOSSA:
            return f"viimeksi {paiva}"
        return f"viimeksi {paiva}, {merkinta.voimakkuus}"
    return ""
