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
