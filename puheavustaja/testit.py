#!/usr/bin/env python3
"""Tunnistuksen ja analyysin regressiotestit (ei tarvitse Ollamaa eikä mikrofonia).

    python testit.py

Lisää tänne jokainen oikeassa puhelussa havaittu ohitettu tai väärä tunnistus,
niin sama virhe ei palaa, kun sääntöjä muokataan.
"""
import json
import unittest

import tietopohja as t
import puheavustaja as p


def tunnista(teksti):
    return t.tarkista(teksti)


class Hatamerkit(unittest.TestCase):
    def test_hata(self):
        tapaukset = {
            "rinta-kipua on ollut aamusta": "rintakipu",
            "rintaa puristaa": "rintakipu",
            "mä en saa henkee": "hengenahdistus",
            "suupieli roikkuu": "halvausoireet",
            "otin lääkettä vähän liikaa tahallaan": "yliannostus",
            "haluun ottaa vähän yliannostuksen": "yliannostus",
            "nielin kaikki pillerit": "yliannostus",
            "en halua enää elää": "itsetuhoisuus",
            "mä en jaksa elää": "itsetuhoisuus",
            "tapan itteni": "itsetuhoisuus",
            "toivon etten heräisi aamulla": "itsetuhoisuus",
            "haluan kuolla en jaksa": "itsetuhoisuus",
            "rintakipu ei mene ohi": "rintakipu",
            "rintakipua ei ole helpottanut": "rintakipu",
        }
        for teksti, odotettu in tapaukset.items():
            with self.subTest(teksti=teksti):
                self.assertIn(odotettu, tunnista(teksti).hata)

    def test_kielto_ei_halyta(self):
        for teksti in ["ei ole ollut rintakipua", "en ole ottanut lääkettä liikaa",
                       "hengenahdistusta ei ole ollut", "rintakipua ei ole enää ollut viikkoon"]:
            with self.subTest(teksti=teksti):
                self.assertEqual(tunnista(teksti).hata, [])

    def test_kieltomuoto_varmistetaan(self):
        h = tunnista("ei mikään auta haluan kuolla")
        self.assertTrue(set(h.hata_kielletty) & t.VARMISTETTAVAT or h.hata)
        self.assertIn("toivottomuus", h.vakavat)


class VakavatJaHuoli(unittest.TestCase):
    def test_vakavat(self):
        tapaukset = {
            "setä kosketti mua luvatta": "kaltoinkohtelu",
            "mieheni lyö minua": "kaltoinkohtelu",
            "poika vie mun eläkkeen": "kaltoinkohtelu",
            "pelkään mennä kotiin": "kaltoinkohtelu",
            "sienet alkaa laulamaan mulle metsässä": "aistiharhat",
            "kuulen ääniä jotka käskee mua": "aistiharhat",
            "mikään ei enää kannata": "toivottomuus",
        }
        for teksti, odotettu in tapaukset.items():
            with self.subTest(teksti=teksti):
                self.assertIn(odotettu, tunnista(teksti).vakavat)

    def test_ei_vaaria_halytyksia(self):
        for teksti in ["se kosketti minua syvästi", "hyvin menee kiitos",
                       "lääke on auttanut", "kävin kaupassa"]:
            with self.subTest(teksti=teksti):
                h = tunnista(teksti)
                self.assertEqual((h.hata, h.vakavat), ([], []))

    def test_huoli(self):
        for teksti in ["olen vähän huolissani tästä lääkkeestä", "mua pelottaa",
                       "en tiedä mitä tehdä", "itkettää koko ajan"]:
            with self.subTest(teksti=teksti):
                self.assertTrue(tunnista(teksti).huoli)
        self.assertFalse(tunnista("en ole huolissani").huoli)

    def test_taso(self):
        self.assertEqual(t.kiireellisyys([tunnista("setä kosketti mua luvatta")]), "punainen")
        self.assertEqual(t.kiireellisyys([tunnista("olen huolissani")]), "keltainen")


class Puheentunnistus(unittest.TestCase):
    def test_kaikuharha(self):
        kaiku = "pahoinvointi, ripuli, ummetus, huimaus, kaatuminen, päänsärky, väsymys"
        self.assertTrue(t.on_kaikuharha(kaiku))
        self.assertTrue(t.on_kaikuharha("Onko sinulla ollut pahoinvointia?",
                                        "Onko sinulla ollut pahoinvointia?"))
        self.assertFalse(t.on_kaikuharha("vähän on ollut pahoinvointia aamuisin",
                                         "Onko sinulla ollut pahoinvointia?"))
        # kaiusta ei poimita oireita, mutta hätä tarkistetaan silti
        self.assertEqual(t.tarkista(kaiku, kaikuharha=True).oireet, [])

    def test_myontava(self):
        for x in ["kyllä", "joo välillä", "en tiedä", "on ollut"]:
            self.assertTrue(t.on_myontava(x), x)
        for x in ["ei", "ei ole", "en ole"]:
            self.assertFalse(t.on_myontava(x), x)


class Kielet(unittest.TestCase):
    def test_kielen_tunnistus(self):
        self.assertEqual(t.tunnista_kieli("I am a bit worried about this medicine"), "en")
        self.assertEqual(t.tunnista_kieli("Hej, jag mår ganska bra"), "sv")
        self.assertEqual(t.tunnista_kieli("minä olen ihan hyvin kiitos", "en"), "fi")
        self.assertEqual(t.tunnista_kieli("ok", "sv"), "sv")       # epäselvä -> ei vaihdeta

    def test_hata_ja_huoli_muilla_kielilla(self):
        self.assertIn("rintakipu", tunnista("I have chest pain").hata)
        self.assertIn("rintakipu", tunnista("jag har ont i bröstet").hata)
        self.assertIn("itsetuhoisuus", tunnista("I want to die").hata)
        self.assertIn("itsetuhoisuus", tunnista("jag vill dö").hata)
        self.assertEqual(tunnista("I don't have chest pain").hata, [])
        self.assertEqual(tunnista("jag har inte ont i bröstet").hata, [])
        self.assertIn("kaltoinkohtelu", tunnista("my son takes my money").vakavat)
        self.assertTrue(tunnista("jag är lite orolig").huoli)
        self.assertFalse(tunnista("det hjälpte bra").huoli)
        self.assertIn("huimaus", tunnista("I have felt dizzy").oireet)   # oire kirjataan suomeksi

    def test_kieli_ei_vaihdu_liian_herkasti(self):
        v = t.Kielenvalitsin("fi")
        self.assertEqual(v.paivita("ok"), "fi")                       # lyhyt vastaus
        self.assertEqual(v.paivita("yes"), "fi")
        self.assertEqual(v.paivita("no niin, se on ollut ihan ok"), "fi")
        # yksittäinen englanninkielinen sana suomen seassa ei vaihda
        self.assertEqual(v.paivita("mä oon ollut vähän down tänään"), "fi")
        # Whisperin varma arvaus ei yksin vaihda, jos teksti on suomea
        self.assertEqual(v.paivita("minulla on ollut hyvä viikko", "en", 0.95), "fi")

    def test_kieli_vaihtuu_kun_selvaa(self):
        v = t.Kielenvalitsin("fi")
        self.assertEqual(v.paivita("I am fine thank you, it has been a good week"), "en")
        # takaisin suomeen vasta selvällä suomella
        self.assertEqual(v.paivita("joo"), "en")
        self.assertEqual(v.paivita("no ei se mitään kyllä mä oon ihan hyvin"), "fi")

    def test_kaksi_epavarmaa_vuoroa_vaihtaa(self):
        v = t.Kielenvalitsin("fi")
        self.assertEqual(v.paivita("joo jag mår bra idag"), "fi")     # sekakieli: vasta ehdokas
        self.assertEqual(v.paivita("joo det är bra med mig"), "sv")   # 2. vuoro: vaihto
        v = t.Kielenvalitsin("fi")
        self.assertEqual(v.paivita("jag mår bra idag"), "sv")         # selvä ruotsi: heti

    def test_mallin_kehys_samalla_kielella(self):
        profiili = t.hae_profiili(None)
        en = t.puhelun_tehtava(profiili, "en")
        sv = t.puhelun_tehtava(profiili, "sv")
        self.assertIn("CALL TASK", en)
        self.assertIn("SAMTALETS UPPGIFT", sv)
        self.assertIn("LOPETUS", en)
        self.assertIn("Reply only in English", t.piilo_ohje("muistutus", "en"))
        self.assertEqual(t.piilo_ohje("muistutus", "fi"), "")
        self.assertIn("SUPPORT", t.tukitilan_tehtava("x", "en"))

    def test_kaannokset_kattavat(self):
        """Jokaiselle suomenkieliselle kiinteälle repliikille on käännös."""
        avaimet = ["avauslause", "lopetuslause", "lopetuslause_tuki", "hataviesti", "varmistuskysymys",
                   *[f"hataviestit.{k}" for k in t.SAANNOT["hataviestit"] if not k.startswith("_")],
                   *[f"hataviestit.{k}" for k in t.SAANNOT["hatamerkit"] if not k.startswith("_")],
                   *[f"vakavat_huolet.{k}.viesti" for k in t.SAANNOT["vakavat_huolet"] if not k.startswith("_")],
                   *[f"lopetuspyynto.{k}" for k in ("kysy_syy", "turvallisuuskysymys", "lopetus", "lopetus_huolestuttava")],
                   *[f"uudelleenkysymys.{k}" for k in ("epaselva_viesti", "toistoviesti", "ohitusviesti")]]
        for kieli in t.MUUT_KIELET:
            for avain in avaimet:
                with self.subTest(kieli=kieli, avain=avain):
                    self.assertIn(avain, t.KIELET[kieli]["tekstit"])


class Huudahdukset(unittest.TestCase):
    def test_tunnistus(self):
        import aani
        k = aani.huudahdus_kuvio()
        self.assertEqual(aani.jaa_huudahdus("Oho! Satutitko itsesi?", k)[0], ("Oho!", True))
        self.assertEqual(aani.jaa_huudahdus("Voi ei, olen pahoillani.", k),
                         [("Voi ei!", True), ("olen pahoillani.", False)])
        self.assertEqual(aani.jaa_huudahdus("Whoa, that is a lot.", k)[0], ("Whoa!", True))
        self.assertEqual(aani.jaa_huudahdus("Oj då, det låter jobbigt.", k)[0], ("Oj då!", True))
        for ei in ["Voisitko kertoa lisää?", "Ohjeena on juoda vettä.", "Voi olla, että se auttaa.",
                   "Ojentaisitko kätesi?"]:
            self.assertEqual(aani.jaa_huudahdus(ei, k), [(ei, False)], ei)

    def test_huudahdus_nopeampi_ja_kovempi(self):
        """Korvaava ääni: huudahdusosa tulee lyhyempänä ja kovempana kuin muu puhe."""
        import queue
        import threading
        import numpy as np
        import aani

        class Pala:
            def __init__(self, a):
                self.audio_float_array = a

        class Aani:
            class config:
                sample_rate = 1000

            def synthesize(self, teksti, config):
                n = int(len(teksti) * 20 * config.length_scale)
                yield Pala(np.full(n, 0.3, dtype=np.float32))

        class Asetus:
            def __init__(self, length_scale, volume):
                self.length_scale = length_scale

        p_ = object.__new__(aani.Puhuja)
        p_.voice = Aani()
        p_._aanet = {"fi": p_.voice}
        p_.tauko_s = 0.0
        p_.config = Asetus(1 / 0.85, 1)
        p_.config_huuto = Asetus(1 / (0.85 * 1.35), 1)
        p_.huuto_vahvistus = 1.7
        p_.huuto_kuvio = aani.huudahdus_kuvio()
        p_.tekstit, p_.aanet = queue.Queue(), queue.Queue()
        threading.Thread(target=p_._syntetisoi, daemon=True).start()
        p_.tekstit.put(("Oho!", "fi"))
        p_.tekstit.put(("Oho.", "fi"))   # sama pituus, ei korostusta -> verrokki
        p_.tekstit.join()
        huuto, _ = p_.aanet.get()
        # Verrokki: sama teksti ilman tunnistusta
        p_.huuto_kuvio = aani.huudahdus_kuvio(["eitunnistu"])
        p_.tekstit.put(("Oho!", "fi"))
        p_.tekstit.join()
        p_.aanet.get()
        tavallinen, _ = p_.aanet.get()
        aani_huuto = huuto[: int(len("Oho!") * 20 / (0.85 * 1.35))]
        self.assertGreater(float(np.max(aani_huuto)), float(np.max(tavallinen)) * 1.5)
        puheen_pituus = int(len("Oho!") * 20 / 0.85)
        self.assertLess(len(aani_huuto), puheen_pituus)
        self.assertLessEqual(float(np.max(np.abs(huuto))), 0.98)


class PuhelunAsetukset(unittest.TestCase):
    def test_ikaluokka(self):
        self.assertEqual(t.ratkaise_ikaluokka("auto", {"ikaryhma": "80-89"}), "iakas")
        self.assertEqual(t.ratkaise_ikaluokka("auto", {"ikaryhma": "60-69"}), "ikaihminen")
        self.assertEqual(t.ratkaise_ikaluokka("auto", {}), "ikaihminen")
        self.assertEqual(t.ratkaise_ikaluokka("25"), "nuori")
        self.assertEqual(t.ratkaise_ikaluokka("aikuinen"), "aikuinen")
        with self.assertRaises(ValueError):
            t.ratkaise_ikaluokka("12")
        with self.assertRaises(ValueError):
            t.ratkaise_ikaluokka("vanhus")

    def test_persoonallisuus(self):
        self.assertIn("pirteä", t.persoonallisuusohje("pirtea", "fi"))
        self.assertIn("cheerful", t.persoonallisuusohje("pirtea", "en"))
        oma = t.persoonallisuusohje("kuin vanha naapuri", "fi")
        self.assertIn("kuin vanha naapuri", oma)
        self.assertIn("säännöt", oma)          # oma kuvaus ei ohita sääntöjä

    def test_kontekstit_kaikilla_kielilla(self):
        profiili = t.hae_profiili(None)
        for konteksti in t.KONTEKSTIT:
            for kieli in ("fi", "sv", "en"):
                with self.subTest(konteksti=konteksti, kieli=kieli):
                    tehtava = t.puhelun_tehtava(profiili, kieli, konteksti)
                    self.assertIn("LOPETUS", tehtava)
                    self.assertNotIn("{", tehtava)
                    avaus = t.kontekstin_avaus(konteksti, kieli) or t.teksti("avauslause", kieli)
                    self.assertTrue(any(s in avaus for s in ("tekoäly", "AI-assistent", "AI assistant")), avaus)
        self.assertIn("vahingoittaa itseäsi", t.puhelun_tehtava(profiili, "fi", "mielenterveys"))
        self.assertIn("antibiootti", t.puhelun_tehtava(profiili, "fi", "laakemuistutus"))
        self.assertNotIn("LÄÄKKEESEEN LIITTYVÄT", t.puhelun_tehtava(profiili, "fi", "juttuseura"))

    def test_itsetuhokysymys_tunnistetaan_mallin_vastauksesta(self):
        self.assertTrue(p.ITSETUHOKYSYMYS.search("Onko sinulla ollut ajatuksia vahingoittaa itseäsi?"))
        self.assertTrue(p.ITSETUHOKYSYMYS.search("Have you had thoughts of harming yourself?"))
        self.assertFalse(p.ITSETUHOKYSYMYS.search("Miten olet nukkunut?"))


class PuhelunKesto(unittest.TestCase):
    def test_raja_joustaa_huolen_mukaan(self):
        tavallinen = [tunnista("hyvin menee")] * 5
        self.assertEqual(t.puhelun_vuororaja(tavallinen, 10, 5), 10)
        huoli = tavallinen + [tunnista("olen huolissani")]
        self.assertGreater(t.puhelun_vuororaja(huoli, 10, 6), 10)
        vakava = tavallinen + [tunnista("mieheni lyö minua")]
        self.assertGreater(t.puhelun_vuororaja(vakava, 10, 6), t.puhelun_vuororaja(huoli, 10, 6))

    def test_ei_katkaista_kesken_huolen(self):
        # raja olisi jo ylittynyt, mutta viimeisessä vuorossa on huolta -> jatketaan
        havainnot = [tunnista("hyvin menee")] * 30 + [tunnista("mua pelottaa tosi paljon")]
        self.assertGreater(t.puhelun_vuororaja(havainnot, 10, 31), 31)

    def test_itsetuho_ei_rajaa(self):
        self.assertIsNone(t.puhelun_vuororaja([tunnista("en halua elää")] * 200, 10, 200, True, True))

    def test_ehdoton_ylaraja_muuten(self):
        havainnot = [tunnista("olen huolissani")] * 500
        self.assertLessEqual(t.puhelun_vuororaja(havainnot, 10, 500), t.SAANNOT["puhelun_kesto"]["ehdoton_ylaraja"])


class Itsetuhoisuus(unittest.TestCase):
    def test_viestissa_lammin_pyynto_112_ja_kriisipuhelin(self):
        pyynto = {"fi": ("älä tee itsellesi", "ettet satuta itseäsi"),
                  "sv": ("gör dig inte illa", "inte skadar dig själv"),
                  "en": ("do not hurt yourself", "not harm yourself")}
        kriisi = {"fi": "09 2525 0111", "sv": "09 2525 0112", "en": "09 2525 0116"}
        for kieli in ("fi", "sv", "en"):
            for _ in range(15):
                v = t.teksti("hataviestit.itsetuhoisuus", kieli)
                self.assertTrue(any(x in v.lower() for x in pyynto[kieli]), v)
                self.assertIn("112", v)
                self.assertIn(kriisi[kieli], v)
                self.assertTrue(v.rstrip().endswith("?"))

    def test_tukitilan_lisaohjeet(self):
        for kieli, sana in (("fi", "älä tee itsellesi"), ("sv", "gör dig inte illa"), ("en", "do not hurt yourself")):
            self.assertIn(sana, t.tukitilan_tehtava("x", kieli, itsetuho=True).lower())
            self.assertNotIn(sana, t.tukitilan_tehtava("x", kieli, itsetuho=False).lower())


class Loukkaukset(unittest.TestCase):
    def test_tunnistetaan(self):
        for x in ["tyhmä kone", "sä oot ihan idiootti", "haista vittuun", "turpa kiinni",
                  "vihaan sinua", "you are so stupid", "shut up", "håll käften", "du är dum"]:
            with self.subTest(x=x):
                self.assertTrue(tunnista(x).loukkaus)
        for x in ["tapan sut", "räjäytän koko paikan", "I will kill you"]:
            with self.subTest(x=x):
                h = tunnista(x)
                self.assertTrue(h.uhkaus and h.loukkaus)

    def test_kiroilu_ja_kielto_eivat_ole_loukkaus(self):
        for x in ["vittu kun sattuu", "pää on ihan paska tänään", "en vihaa sinua",
                  "ole hyvä", "mieheni lyö minua", "olen ollut hiljaa"]:
            with self.subTest(x=x):
                self.assertFalse(tunnista(x).loukkaus)

    def test_taso(self):
        self.assertEqual(t.kiireellisyys([tunnista("tyhmä kone")]), "vihreä")
        self.assertEqual(t.kiireellisyys([tunnista("tyhmä kone"), tunnista("turpa kiinni")]), "keltainen")
        self.assertEqual(t.kiireellisyys([tunnista("tapan sut")]), "keltainen")

    def test_vastaukset_kaikilla_kielilla(self):
        for kieli in ("fi", "sv", "en"):
            for avain in ("loukkaus.viesti_1", "loukkaus.uhkaus_1", "loukkaus.viesti_2", "loukkaus.lopetus"):
                self.assertTrue(t.teksti(avain, kieli).strip(), (kieli, avain))
            self.assertIn("?", t.teksti("loukkaus.viesti_1", kieli))


class Vaihtelu(unittest.TestCase):
    def test_versiot_vaihtelevat_eika_sama_perakkain(self):
        nahty = set()
        edellinen = None
        for _ in range(30):
            v = t.teksti("uudelleenkysymys.ohitusviesti", "fi")
            self.assertNotEqual(v, edellinen)
            edellinen = v
            nahty.add(v)
        self.assertGreaterEqual(len(nahty), 3)
        for kieli in ("sv", "en"):
            self.assertGreaterEqual(len({t.teksti("lopetuslause", kieli) for _ in range(20)}), 2)

    def test_jokaisessa_versiossa_turvallisuustieto(self):
        """Vaihtelu ei saa pudottaa 112:ta tai lupausta hoitajan soitosta."""
        for kieli in ("fi", "sv", "en"):
            for avain in ["lopetuslause_tuki", "lopetuspyynto.lopetus_huolestuttava",
                          "vakavat_huolet.kaltoinkohtelu.viesti"]:
                for _ in range(15):
                    self.assertIn("112", t.teksti(avain, kieli), (kieli, avain))

    def test_avaus_kertoo_aina_tekoalysta(self):
        for kieli, sana in (("fi", "tekoäly"), ("sv", "AI-assistent"), ("en", "AI assistant")):
            for _ in range(15):
                self.assertIn(sana, t.teksti("avauslause", kieli))

    def test_mallin_muotoilun_tarkistus(self):
        ok = p._muotoilu_kelpaa
        self.assertTrue(ok("Kiitos, että kerroit minulle.", "Kiitos, kun kerroit tästä minulle.", "fi"))
        self.assertFalse(ok("Kiitos, että kerroit.", "Soita heti numeroon 112.", "fi"))       # ohje + numero
        self.assertFalse(ok("Onko kaikki hyvin?", "Kaikki on varmasti hyvin.", "fi"))          # kysymys katosi
        self.assertFalse(ok("Kiitos, että kerroit.", "Thank you for telling me.", "fi"))       # kieli vaihtui
        self.assertFalse(ok("Kiitos.", "Kiitos. Hoitaja soittaa sinulle huomenna.", "fi"))     # lisälause
        self.assertFalse(ok("Selvä, ymmärrän.", "Selvä, {huolet} ymmärrän sen.", "fi"))
        # Merkitys ei saa vaihtua, vaikka lause kuulostaisi kivalta
        self.assertFalse(ok("Toki, lopetetaan, jos haluat.", "Ymmärrän hyvin, ja se on täysin okei.", "fi"))
        self.assertTrue(ok("Toki, lopetetaan, jos haluat.", "Selvä, lopetetaan, jos niin haluat.", "fi"))
        self.assertTrue(ok("Thank you for telling me.", "Thank you so much for telling me that.", "en"))
        # Toimintaohjetta sisältävää lausetta ei edes yritetä muotoilla
        self.assertEqual(p.muotoile_ensimmainen_lause("Soita nyt 112. Kiitos.", "fi"), "Soita nyt 112. Kiitos.")


class Analyysi(unittest.TestCase):
    def test_katkennut_json_korjataan_joka_kohdasta(self):
        kokonainen = json.dumps({
            "yleisvointi": "väsynyt",
            "oireet": [{"oire": "pahoinvointi", "voimakkuus": "lievä", "tilanne": "aamuisin"}],
            "huolet": ["a", "b"], "ehdotettu_taso": "keltainen", "perustelu": "x"},
            ensure_ascii=False, indent=2)
        for i in range(1, len(kokonainen) + 1):
            p._tasoita(p.jasenna_json(kokonainen[:i]))

    def test_roskaa_ymparilla(self):
        self.assertEqual(p.jasenna_json('```json\n{"a":[1,2,],}\n```'), {"a": [1, 2]})

    def test_varayhteenveto(self):
        v = p.varayhteenveto([tunnista("setä kosketti mua luvatta")], "testi")
        self.assertEqual(v["ehdotettu_taso"], "punainen")
        self.assertTrue(v["varayhteenveto"])

    def test_keksitty_numero_poistetaan(self):
        self.assertEqual(p.suodata_numerot("Soita heti 992-200."), p.NUMERON_KORVAUS)
        self.assertEqual(p.suodata_numerot("Soita 112."), "Soita 112.")
        self.assertEqual(p.suodata_numerot("Soita 0800 147 111."), "Soita 0800 147 111.")


if __name__ == "__main__":
    unittest.main(verbosity=1)
