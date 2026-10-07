# Agenttinen hyvinvointikumppani

SOTE AI Hackathon 2026 · Haaste 3.1 Jatkuva ja ennakoiva omahoidon tuki
Tiimi: Savon Ammattiopisto (Risto, Justus, Jere)

---

## Mitä rakennamme

Ennakoiva hyvinvointikumppani, joka tunnistaa datasta ihmiset, jotka **eivät itse ota
yhteyttä**, ja tavoittaa heidät ajoissa tavallisella puhelinsoitolla. Muutos
terveysdatassa (esim. uusi resepti) laukaisee räätälöidyn kysymyksen. Ääniagentti
soittaa, keskustelee suomeksi, arvioi vastauksen ja tuottaa hoitajalle priorisoidun
takaisinsoittojonon. Ihminen tekee aina hoitopäätöksen.

### Ongelma (haasteen 3.1 sanoin)
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

## Tekninen pakki (lukittavat valinnat)

| Osa | Valinta | Status |
|---|---|---|
| Backend | Python 3.12 + FastAPI + Uvicorn | lukittu |
| Frontend | Vue 3 + Vite + Pinia + Tailwind | lukittu |
| Puhelu ulos | Twilio Programmable Voice + Media Streams (mulaw/8000/mono/base64) | lukittu |
| Ääniagentti | Polku A: Azure Speech fi-FI -putki (STT → LLM → TTS). Polku B (venytys): Azure OpenAI Realtime | lukittu |
| Suomen puhe | Azure STT fi-FI + TTS `fi-FI-SelmaNeural`, ulostulo `Raw8Khz8BitMonoMulaw` | TESTATTAVA viikolla |
| Data | Veil.AI synteettiset profiilit, CSV/JSON (saatavilla vasta 13.10.) | odottaa |
| Kehitysdata nyt | Synthea (avoin FHIR-generaattori) | otettava käyttöön |
| Tietokanta | Postgres 17 (dev: Docker Compose, tuotanto: Azure Database for PostgreSQL, Sweden Central) | lukittu |
| Dev-infra | Docker Compose: Postgres + pgAdmin + synteettinen demodata (vain paikallisesti) | lukittu |
| Hosting | EU: Azure Sweden Central / West / North Europe | lukittu |

> HUOM: kilpailudata on **CSV/JSON, ei FHIR**. Tehkää datan lukemiseen adapterikerros,
> jotta Veil.AI:n oikean muodon näkee vasta 13.10. ilman uudelleenkirjoitusta.

---

## Kehitysympäristö (Docker)

Paikallinen tietokanta ja demodatan katselu. **Vain kehitykseen, ei tuotantoon.**

```bash
cp .env.example .env            # vaihda salasanat; .env ei mene repoon
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

- **Postgres**: `127.0.0.1:5432`, tunnukset `.env`:stä.
- **pgAdmin**: <http://127.0.0.1:5050>. Kirjaudu `PGADMIN_EMAIL`/`PGADMIN_PASSWORD`;
  palvelin "hyvinvointi (dev)" on valmiina, tietokannan salasana kysytään avattaessa.
- **Demodata**: `data/csv/*.csv` (synteettinen) ladataan skeemaan `demo` ensimmäisellä
  käynnistyksellä. Uudelleenlataus: `... down -v` ja `up` uudelleen (poistaa kaiken datan).
- Pelkkä tietokanta ilman pgAdminia: `docker compose up -d`.

Miksi tämä on turvallinen dev-tapa: portit vain `127.0.0.1`:ssä (ei näy lähiverkkoon),
ei oletussalasanoja (compose kaatuu ilman `.env`:iä), pgAdmin kirjautumisen takana,
vain synteettistä dataa, kiinnitetyt image-versiot. Tuotannossa ei composea eikä pgAdminia:
hallinnoitu Postgres Sweden Centralissa, salaisuudet Azure Key Vaultissa (ks. SAANTELY.md).

`data/csv/` on `.gitignore`ssa (koko ~80 Mt, Veil.AI-datan käyttöehdot) — jaetaan tiimin kesken erikseen.

---

## Mitä toimme valmiina (pakollinen kuvaus palautukseen)

Säännöt (kohta 7) sallivat valmiiksi: kehitysympäristö, yleiskäyttöiset komponentit ja
omat olemassa olevat tuotteet. Itse haasteeseen kohdistettu ratkaisu rakennetaan
tapahtuman aikana. Pidä tätä listaa ajan tasalla rakentaessasi:

- [ ] kehitysympäristö pystyssä (yleiskäyttöinen)
- [ ] puhelinyhteyden perusputki Twiliolla (yleiskäyttöinen)
- [ ] ääniagentin perusrunko, suomen TTS/STT testattu (yleiskäyttöinen)
- [ ] (tapahtumassa rakennettava: muutosten tunnistus, kysymysten generointi, triage,
      hoitajan näkymä)

---

## Sääntely lyhyesti

- **GDPR**: terveystiedot ovat erityisiä henkilötietoja (art. 9). DPIA pakollinen.
  Datan minimointi: tallennetaan vain terveyden kannalta olennainen yhteenveto.
- **AI Act**: läpinäkyvyys (art. 50) – puhelun alussa kerrotaan, että soittaja on
  tekoäly. Vanhukset ovat haavoittuva ryhmä. Ihmisen valvonta on sisäänrakennettu.
- **EHDS**: yhteentoimivuus ja kansalaisen oikeus omiin tietoihin.
- **MDR (lääkinnällinen laite)**: oireiden vakavuuden arviointi voi tehdä tuotteesta
  luokan IIa laitteen. Rajausratkaisu: järjestelmä on hoitajan **päätöksen tuki**, ei
  diagnosoi eikä neuvo sairaudessa (vrt. Keski-Suomen linjaus). Avataan tämä pitchissä
  rehellisesti.

Tavallinen puhelu **ei ole päästä päähän salattu** – kerrotaan tämä ja riskienhallinta
(ei henkilötunnusta puhelussa, DPA Twilion kanssa, ei äänitallenteita, vastaajan
tunnistus).

---

## Arviointikriteerit (mihin tunnit käytetään)

| Kriteeri | Paino |
|---|---|
| Vaikuttavuus | 30 % |
| Toteutettavuus (toimiva demo) | 25 % |
| Tekoälyn hyödyntäminen | 15 % |
| Vastuullisuus | 15 % |
| Innovatiivisuus | 15 % |

Palkinto edellyttää painotettua keskiarvoa ≥ 3,0. Tasapelin ratkaisee vaikuttavuus.
**Toimiva demo + todellinen hyöty painavat 55 %.** Yksi täydellisesti toimiva polku on
parempi kuin viisi puolivalmista.

---

## Tiimi ja vastuut

| Henkilö | Vastuu |
|---|---|
| Risto (vetäjä) | Backend, datan luku, muutosten tunnistus, triage, sääntelyosuus pitchiin |
| Justus | Puhelin + ääniagentti (Twilio, suomen testaus) |
| Jere | Hoitajan näkymä, synteettinen data, demon käsikirjoitus |

---

## Hackathon-aikataulu (13.–14.10., Dipoli, Espoo, tilat auki 8–18)

- **Päivä 1 aamu**: data sisään (Veil.AI 13.10.), muutosten tunnistus, kysymykset
- **Päivä 1 iltapäivä**: ensimmäinen toimiva puhelu päästä päähän, vaikka kömpelö
- **Päivä 2 aamu**: triage + hoitajan näkymä
- **Päivä 2**: viimeistely, demon harjoittelu, pitch
- **Palautus ke 14.10. klo 14.30 Junctionissa** (myöhästynyttä ei arvioida)

---

# Selvitettävää NYT (prep-lista)

### A. Hoidettava tänään ti 6.10.
- [ ] **Wolters Kluwer UpToDate -tunnus**: ilmoita Felinille klo 12 mennessä kenelle
      (nimi, sähköposti, puhelinnumero)
- [ ] **Webinaarin osallistujat**: nimet + sähköpostit Felinille (ei tule Junctionin
      kautta, siksi erikseen)
- [ ] Vahvista osallistuminen (takaraja ke 7.10., muuten paikka jonossa olevalle)
- [ ] Jos joku alle 18 v: pyydä huoltajan suostumuslomake (palautus 10.10. mennessä)

### B. Ke 7.10.
- [ ] **Sääntelywebinaari klo 10.00–10.45** (GDPR, AI Act, EHDS, tuotesääntely) –
      kirjatkaa kysymyksiä: onko tuote MDR-laite, miten rajataan päätöksen tueksi

### C. Tekninen de-riskaus tällä viikolla (tärkein ensin)
- [ ] **Suomen puheen laatu** – suurin riski. Testaa STT+TTS fi-FI oikealla,
      hitaalla/vanhuksen tyylisellä puheella. Vertaa reaaliaikamalli vs. erillinen putki.
- [ ] **Puhelin soimaan** – Twilio-tili, testinumero, Media Streams websocket pystyyn,
      yksi soitto itselle läpi
- [ ] **Synthea-data** käyttöön kehitykseen (oikea data vasta 13.10.)
- [ ] Adapterikerros datan lukuun (CSV/JSON nyt, Veil.AI-muoto myöhemmin)

### D. Tilit ja pääsyt valmiiksi (tuokaa mukananne)
- [ ] Twilio-tili + vahvistettu suomalainen testinumero (trial soittaa vain vahvistettuihin)
- [ ] Azure-tili + **Speech-resurssi EU-alueelle** (Sweden Central / West / North Europe);
      tarkista Azure for Students -kreditit
- [ ] ngrok (wss ulos devissä)
- [ ] Omat AI-lisenssit (Claude Code, Codex, Gemini) mukaan
- [ ] UpToDate-tunnus kun se tulee

### E. Päätettävät valinnat
- [x] Ääni: Polku A = Azure Speech fi-FI -putki (lukittu). Polku B = venytys jos suomi testaa hyvin.
- [x] Backend FastAPI, frontend Vue 3, hosting EU (lukittu)
- [ ] Mitkä 2–3 laukaisinta demoon (ehdotus: uusi lääke, uusimatta jäänyt resepti, kotiutus)
- [ ] MDR-rajaus: päätöksen tuki vai avoin IIa-keskustelu pitchissä

### F. Vielä Felinilta kysyttävää
- [ ] Montako tiimiä haasteessa 3.1 (kilpailutilanne)?
- [ ] Pitchin kesto ja finaalin kulku (julkaistaan viim. viikkoa ennen)

### G. Pitchiin valmiiksi (halpoja, vahvoja)
- [ ] Kustannuslaskelma: säästyvät hoitajan soitot, vältetty päivystyskäynti
      (vertailu: Naver Izumo 100 → 5 työntekijää)
- [ ] Muisti: agentti muistaa edellisen puhelun ("viime viikolla huimasi, nyt?")
- [ ] Saavutettavuus: hidas puhe, toisto; testaa ymmärtääkö agentti savon murretta –
      jos ymmärtää, näytä lavalla

> Demossa EI aitoja henkilötietoja (sääntö). Lavalla soiva puhelu ok, kun vastaaja
> esittää synteettistä potilasta.
