"""Jeren tietopohjan regressiotestit (puheavustaja/testit.py, suomenkieliset osat).

Lisää tänne jokainen oikeassa puhelussa havaittu ohitettu tai väärä tunnistus, niin sama
virhe ei palaa, kun sääntöjä muokataan.
"""

import pytest

from app.puhelu import tietopohja as t


def tunnista(teksti: str) -> t.Tietopohjahavainto:
    return t.tarkista(teksti)


@pytest.mark.parametrize(
    ("teksti", "odotettu"),
    [
        ("rinta-kipua on ollut aamusta", "rintakipu"),
        ("rintaa puristaa", "rintakipu"),
        ("mä en saa henkee", "hengenahdistus"),
        # 12.10. testipankki: "meinaa saada" jäi tunnistamatta, puhelu vihreäksi.
        ("en meinaa saada henkeä", "hengenahdistus"),
        ("en oikein saa henkeä", "hengenahdistus"),
        ("en tahdo saada happea", "hengenahdistus"),
        ("suupieli roikkuu", "halvausoireet"),
        ("otin lääkettä vähän liikaa tahallaan", "yliannostus"),
        ("haluun ottaa vähän yliannostuksen", "yliannostus"),
        ("nielin kaikki pillerit", "yliannostus"),
        ("en halua enää elää", "itsetuhoisuus"),
        ("mä en jaksa elää", "itsetuhoisuus"),
        ("tapan itteni", "itsetuhoisuus"),
        ("toivon etten heräisi aamulla", "itsetuhoisuus"),
        ("haluan kuolla en jaksa", "itsetuhoisuus"),
        # 11.10. puhelu: puheentunnistus pilkkoi sanan, malli sai sen ilman hätäsääntöä.
        ("Olen itse tuhoinen.", "itsetuhoisuus"),
        ("on ollut itsetuhoisia ajatuksia", "itsetuhoisuus"),
        ("rintakipu ei mene ohi", "rintakipu"),
        ("rintakipua ei ole helpottanut", "rintakipu"),
        ("kurkku menee umpeen", "vakava_allergia"),
        ("oksensin verta", "runsas_verenvuoto"),
        # 12.10. puhelu: kaatunut, verta päässä ja lattialla -> jäi keltaiseksi ilman 112:ta.
        ("Pää vuotaan aika pahasti verta, että mitäs me nyt tehdään?", "runsas_verenvuoto"),
        ("haava vuotaa kovasti", "runsas_verenvuoto"),
        ("Täällä Mä makaan lattialla.", "kaatunut_lattialla"),
        ("kaaduin enkä pääse ylös", "kaatunut_lattialla"),
    ],
)
def test_hatamerkki(teksti, odotettu):
    assert odotettu in tunnista(teksti).hata


@pytest.mark.parametrize(
    "teksti",
    [
        "ei ole ollut rintakipua",
        "en ole ottanut lääkettä liikaa",
        "hengenahdistusta ei ole ollut",
        "rintakipua ei ole enää ollut viikkoon",
    ],
)
def test_kielto_ei_halyta(teksti):
    assert tunnista(teksti).hata == []


def test_kielletty_itsetuhoisuus_varmistetaan_eika_halyta():
    havainto = tunnista("en ole itsetuhoinen")

    assert havainto.hata == []
    assert "itsetuhoisuus" in havainto.hata_kielletty


def test_kieltomuoto_varmistetaan():
    havainto = tunnista("ei mikään auta haluan kuolla")

    assert set(havainto.hata_kielletty) & t.VARMISTETTAVAT or havainto.hata
    assert "toivottomuus" in havainto.vakavat


@pytest.mark.parametrize(
    ("teksti", "odotettu"),
    [
        ("setä kosketti mua luvatta", "kaltoinkohtelu"),
        ("mieheni lyö minua", "kaltoinkohtelu"),
        ("poika vie mun eläkkeen", "kaltoinkohtelu"),
        ("pelkään mennä kotiin", "kaltoinkohtelu"),
        ("sienet alkaa laulamaan mulle metsässä", "aistiharhat"),
        ("kuulen ääniä jotka käskee mua", "aistiharhat"),
        ("mikään ei enää kannata", "toivottomuus"),
    ],
)
def test_vakava_huoli(teksti, odotettu):
    assert odotettu in tunnista(teksti).vakavat


@pytest.mark.parametrize(
    "teksti", ["se kosketti minua syvästi", "hyvin menee kiitos", "lääke on auttanut"]
)
def test_ei_vaaria_halytyksia(teksti):
    havainto = tunnista(teksti)

    assert (havainto.hata, havainto.vakavat) == ([], [])


@pytest.mark.parametrize(
    "teksti",
    ["olen vähän huolissani tästä lääkkeestä", "mua pelottaa", "en tiedä mitä tehdä"],
)
def test_huoli(teksti):
    assert tunnista(teksti).huoli


@pytest.mark.parametrize(
    "teksti", ["otin uuden annoksen mukaan", "olen ottanut annoksen ohjeen mukaan"]
)
def test_sanan_keskelta_ei_tunnisteta_oiretta(teksti):
    """11.10.: "annoksen" osui kuvioon "oksen" -> pahoinvointi ja keltainen taso."""
    havainto = tunnista(teksti)

    assert (havainto.oireet, havainto.keltainen) == ([], [])


@pytest.mark.parametrize(
    ("teksti", "odotettu"),
    [
        # 12.10. testipankki: taivutusmuodot jäivät tunnistamatta, lihaskipu jäi vihreäksi.
        ("hyvin kiitos, mutta lihaksia on kolottanut eilen illalla", "lihaskipu"),
        ("lihakset on kipeät siitä asti kun aloitin sen lääkkeen", "lihaskipu"),
        ("jalat on ollut paksut", "turvotus"),
        ("vatsa on ollu vähä sekaisin", "ripuli"),
        ("joo vähän vapisee", "vapina"),
        # 12.10. puhelu: "pääkipuja" ei ollut päänsärky.
        ("hirveä darra iski, pääkipuja silleen tosi vaikeet", "päänsärky"),
        ("ihan ok muuten, mutta en ole jaksanut juoda", "ruokahaluttomuus ja juominen"),
        ("ruoka ei oikein maistu", "ruokahaluttomuus ja juominen"),
    ],
)
def test_oireen_taivutusmuodot(teksti, odotettu):
    assert odotettu in tunnista(teksti).oireet


@pytest.mark.parametrize(
    "teksti",
    ["ei, lihakset on kunnossa", "ei ole kolottanut", "kyllä on maistunut", "ruokahalu on hyvä"],
)
def test_kielletty_oire_ei_tunnistu(teksti):
    assert tunnista(teksti).oireet == []


@pytest.mark.parametrize("teksti", ["kyllä, eilen oli kuumetta", "on ollut vilunväristyksiä"])
def test_kuume_nostaa_keltaiseksi(teksti):
    """11.10. testipankki: kuume jäi vihreäksi (solunsalpaajilla kiireellinen)."""
    assert t.kiireellisyys([tunnista(teksti)]) == "keltainen"


def test_kielletty_kuume_ei_nosta():
    assert t.kiireellisyys([tunnista("ei ole ollut kuumetta")]) == "vihreä"


def test_kielletty_huoli_ei_ole_huoli():
    assert not tunnista("en ole huolissani").huoli


def test_kiireellisyys():
    assert t.kiireellisyys([tunnista("setä kosketti mua luvatta")]) == "punainen"
    assert t.kiireellisyys([tunnista("olen huolissani")]) == "keltainen"
    assert t.kiireellisyys([tunnista("hyvin menee")]) == "vihreä"


@pytest.mark.parametrize("teksti", ["kyllä", "joo välillä", "en tiedä", "on ollut"])
def test_myontava_varmistukseen(teksti):
    assert t.on_myontava(teksti)


@pytest.mark.parametrize("teksti", ["ei", "ei ole", "en ole"])
def test_kieltava_varmistukseen(teksti):
    assert not t.on_myontava(teksti)


def test_itsetuhoviestissa_lammin_pyynto_hatanumero_ja_kriisipuhelin():
    edelliset: dict[str, str] = {}
    for _ in range(15):
        viesti = t.teksti("hataviestit.itsetuhoisuus", edelliset)

        assert any(x in viesti.lower() for x in ("älä tee itsellesi", "ettet satuta itseäsi"))
        assert "112" in viesti
        assert "09 2525 0111" in viesti
        assert viesti.rstrip().endswith("?")


def test_tukitilan_lisaohjeet_vain_itsetuhossa():
    assert "älä väittele" in t.tukitilan_ohje("x", itsetuho=True).lower()
    assert "vaarallisia välineitä" not in t.tukitilan_ohje("x", itsetuho=False)


@pytest.mark.parametrize(
    "teksti",
    ["tyhmä kone", "sä oot ihan idiootti", "haista vittuun", "turpa kiinni", "vihaan sinua"],
)
def test_loukkaus(teksti):
    assert tunnista(teksti).loukkaus


@pytest.mark.parametrize("teksti", ["tapan sut", "räjäytän koko paikan"])
def test_uhkaus(teksti):
    havainto = tunnista(teksti)

    assert havainto.uhkaus and havainto.loukkaus


@pytest.mark.parametrize(
    "teksti",
    [
        "vittu kun sattuu",
        "pää on ihan paska tänään",
        "en vihaa sinua",
        "ole hyvä",
        "mieheni lyö minua",
        "olen ollut hiljaa",
    ],
)
def test_kiroilu_ja_kielto_eivat_ole_loukkaus(teksti):
    assert not tunnista(teksti).loukkaus


def test_loukkausten_taso():
    assert t.kiireellisyys([tunnista("tyhmä kone")]) == "vihreä"
    assert t.kiireellisyys([tunnista("tyhmä kone"), tunnista("turpa kiinni")]) == "keltainen"
    assert t.kiireellisyys([tunnista("tapan sut")]) == "keltainen"


def test_versiot_vaihtelevat_eika_sama_perakkain():
    edelliset: dict[str, str] = {}
    nahty = set()
    edellinen = None
    for _ in range(30):
        versio = t.teksti("uudelleenkysymys.ohitusviesti", edelliset)

        assert versio != edellinen
        edellinen = versio
        nahty.add(versio)
    assert len(nahty) >= 3


@pytest.mark.parametrize(
    "avain",
    [
        "lopetuslause_tuki",
        "lopetuspyynto.lopetus_huolestuttava",
        "vakavat_huolet.kaltoinkohtelu.viesti",
    ],
)
def test_jokaisessa_versiossa_hatanumero(avain):
    """Vaihtelu ei saa pudottaa hätänumeroa."""
    edelliset: dict[str, str] = {}
    for _ in range(15):
        assert "112" in t.teksti(avain, edelliset)


def test_korkeampi_taso_saanto_vain_nostaa():
    assert t.korkeampi_taso("keltainen", "vihreä") == "keltainen"
    assert t.korkeampi_taso("vihreä", "punainen") == "punainen"
    assert t.korkeampi_taso("tuntematon") == "keltainen"


@pytest.mark.parametrize(
    "teksti",
    # 12.10. testipankki: vapina puuttui tietopohjasta, ja malli lopetti puhelun sen kohdalla.
    ["on ollut vapinaa käsissä", "kädet on täristy vähän", "käsissä on ollut tärinää"],
)
def test_vapina_tunnistetaan(teksti):
    assert "vapina" in tunnista(teksti).oireet


def test_kielletty_vapina_ei_ole_oire():
    assert "vapina" not in tunnista("ei ole ollut vapinaa").oireet


@pytest.mark.parametrize(
    ("teksti", "odotettu"),
    [
        # 12.10. puhelu: kolme lopetuspyyntöä jäi tunnistamatta.
        ("Alright mä lopetan tää puhelu.", True),
        ("Voit lopettamalla tämän puhelun kiitos.", True),
        ("No niin, eiköhän me nyt lopeteta.", True),
        # Lääkkeen lopettaminen ei ole lopetuspyyntö (11.10. opittu).
        ("lopetin sen lääkkeen", False),
        ("mä lopetan nyt sen lääkkeen", False),
        ("lääkäri sanoi että lopetetaan se lääke", False),
    ],
)
def test_lopetuspyynto(teksti, odotettu):
    assert tunnista(teksti).lopetuspyynto is odotettu


@pytest.mark.parametrize(
    "teksti",
    [
        "nenästä vuotaa vähän verta",
        "en pääse ylös sängystä aamuisin",
        "kaaduin viime viikolla mutta pääsin ylös",
        "pyyhin lattiaa eilen",
    ],
)
def test_lievat_eivat_ole_hatamerkkeja(teksti):
    assert tunnista(teksti).hata == []
