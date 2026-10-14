# Puhelun Demo esitys!!!

https://drive.google.com/file/d/1RkReNimeM9_mGRus30yAyqKGouU2tefu/view?usp=sharing
















# Agenttinen hyvinvointikumppani

SOTE AI Hackathon 2026 · Haaste 3.1 Jatkuva ja ennakoiva omahoidon tuki
Tiimi: Savon Ammattiopisto (Risto, Justus, Jere)

---

## Sisällys

- [Mitä rakennamme](#mitä-rakennamme)
  - [Ongelma](#ongelma-haasteen-31-sanoin)
  - [Mikä tässä on uutta](#mikä-tässä-on-uutta)
- [Arkkitehtuuri](#arkkitehtuuri)
- [Tekninen pakki](#tekninen-pakki-lukittavat-valinnat)
- [Kehitysympäristö (Docker)](#kehitysympäristö-docker)
  - [Tietokannan alustus](#tietokannan-alustus-uusi-kone--tiimiläinen)
  - [Backend käyntiin](#backend-käyntiin-migraatioiden-ajo-tärkeä)
  - [Hoitajan käyttöliittymä](#hoitajan-käyttöliittymään-frontend)
- [Mitä toimme valmiina](#mitä-toimme-valmiina-pakollinen-kuvaus-palautukseen)
- [Sääntely lyhyesti](#sääntely-lyhyesti)
- [Arviointikriteerit](#arviointikriteerit-mihin-tunnit-käytetään)
- [Tiimi ja vastuut](#tiimi-ja-vastuut)
- [Hackathon-aikataulu](#hackathon-aikataulu-131410-dipoli-espoo-tilat-auki-818)
- [Selvitettävää NYT (prep-lista)](#selvitettävää-nyt-prep-lista)
  - [Hoidettava tänään](#a-hoidettava-tänään-ti-610)
  - [Ke 7.10.](#b-ke-710)
  - [Tekninen de-riskkaus](#c-tekninen-de-riskkaus-tällä-viikolla-tärkein-ensin)
  - [Tilit ja pääsyt](#d-tilit-ja-pääsyt-valmiiksi-tuokaa-mukananne)
  - [Päätettävät valinnat](#e-päätettävät-valinnat)
  - [Kysyttävää Felinilta](#f-vielä-felinilta-kysyttävää)
  - [Pitchiin valmiiksi](#g-pitchiin-valmiiksi-halpoja-vahvoja)

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
| Käyttöliittymä | FastAPI + Jinja2 HTML-pohjat, CSS ja tarvittaessa pieni määrä JavaScriptiä | Python-sovelluksen yhteydessä |
| Puhelu ulos | Twilio Programmable Voice + Media Streams (mulaw/8000/mono/base64) | lukittu |
| Ääniagentti | Polku A: Azure Speech fi-FI -putki (STT → LLM → TTS). Polku B (venytys): Azure OpenAI Realtime | lukittu |
| Puhelun LLM | Demo: Gemma 4 12B paikallisesti Ollamalla (OpenAI-yhteensopiva rajapinta), keskustelu Jeren ohjeistuksella. Tuotanto: EU-hostattu, asetuksilla | valittu 11.10., laatu TESTATTAVA |
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

### Tietokannan alustus (uusi kone / tiimiläinen)

Edellytykset: Docker Desktop käynnissä, synteettiset CSV:t kansiossa `data/csv/`
(eivät tule gitistä, pyydä tiimiltä), portit 5432 ja 5050 vapaina.

```bash
# 1. .env satunnaisilla salasanoilla (sama salasana POSTGRES_PASSWORD:iin ja DATABASE_URL:iin)
PG=$(openssl rand -hex 16); PA=$(openssl rand -hex 16)
sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$PG/" \
    -e "s#^DATABASE_URL=.*#DATABASE_URL=postgresql+psycopg://hyvinvointi:$PG@127.0.0.1:5432/hyvinvointi#" \
    -e "s/^PGADMIN_PASSWORD=.*/PGADMIN_PASSWORD=$PA/" .env.example > .env
chmod 600 .env

# 2. Käynnistä; ensimmäinen käynnistys alustaa kannan ja lataa demodatan
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --wait

# 3. Tarkista: 16 taulua ladattu, potilaita > 0
docker compose logs postgres | grep demodata
docker compose exec postgres psql -U hyvinvointi -d hyvinvointi -c "select count(*) from demo.patients"
```

Mitä alustuksessa tapahtuu: Postgres luo tietokannan ja käyttäjän `.env`:n arvoilla, sitten
ajaa `docker/postgres/init-dev/`-skriptit. Skripti luo skeeman `demo` ja jokaisesta CSV:stä
taulun (nimi tiedostosta, sarakkeet otsakeriviltä, kaikki TEXT).

**Alustus ajetaan vain kerran, tyhjään volyymiin.** Siksi:
- Uusi data (esim. Veil.AI 13.10.) tai muutettu init-skripti: vaihda CSV:t `data/csv/`:hen ja
  nollaa kanta: `docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v`, sitten `up` uudelleen.
- `POSTGRES_PASSWORD`:n vaihtaminen `.env`:ssä jälkikäteen EI muuta kannan salasanaa. Nollaa
  kanta (`down -v`) tai vaihda salasana SQL:llä (`ALTER USER hyvinvointi PASSWORD '...'`).
- `down -v` poistaa myös sovelluksen omat taulut (jono, triage) ja pgAdminin asetukset.

Vianetsintä: `docker compose logs postgres` näyttää init-skriptin virheet. Jos alustus kaatui
puolivälissä, kanta jää vajaaksi: korjaa syy ja aja `down -v` + `up`.

Miksi tämä on turvallinen dev-tapa: portit vain `127.0.0.1`:ssä (ei näy lähiverkkoon),
ei oletussalasanoja (compose kaatuu ilman `.env`:iä), pgAdmin kirjautumisen takana,
vain synteettistä dataa, kiinnitetyt image-versiot. Tuotannossa ei composea eikä pgAdminia:
hallinnoitu Postgres Sweden Centralissa, salaisuudet Azure Key Vaultissa (ks. SAANTELY.md).

`data/csv/` on `.gitignore`ssa (koko ~80 Mt, Veil.AI-datan käyttöehdot) — jaetaan tiimin kesken erikseen.

### Backend käyntiin MIGRAATIOIDEN AJO TÄRKEÄ!

Edellytykset: kanta pystyssä yllä olevilla ohjeilla, `uv` asennettu (`brew install uv`).

```bash
# 1. Lisää .env:iin (repon juuressa) dev-asetukset, jos puuttuvat
grep -q '^DEV_TILA=' .env || echo 'DEV_TILA=true' >> .env

# 2. Riippuvuudet (hakee myös Python 3.12:n)
cd backend
uv sync

# 3. Migraatiot: luo sovelluksen taulut ja kysymys-/lääkeryhmäsisällön
uv run alembic upgrade head
uv run alembic current          # pitää näyttää "(head)"

# 4. Testit
uv run pytest

# 5. Käynnistä (ajaa myös puuttuvat migraatiot ja ensimmäisen tunnistuksen)
uv run uvicorn app.main:app --reload --port 8000
```

Tarkista: <http://127.0.0.1:8000/terveys> vastaa `{"tila":"ok"}` ja
<http://127.0.0.1:8000/docs> näyttää reitit. Soittojono: `GET /puhelutehtavat`
(tai käsin `POST /puhelutehtavat/tunnista` bodylla `{}`).

### Hoitajan käyttöliittymään (frontend)

Käyttöliittymä tarjoillaan samasta FastAPI-backendistä, joten erillistä frontend-palvelinta
ei tarvitse käynnistää. Kun backend on käynnissä, avaa selaimessa:

- Hoitajan näkymä: <http://127.0.0.1:8000/hoitaja/>
- Swagger-rajapinta: <http://127.0.0.1:8000/docs>

Hoitajan näkymä näyttää tietokannan soittojonon. Jos sivu ei aukea, varmista ensin, että
backendin käynnistyskomento on edelleen käynnissä eikä terminaalissa näy virhettä. Tarkista
myös, että Postgres on käynnissä ja migraatiot on ajettu yllä olevien ohjeiden mukaan.

Kun joku on lisännyt uuden migraation (git pull): aja `uv run alembic upgrade head`
tai käynnistä backend uudelleen. Kanta sekaisin devissä: nollaa koko kanta
(`docker compose ... down -v` + `up`) ja aja vaiheet 3–5 uudelleen.

Valinnaiset `.env`-asetukset: `DEMO_PUHELINNUMERO` (Twilioon vahvistettu numero
soittotiedoille), `TUNNISTUS_VALI_MINUUTIT` (ajastettu tunnistus, oletus 15, 0 = pois),
`LLM_BASE_URL` ja `LLM_MALLI` (puhelun paikallinen LLM). Kaikki: `.env.example`.

Puhelun malli (Ollama natiivisti; 16 Gt Macilla sulje raskaat ohjelmat, pienennä Docker
Desktopin muistia ja aja vain yhtä Ollama-palvelinta):

```bash
ollama pull gemma4:12b-it-q4_K_M
ollama create hyvinvointi-gemma12-gguf -f ollama/Modelfile.gemma12-gguf
OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_KEEP_ALIVE=-1 \
  OLLAMA_NUM_PARALLEL=1 ollama serve
# .env: LLM_MALLI=hyvinvointi-gemma12-gguf, LLM_AJATTELU_POIS=true
```

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
- [x] Backend ja hoitajan käyttöliittymä Python/FastAPI-sovelluksessa, hosting EU
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
