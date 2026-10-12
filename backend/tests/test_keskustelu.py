"""Puhelun tilakone (Jeren malli) ilman Twiliota: säännöt ennen mallia, piilo-ohjeet,
mallin LOPETUS/UUDELLEEN, tukitila, varatoiminta ilman mallia."""

import asyncio

import pytest

from app.mallit import Kysymys, Puhelukonteksti, PuhelunLoppu
from app.puhelu.agentti import KiinteaAgentti
from app.puhelu.keskustelu import (
    EI_KUULUNUT,
    HOITAJALLE,
    HUONON_VOINNIN_JATKO,
    LAAKENEUVO_VASTAUS,
    OIREEN_TARKENNUS,
    Puheluistunto,
    Vaihe,
    kaiun_vuoro,
    kasittele,
)
from app.puhelu.tietopohja import NUMERON_KORVAUS
from app.puhelu.tietopohja import saannot as tietopohja

KYSYMYKSET = [
    Kysymys(tunniste="UUSI_LAAKE", teksti="Miten uusi lääke on sopinut?"),
    Kysymys(tunniste="vointi", teksti="Miten voit tänään?"),
]
AVAUKSET = tietopohja()["avauslause"]
LOPETUSLAUSEET = tietopohja()["lopetuslause"]


def istunto() -> Puheluistunto:
    return Puheluistunto(
        istunto_id="i-1",
        tehtava_id="t-1",
        kysymykset=list(KYSYMYKSET),
        konteksti=Puhelukonteksti(ika=82, aktiivisia_laakkeita=3, muutokset=[]),
        paivita_jono=False,
    )


class Malli:
    """Feikki-LLM: palauttaa annetut vastaukset järjestyksessä ja tallentaa viestit."""

    def __init__(self, *vastaukset: str) -> None:
        self.vastaukset = list(vastaukset)
        self.viestit: list[list[dict[str, str]]] = []

    async def lammita(self, viestit) -> None:
        return None

    async def vastaa(self, viestit):
        self.viestit.append([dict(viesti) for viesti in viestit])
        return self.vastaukset.pop(0) if self.vastaukset else "Selvä. Miten muuten menee?"


class KaatuvaMalli(Malli):
    async def vastaa(self, viestit):
        raise TimeoutError("malli ei vastannut")


def vuoro(tila, puhe, agentti=None, varmuus=0.9):
    return asyncio.run(kasittele(tila, puhe, varmuus, agentti or KiinteaAgentti()))


def test_avaus_kertoo_tekoalysta_eika_katkea():
    tila = istunto()

    avaus = vuoro(tila, None)

    assert avaus.sanottavat[0] in AVAUKSET
    assert "tekoäly" in avaus.sanottavat[0]
    assert avaus.kuuntele
    assert avaus.keskeytymaton == 1


def test_malli_keskustelee_ja_lopettaa_lopetus_merkilla():
    tila = istunto()
    malli = Malli("Mukava kuulla. Miten uusi lääke on sopinut?", "Hyvä kuulla. LOPETUS")
    vuoro(tila, None, malli)

    eka = vuoro(tila, "Ihan hyvin menee.", malli)
    loppu = vuoro(tila, "Hyvin on sopinut.", malli)

    assert eka.sanottavat == ["Mukava kuulla. Miten uusi lääke on sopinut?"]
    assert loppu.sanottavat[0] == "Hyvä kuulla."
    assert loppu.sanottavat[-1] in LOPETUSLAUSEET
    assert not loppu.kuuntele
    assert tila.loppu == PuhelunLoppu.VALMIS


def test_mallille_jarjestelmaohje_kysymykset_ja_keskustelu():
    tila = istunto()
    malli = Malli("Selvä.")
    vuoro(tila, None, malli)

    vuoro(tila, "Ihan hyvin.", malli)

    viestit = malli.viestit[0]
    assert viestit[0]["role"] == "system"
    assert "Miten uusi lääke on sopinut?" in viestit[0]["content"]  # kannan kysymys
    assert "iäkkään" in viestit[0]["content"]  # 82-vuotias: Jeren ikäluokka "iakas"
    assert [viesti["role"] for viesti in viestit[1:]] == ["assistant", "user"]
    assert viestit[-1]["content"].startswith("Ihan hyvin.")
    # Kysymyslista ei unohdu: seuraava kysymättä oleva piilo-ohjeena.
    assert 'kysy seuraavaksi listalta: "Miten uusi lääke on sopinut?"' in viestit[-1]["content"]


def test_oireeseen_tietopohjan_jatkokysymykset_ja_kotihoito_piilo_ohjeena():
    tila = istunto()
    malli = Malli("Ikävä kuulla.")
    vuoro(tila, None, malli)

    vuoro(tila, "Vähän on huimannut aamuisin.", malli)

    viimeinen = malli.viestit[0][-1]["content"]
    assert "(Tietopohja: henkilö mainitsi oire 'huimaus'" in viimeinen
    assert "Kotihoito-ohjeet:" in viimeinen


def test_oireen_jatkokysymysta_ei_korvata_listan_kysymyksella():
    # 11.10. puhelu: "Pystytkö syömään ja juomaan?" korvattiin ihottumakysymyksellä.
    tila = istunto()
    jatkokysymys = "Selvä. Pystytkö syömään ja juomaan normaalisti?"
    malli = Malli(jatkokysymys)
    vuoro(tila, None, malli)
    tila.oire_jatko = 2
    tila.ilman_edistysta = 2

    seuraava = vuoro(tila, "Aamuisin varsinkin.", malli)

    assert seuraava.sanottavat == [jatkokysymys]
    assert tila.seuraava == 0


def test_kiroilu_oireen_yhteydessa_ei_ohita_oiretta():
    # 11.10.: "aikamoista ripulia kun v**** turpa kiinni" -> loukkausrepliikki, ripuli ohi.
    tila = istunto()
    malli = Malli("Ikävä kuulla ripulista. Montako päivää sitä on ollut?")
    vuoro(tila, None, malli)

    seuraava = vuoro(tila, "aikamoista ripulia kun vittu turpa kiinni", malli)

    assert seuraava.sanottavat == ["Ikävä kuulla ripulista. Montako päivää sitä on ollut?"]
    assert "loukkaus:oire_ensin" in tila.syyt
    assert tila.loukkauksia == 0
    assert "(Tietopohja: henkilö mainitsi oire 'ripuli'" in malli.viestit[0][-1]["content"]


def test_vain_ensimmainen_kysymys_ja_hoitajalupaus_ennen_sita():
    # 12.10. testipankki: leikkaus pudotti kysymyksen perässä olleen "Välitän ... hoitajalle".
    tila = istunto()
    malli = Malli(
        "Kuulostaa siltä, että tämä pelottaa sinua. Voitko kertoa lisää? Välitän tämän "
        "asian hoitajalle.\n\nTietojemme mukaan sinulle on merkitty uusi lääke. "
        "Oletko aloittanut sen käytön?"
    )
    vuoro(tila, None, malli)

    seuraava = vuoro(tila, "Ihan hyvin.", malli)

    assert seuraava.sanottavat == [
        "Kuulostaa siltä, että tämä pelottaa sinua. Välitän tämän asian hoitajalle. "
        "Voitko kertoa lisää?"
    ]
    assert "malli:yksi_kysymys" in tila.syyt


def _oire_kotihoitovuoroon(malli: Malli):
    tila = istunto()
    vuoro(tila, None, malli)
    vuoro(tila, "Vähän on huimannut aamuisin.", malli)
    vuoro(tila, "Pari päivää sitten alkoi.", malli)
    return tila, vuoro(tila, "Aamuisin lähinnä.", malli)


def test_kotihoito_lisataan_kun_malli_ohittaa_sen():
    # 12.10. testipankki: malli ohitti kotihoito-ohjeen 14 kertaa 16:sta.
    malli = Malli(
        "Ikävä kuulla. Milloin huimaus alkoi?",
        "Selvä. Milloin sitä on eniten?",
        "Ymmärrän. Miten uusi lääke on sopinut?",
    )

    tila, kotihoito = _oire_kotihoitovuoroon(malli)

    sanottu = " ".join(kotihoito.sanottavat)
    assert "kotihoito:lisatty:huimaus" in tila.syyt
    assert "Nouse ylös rauhallisesti" in sanottu
    assert "Ota yhteyttä hoitajaan, jos olet kaatunut" in sanottu
    assert sanottu.endswith("Miten uusi lääke on sopinut?")


def test_kotihoitoa_ei_lisata_kun_malli_antoi_sen():
    malli = Malli(
        "Ikävä kuulla. Milloin huimaus alkoi?",
        "Selvä. Milloin sitä on eniten?",
        "Nouse ylös rauhallisesti ja istu ensin hetki sängyn reunalla. "
        "Miten uusi lääke on sopinut?",
    )

    tila, kotihoito = _oire_kotihoitovuoroon(malli)

    assert not any(syy.startswith("kotihoito:") for syy in tila.syyt)
    assert kotihoito.sanottavat == [
        "Nouse ylös rauhallisesti ja istu ensin hetki sängyn reunalla. "
        "Miten uusi lääke on sopinut?"
    ]


def test_huonoon_vointiin_ei_lopeteta():
    # 11.10.: "No huonosti" -> malli kiitti ja sanoi LOPETUS.
    tila = istunto()
    malli = Malli("Onpa sääli. Kiitos kun kerroit. LOPETUS")
    vuoro(tila, None, malli)

    seuraava = vuoro(tila, "No huonosti", malli)

    assert seuraava.kuuntele
    assert tila.loppu is None
    assert seuraava.sanottavat[-1].endswith(HUONON_VOINNIN_JATKO)


def test_uuteen_oireeseen_viimeisessa_vastauksessa_ei_lopeteta():
    # 12.10. testipankki: "vapinaa käsissä" viimeiseen listan kysymykseen -> malli lopetti.
    tila = istunto()
    malli = Malli("Olen pahoillani vapinasta. Kiitos, että kerroit. LOPETUS")
    vuoro(tila, None, malli)
    tila.seuraava = len(tila.kysymykset)  # lista käyty

    seuraava = vuoro(tila, "ihan ok muuten, mutta on ollut vapinaa käsissä", malli)

    assert seuraava.kuuntele
    assert tila.loppu is None
    assert seuraava.sanottavat[-1].endswith(OIREEN_TARKENNUS)
    assert "malli:lopetus_ohitettu_oire" in tila.syyt


@pytest.mark.parametrize(
    "vastaus",
    [
        # 12.10. testipankki: kuulumisvastaus luettiin kieltäytymiseksi ja puhelu päättyi.
        "en muista ihan tarkkaan, mutta ihan hyvin",
        "ei mitään erikoista",
        "no ei tässä mitään ihmeempiä",
        "ei nyt kummempia",
        "en oikein tiedä, ihan hyvin kai",
    ],
)
def test_kieltomuotoinen_kuulumisvastaus_ei_lopeta_puhelua(vastaus):
    tila = istunto()
    malli = Malli("Selvä. Miten uusi lääke on sopinut?")
    vuoro(tila, None, malli)

    seuraava = vuoro(tila, vastaus, malli)

    assert seuraava.kuuntele
    assert not any(syy.startswith("lopetuspyynto") for syy in tila.syyt)


@pytest.mark.parametrize("vastaus", ["en ehdi nyt", "soita myöhemmin", "ei ole aikaa nyt"])
def test_selva_kieltaytyminen_avauksen_jalkeen_on_lopetuspyynto(vastaus):
    tila = istunto()
    vuoro(tila, None, Malli())

    vuoro(tila, vastaus, Malli())

    assert any(syy.startswith("lopetuspyynto") for syy in tila.syyt)


def test_kaatumisen_jalkeen_ei_paase_ylos_on_hata():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Kaaduin äsken keittiössä.")

    hata = vuoro(tila, "En pääse ylös.")

    assert tila.hatamerkki == "kaatunut_lattialla"
    assert not hata.kuuntele
    assert "112" in " ".join(hata.sanottavat)
    assert tila.tulos().yhteenveto.taso == "punainen"


def test_huoli_saa_piilo_ohjeen_pysahtya():
    tila = istunto()
    malli = Malli("Ymmärrän, että se huolettaa.")
    vuoro(tila, None, malli)

    vuoro(tila, "Olen vähän huolissani tästä lääkkeestä.", malli)

    assert "(Ohje: Henkilö kertoi juuri huolesta" in malli.viestit[0][-1]["content"]


def test_uudelleen_merkki_poistetaan_ja_vastaus_merkitaan_epavarmaksi():
    tila = istunto()
    malli = Malli("UUDELLEEN Onko lääke sopinut hyvin, kyllä vai ei?")
    vuoro(tila, None, malli)

    vastaus = vuoro(tila, "Kävin kaupassa.", malli)

    assert vastaus.sanottavat == ["Onko lääke sopinut hyvin, kyllä vai ei?"]
    assert tila.vastaukset[-1].epavarma


def test_keksitty_puhelinnumero_korvataan():
    tila = istunto()
    malli = Malli("Soita numeroon 992-200. Miten muuten menee?")
    vuoro(tila, None, malli)

    vastaus = vuoro(tila, "Vähän väsyttää.", malli)

    assert vastaus.sanottavat == [f"{NUMERON_KORVAUS} Miten muuten menee?"]


def test_hatamerkki_ohittaa_mallin_ja_lopettaa():
    tila = istunto()
    malli = Malli()
    vuoro(tila, None, malli)

    hata = vuoro(tila, "Rintaan sattuu.", malli)

    assert hata.sanottavat[-1] == tietopohja()["hataviestit"]["rintakipu"]
    assert not hata.kuuntele
    assert malli.viestit == []
    assert tila.loppu == PuhelunLoppu.HATAMERKKI
    assert tila.hatamerkki == "rintakipu"


def test_hatasana_tunnistetaan_vaikka_varmuus_matala():
    tila = istunto()
    vuoro(tila, None)

    hata = vuoro(tila, "rintaan sattuu", varmuus=0.2)

    assert tila.loppu == PuhelunLoppu.HATAMERKKI
    assert not hata.kuuntele


def test_itsetuhoisuus_jaa_tukitilaan_eika_katkaise():
    tila = istunto()
    malli = Malli("Olen tässä. Oletko nyt turvassa?")
    vuoro(tila, None, malli)

    hata = vuoro(tila, "En halua enää elää.", malli)
    tuki = vuoro(tila, "En tiedä.", malli)

    assert hata.kuuntele
    assert tila.vaihe == Vaihe.TUKI
    assert tila.tukitila == "raskaita ajatuksia"
    assert "TUKI" in malli.viestit[0][0]["content"]  # tukitilan järjestelmäohje
    assert tuki.sanottavat == ["Olen tässä. Oletko nyt turvassa?"]


def test_tukitilassa_lopetus_ohitetaan_kun_puhe_yha_raskasta():
    tila = istunto()
    malli = Malli("LOPETUS")
    vuoro(tila, None, malli)
    vuoro(tila, "En halua enää elää.", malli)

    vastaus = vuoro(tila, "Olen tosi huolissani kaikesta.", malli)

    assert vastaus.kuuntele
    assert vastaus.sanottavat[0] in tietopohja()["tuki_jatko"]


def test_lopetuspyynto_lopettaa_heti():
    # 12.10.: yksi pyyntö riittää (aiemmin ensin kuulumiskysymys).
    tila = istunto()
    vuoro(tila, None)

    loppu = vuoro(tila, "Pitää nyt mennä.")

    assert not loppu.kuuntele
    assert loppu.sanottavat[0] in tietopohja()["lopetuspyynto"]["lopetus"]
    assert tila.loppu == PuhelunLoppu.LOPETTI


def test_lopetuspyynto_huolen_jalkeen_lopettaa_heti_hoitajan_soitolla():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Vähän on huimannut aamuisin.")

    loppu = vuoro(tila, "No lopetetaan nyt tämä puhelu.")

    assert not loppu.kuuntele
    assert loppu.sanottavat[0] in tietopohja()["lopetuspyynto"]["lopetus_huolestuttava"]


def test_itsetuhoisen_puheen_jalkeen_kysytaan_turvallisuudesta_ennen_lopetusta():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "en halua enää elää")

    eka = vuoro(tila, "lopetetaan tämä puhelu")
    toka = vuoro(tila, "joo olen turvassa")

    assert eka.kuuntele
    assert eka.sanottavat[0] in tietopohja()["lopetuspyynto"]["turvallisuuskysymys"]
    assert not toka.kuuntele


def test_loukkaus_kolmannella_kerralla_lopettaa():
    tila = istunto()
    vuoro(tila, None)

    for teksti in ("tyhmä kone", "turpa kiinni"):
        assert vuoro(tila, teksti).kuuntele
    loppu = vuoro(tila, "haista vittuun")

    assert not loppu.kuuntele
    assert tila.loppu == PuhelunLoppu.LOPETTI


def test_hoitajapyynto_lopettaa_kiintealla_viestilla():
    tila = istunto()
    vuoro(tila, None)

    vastaus = vuoro(tila, "Haluan puhua hoitajan kanssa.")

    assert vastaus.sanottavat == [HOITAJALLE]
    assert tila.loppu == PuhelunLoppu.HALUAA_HOITAJAN


def test_laakeneuvo_kiintea_vastaus_ilman_mallia():
    tila = istunto()
    malli = Malli()
    vuoro(tila, None, malli)

    vastaus = vuoro(tila, "Pitäisikö minun lopettaa se uusi lääke?", malli)

    assert vastaus.sanottavat[0] == LAAKENEUVO_VASTAUS
    assert tila.vastaukset[-1].kysymys_hoitajalle
    assert malli.viestit == []


def test_usein_kysyttyyn_vastataan_saannolla():
    tila = istunto()
    malli = Malli()
    vuoro(tila, None, malli)

    vastaus = vuoro(tila, "Mikä lääke se on?", malli)

    assert "tietosuojan" in vastaus.sanottavat[0]
    assert malli.viestit == []


def test_toistopyynto_toistaa_kysymyksen():
    tila = istunto()
    malli = Malli("Kiitos. Miten uusi lääke on sopinut?")
    vuoro(tila, None, malli)
    vuoro(tila, "Ihan hyvin.", malli)

    toisto = vuoro(tila, "Mitä kysyit?", malli)

    assert toisto.sanottavat[0] in tietopohja()["uudelleenkysymys"]["toistoviesti"]
    assert toisto.sanottavat[1] == "Miten uusi lääke on sopinut?"


def test_matala_varmuus_kysytaan_uudelleen_ja_sitten_siirrytaan():
    tila = istunto()
    malli = Malli("Selvä, siirrytään eteenpäin. Miten voit tänään?")
    vuoro(tila, None, malli)

    vastaukset = [vuoro(tila, "mmh jotain epäselvää", malli, varmuus=0.2) for _ in range(3)]

    epaselvat = tietopohja()["uudelleenkysymys"]["epaselva_viesti"]
    assert vastaukset[0].sanottavat[0] in epaselvat
    assert vastaukset[1].sanottavat[0] in epaselvat
    # Raja täynnä: malli saa ohjeen siirtyä eteenpäin.
    assert "(Ohje: Tähän ei saatu selvää vastausta" in malli.viestit[0][-1]["content"]
    assert tila.vastaukset[0].epavarma


def test_hiljaisuus_toistaa_kerran_ja_sitten_lopettaa():
    tila = istunto()
    avaus = vuoro(tila, None)

    toisto = vuoro(tila, "")
    loppu = vuoro(tila, None)

    assert toisto.sanottavat[0] == EI_KUULUNUT
    assert toisto.sanottavat[1] in avaus.sanottavat[0]
    assert not loppu.kuuntele
    assert tila.loppu == PuhelunLoppu.EI_VASTAUSTA


def test_ilman_mallia_kannan_kysymykset_jarjestyksessa():
    tila = istunto()
    vuoro(tila, None)

    eka = vuoro(tila, "Ihan hyvin.")
    toka = vuoro(tila, "Hyvin on sopinut.")
    loppu = vuoro(tila, "Hyvin.")

    assert eka.sanottavat == ["Kiitos.", KYSYMYKSET[0].teksti]
    assert toka.sanottavat == ["Kiitos.", KYSYMYKSET[1].teksti]
    assert loppu.sanottavat[-1] in LOPETUSLAUSEET
    assert tila.loppu == PuhelunLoppu.VALMIS


def test_mallin_virhe_ei_kaada_puhelua():
    tila = istunto()
    vuoro(tila, None)

    vastaus = vuoro(tila, "Ihan hyvin.", KaatuvaMalli())

    assert vastaus.kuuntele
    assert vastaus.sanottavat == ["Kiitos.", KYSYMYKSET[0].teksti]
    assert any(syy.startswith("agentti_virhe:TimeoutError") for syy in tila.syyt)


def test_tulos_sisaltaa_saantojen_triagen():
    tila = istunto()
    vuoro(tila, None)
    vuoro(tila, "Olen kaatunut eilen.")

    tulos = tila.tulos()

    assert tulos.yhteenveto.taso.value == "keltainen"
    assert "kaatuminen" in tulos.yhteenveto.saanto.huolestuttavat_oireet


def test_litteroinnissa_ei_piilo_ohjeita():
    tila = istunto()
    malli = Malli("Ikävä kuulla.")
    vuoro(tila, None, malli)
    vuoro(tila, "Vähän on huimannut.", malli)

    assert "Tietopohja" not in tila.litterointi()
    assert "Henkilö: Vähän on huimannut." in tila.litterointi()


def test_kaikutesti_toistaa_kuullun_ja_hatasanan():
    avaus, eka_tulos = kaiun_vuoro(0, None, None)
    vuoro_, tulos = kaiun_vuoro(1, "rintaan sattuu", 0.8)

    assert avaus.kuuntele and eka_tulos is None
    assert vuoro_.sanottavat[0] == "Kuulin: rintaan sattuu."
    assert tulos.hatamerkki == "rintakipu"
