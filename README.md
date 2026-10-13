# Agenttinen hyvinvointikumppani

SOTE AI Hackathon 2026 · Haaste 3.1 Jatkuva ja ennakoiva omahoidon tuki

---

## Sisällys

- [Mitä rakennamme](#mitä-rakennamme)
  - [Ongelma](#ongelma)
  - [Mikä tässä on uutta](#mikä-tässä-on-uutta)
- [Arkkitehtuuri](#arkkitehtuuri)
- [Teknologia](#teknologia)
- [Asennusohjeet](asennus_ohjeet.md)
- [Sääntely lyhyesti](#sääntely-lyhyesti)

---

## Mitä rakennamme

Ennakoiva hyvinvointikumppani, joka tunnistaa datasta ihmiset, jotka **eivät itse ota
yhteyttä**, ja tavoittaa heidät ajoissa tavallisella puhelinsoitolla. Muutos
terveysdatassa (esim. uusi resepti) laukaisee räätälöidyn kysymyksen. Ääniagentti
soittaa, keskustelee suomeksi, arvioi vastauksen ja tuottaa hoitajalle priorisoidun
takaisinsoittojonon. Ihminen tekee aina hoitopäätöksen.

### Ongelma

Nykyiset digipalvelut ovat reaktiivisia: asukas ottaa yhteyttä vasta, kun ongelma on
syventynyt, ja tuki katkeaa kohtaamisten välissä.

### Mikä tässä on uutta

- **Datalähtöinen laukaisu**: muutos potilastiedoissa käynnistää yhteydenoton, ei
  kiinteä aikataulu (vrt. Naver CareCall) eikä hoitajan käsin käynnistämä kutsu (vrt.
  Hippocratic AI).
- **Hiljaisten signaalien tunnistus**: uusimatta jäänyt resepti, väliin jäänyt aika,
  tekemätön labra, kotiutuksen jälkeinen hiljaisuus.
- **Suomen kieli ja EU-sääntely sisäänrakennettuna** (GDPR, AI Act, EHDS).

---

## Arkkitehtuuri

```
synteettinen data ─▶ muutosten tunnistus ─▶ kysymysten generointi
      (CSV/JSON)        (deterministinen)       (LLM muotoilee)
                                                      │
hoitaja soittaa ◀─ hoitajan näkymä ◀─ triage ◀─ ääniagentti soittaa (puhelu)
                     (web, jono)    (lievä/kesk/vakava)   (keskustelee suomeksi)
```

Suunnitteluperiaate: **LLM vain siellä missä sitä tarvitaan.** Muutosten tunnistus ja
hälytysrajat ovat sääntöpohjaisia. LLM muotoilee kysymykset ja käy keskustelun.
Punaiset liput (rintakipu, hengenahdistus, halvausoireet) tunnistetaan myös
sääntöpohjaisesti reaaliajassa.

---

## Teknologia

| Osa | Toteutus |
|---|---|
| Backend | Python 3.12, FastAPI ja Uvicorn |
| Käyttöliittymä | FastAPI, Jinja2, CSS ja JavaScript |
| Puhelu | Twilio Programmable Voice ja ConversationRelay/Gather |
| Puhelun kielimalli | Gemma 4 12B paikallisesti Ollamalla |
| Data | Synteettinen CSV-data adapterikerroksen kautta |
| Tietokanta | PostgreSQL 17 |
| Paikallinen ympäristö | Docker Compose, pgAdmin ja ngrok |

---

## Asennus ja käynnistys

Paikallisen kehitys- ja demoympäristön Docker-, tietokanta-, backend-, käyttöliittymä-
ja Ollama-ohjeet ovat tiedostossa [asennus_ohjeet.md](asennus_ohjeet.md).

---

## Sääntely lyhyesti

- **GDPR**: terveystiedot ovat erityisiä henkilötietoja (art. 9). DPIA pakollinen.
  Datan minimointi: tallennetaan vain terveyden kannalta olennainen yhteenveto.
- **AI Act**: läpinäkyvyys (art. 50) – puhelun alussa kerrotaan, että soittaja on
  tekoäly. Vanhukset ovat haavoittuva ryhmä. Ihmisen valvonta on sisäänrakennettu.
- **EHDS**: yhteentoimivuus ja kansalaisen oikeus omiin tietoihin.
- **MDR (lääkinnällinen laite)**: oireiden vakavuuden arviointi voi tehdä tuotteesta
  luokan IIa laitteen. Järjestelmä rajataan hoitajan **päätöksenteon tueksi**: se ei
  diagnosoi, tee hoitopäätöksiä eikä neuvo sairauden hoidossa.

Tavallinen puhelu **ei ole päästä päähän salattu** – kerrotaan tämä ja riskienhallinta
(ei henkilötunnusta puhelussa, DPA Twilion kanssa, ei äänitallenteita, vastaajan
tunnistus).

Demossa käytetään vain synteettisiä henkilö- ja terveystietoja.
