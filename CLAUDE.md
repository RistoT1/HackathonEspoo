# CLAUDE.md

Ohjeet Claude Codelle tässä repossa. Projektin yleiskuva, arkkitehtuuri ja aikataulu: @README.md
Sääntely, tietoturva ja arviointikriteerit: @SAANTELY.md — noudata sitä.

Greenfield-hackathonprojekti: EI vielä mitattuja suorituskykylukuja. Älä keksi latensseja,
rivimääriä tai muita lukuja — kirjaa päätös vasta kun se on oikeasti mitattu ajamalla.
Tekniset valinnat (stäkki, ääniratkaisu) on tässä tiedostossa LUKITTU; TBD-kohdat ovat auki.

---

## Projekti

Ennakoiva hyvinvointikumppani (haaste 3.1). Muutos synteettisessä terveysdatassa laukaisee
puhelinsoiton vanhukselle; ääniagentti keskustelee suomeksi, triage luokittelee kiireellisyyden,
hoitaja saa priorisoidun jonon ja soittaa. Rakennetaan kahdessa päivässä: suosi pientä, toimivaa
ja demottavaa yli kattavuuden. Yksi täydellisesti toimiva polku > viisi puolivalmista.

Demon rahasana (tähän tähdätään): laukaisin laukeaa → puhelin soi → vanhus vastaa yhteen kahteen
kysymykseen suomeksi → keltainen hälytys ilmestyy hoitajan jonoon.

Koodi kirjoitetaan **suomeksi**: domain-mallit, DTO:t, reitit ja muuttujanimet suomeksi
(esim. `Vanhus`, `Puhelu`, `Muutos`, `Kiireellisyys`, `Yhteenveto`). Noudata tätä.

---

## Stäkki (lukittu)

### Backend (Python 3.12)
- **FastAPI** + **Uvicorn** — async + natiivit websocketit (Twilio Media Streams, webhookit).
- **Pydantic v2** / **pydantic-settings** — DTO:t, validointi ja konfiguraatio (.env:stä).
- **SQLAlchemy 2.0** (tai SQLModel) + **Postgres 17** devissä Dockerissa (sama moottori kuin
  tuotannossa) — triage-tulokset ja hoitajan jono. Ajuri `psycopg` (v3), yhteys `DATABASE_URL`
  `.env`:stä. SQLite sallittu vain pytestin nopeisiin yksikkötesteihin.
  Tuotannossa Azure Database for PostgreSQL, Sweden Central.
- **Docker Compose** — VAIN paikallinen dev-infra (Postgres + pgAdmin), ks. "Docker" alla.
- **APScheduler** — ajastettu muutosten tunnistus.
- **twilio** (Python SDK) — ulossoitot; Media Streams -ääni websocketilla backendille.
- **azure-cognitiveservices-speech** — suomen puheentunnistus ja -synteesi (ks. ääni alla).
- **httpx** (async) — LLM-, UpToDate- ja muut ulkoiset kutsut.
- **Runtime-LLM (puhelun keskustelu)**: demossa **paikallinen malli Ollamalla**, kutsu
  OpenAI-yhteensopivaa rajapintaa vasten (`LLM_BASE_URL`, `LLM_MALLI` `.env`:stä). Tuotannossa
  sama koodi EU-hostattuun malliin (esim. Azure OpenAI EU Data Zone) pelkällä asetusmuutoksella.
  Älä koodaa minkään yksittäisen palveluntarjoajan SDK:ta vasten.
- **Jinja2** — FastAPI:n palvelinpuolen HTML-pohjat hoitajan käyttöliittymälle.
- Data: **pandas** CSV/JSON-profiilien lukuun — vain adapterikerroksessa.
- Lokitus: `structlog` tai stdlib `logging`, JSON. Testit: **pytest** (+ `pytest-asyncio`).

### Käyttöliittymä (hoitajan näkymä)
- Käyttöliittymä tarjoillaan samasta FastAPI-sovelluksesta Jinja2-pohjilla.
- Tyylit ja mahdollinen pieni määrä selaimen JavaScriptiä ovat `static/`-kansiossa.
- Erillistä Vue-/Node-frontend-projektia ei tehdä.
- Jono = taulukko + tapauskohtainen näkymä. Trendikäyrä on plussaa, ei MVP.

---

## Komennot

Täytä heti kun projekti pystyyn. Aja testit + lint ennen committia.

```bash
# Tietokanta (repon juuri) — ensin: cp .env.example .env ja vaihda salasanat
# Vain Postgres:          docker compose up -d
# Dev + pgAdmin + demo:   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
# pgAdmin:                http://127.0.0.1:5050
# Alas (data säilyy):     docker compose -f docker-compose.yml -f docker-compose.dev.yml down
# Nollaa + lataa demo:    ... down -v  (poistaa volyymit), sitten up uudelleen

# Backend (backend/)
# Asennus:  uv sync   (tai pip install -r requirements.txt)
# Aja:      uvicorn app.main:sovellus --reload --port 8000
# Testit:   pytest
# Lint:     ruff check . && ruff format .
# Asennus:  uv sync
# Aja:      uv run uvicorn app.main:app --reload --port 8000   (ajaa migraatiot kaynnistyksessa)
# Testit:   uv run pytest
# Lint:     uv run ruff check . && uv run ruff format .
# Migraatio mallimuutoksen jalkeen:  uv run alembic revision --autogenerate -m "kuvaus"
# Synthean ATC-vastaavuus uudelleen:  PYTHONPATH=. uv run python scripts/hae_synthea_atc.py
# Dev-reitit (/dev/...): DEV_TILA=true .env:iin. API-dokumentaatio: http://127.0.0.1:8000/docs
# wss ulos devissä:  ngrok http 8000   (Twilio tarvitsee julkisen wss-osoitteen)

# Käyttöliittymä tarjoillaan backendin kanssa osoitteessa http://127.0.0.1:8000
```

### Docker (vain dev)
- `docker-compose.yml`: Postgres. `docker-compose.dev.yml`: lisää pgAdminin ja lataa synteettisen
  demodatan (`data/csv/*.csv`) skeemaan `demo` ensimmäisellä käynnistyksellä (tyhjä volyymi).
  Lataus on kenttänimiriippumaton (taulut CSV-otsakkeista, sarakkeet TEXT), joten Veil.AI:n CSV
  toimii sellaisenaan. `demo`-skeema on vain katseluun; sovellus lukee datan `data/adapteri.py`:n kautta.
- Tietoturva devissä: portit sidottu `127.0.0.1`:een (ei näy hackathonin wifiin), salasanat
  pakollisia `.env`:stä (compose ei käynnisty ilman), pgAdmin vaatii kirjautumisen eikä tallenna
  tietokannan salasanaa, Postgres `scram-sha-256`, kiinnitetyt image-versiot.
- **Compose-tiedostoja EI käytetä tuotannossa.** pgAdmin ei kuulu tuotantoon lainkaan. Tuotannossa
  hallinnoitu Postgres Sweden Centralissa, salaisuudet Key Vaultissa, pääsy Entra ID:n kautta.

---

## Ehdottomat säännöt (älä riko — nämä ovat myös vastuullisuuspisteet, 15 %)

- **EI AITOJA HENKILÖTIETOJA missään** — ei koodissa, testeissä, demoissa eikä esityksissä.
  Vain synteettistä dataa (Synthea devissä, Veil.AI 13.10.). Hackathonin sääntö + GDPR.
- **Triagen hälytysrajat ja punaiset liput ovat sääntöpohjaisia**, eivät pelkän LLM:n varassa.
  LLM saa EHDOTTAA tasoa; sääntö voi vain NOSTAA kiireellisyyttä, ei laskea.
- **Ihminen päättää.** Järjestelmä on hoitajan päätöksen tuki. Ei diagnooseja, ei lääke-
  neuvoja vanhukselle. Agentti sanoo: "Välitän tämän hoitajalle."
- **Agentti kertoo olevansa tekoäly** heti puhelun alussa (AI Act art. 50).
- **Hätämerkit** (rintakipu, hengenahdistus, halvausoireet) → ohjaa heti soittamaan 112,
  tunnista sääntöpohjaisesti myös reaaliajassa kesken puhelun.
- **Datan minimointi**: tallenna vain terveyden kannalta olennainen yhteenveto. Ei rupattelun
  tallennusta (tai vain erillisellä suostumuksella). Ei äänitallenteita.
- **EU-hosting.** Kaikki data ja mallikutsut EU-alueelle (Azure Sweden Central / West / North Europe)
  tai paikallisesti omalla koneella (demon LLM).
- **Ei salaisuuksia repoon.** Avaimet ja tunnukset vain `.env`:iin (`.gitignore`). Ei committiin.

---

## Sääntely ja vastuullisuus — toimi näin

Täysi analyysi, perustelut ja arviointikriteerit: @SAANTELY.md. Noudata sitä. Yllä olevien
ehdottomien sääntöjen LISÄKSI koodin on toteutettava nämä:
- **Runtime-LLM paikallisesti (demo) tai EU-hostattuna (tuotanto).** Demossa malli ajetaan
  Ollamalla omalla koneella: puhelun sisältö ei lähde koneelta mallille. Tuotannossa EU-hostattu
  malli DPA:lla (esim. Azure OpenAI EU Data Zone). Potilassisältöä EI reititetä kuluttaja-API:n
  (suora OpenAI / Gemini / Claude) kautta. Azure-palvelut (Speech) vain Sweden Centralissa.
  Kehitystyökalut (Claude Code ym.) saavat käsitellä koodia ja synteettistä dataa.
- **Pseudonymisoi**: erota henkilöllisyys (nimi, puhelinnumero) terveystiedosta, linkitä tunnuksella.
- **Minimoi PII LLM-prompteissa**: älä lähetä nimeä tai henkilötunnusta mallille, vain mitä kysymys vaatii.
- **Twilio vain `wss` + DPA**; puhelussa ei henkilötunnusta eikä diagnooseja; ei äänitallenteiden säilytystä.
- **Hoitajan näkymä**: audit-loki jokaisesta katselusta (kun puhelun jälkeinen osuus tehdään).
  Kirjautumista EI tehdä hackathonissa: tuotannossa pääsy Entra ID:n kautta (MFA + roolit), ei
  omaa kirjautumista.
- **Salaisuudet** tuotannossa Azure Key Vaultiin, devissä `.env`. Ei repoon.

Arviointikriteerit (mihin tunnit käytetään): vaikuttavuus 30 %, toteutettavuus 25 %, tekoäly 15 %,
vastuullisuus 15 %, innovatiivisuus 15 %. Tasapelin ratkaisee vaikuttavuus; palkintoon painotettu
ka ≥ 3,0. Vaikuttavuus + toteutettavuus = 55 %, joten toimiva demo ja todellinen hyöty edellä.

---

## Arkkitehtuuri

Putki: `data → muutosten tunnistus → kysymysten generointi → puhelu → ääniagentti →
triage → hoitajan näkymä → hoitaja soittaa`.

Ydinperiaate: **LLM vain siellä missä sitä tarvitaan.** Pidä deterministinen ja LLM-osa erillään.
Älä upota liiketoimintalogiikkaa prompteihin.
- Deterministinen: muutosten tunnistus, hälytysrajat, punaiset liput, triage-säännöt.
- LLM: kysymysten muotoilu selkokielelle, keskustelu, litteroinnin tiivistäminen.

Moduulijako (`backend/app/`):
```
main.py          FastAPI-sovellus, reittien kokoaminen, migraatiot + ajastin käynnistyksessä
config.py        pydantic-settings (.env)
mallit.py        Pydantic-DTO:t (suomenkieliset kentät) = rajapintasopimus
tietokanta.py    SQLAlchemy-sessiot + taulumallit
data/            ADAPTERIKERROS: lue KAIKKI data tämän kautta
  adapteri.py    yhteinen rajapinta (Datalahde)
  postgres_demo.py  dev-lähde: Synthea-CSV:t Postgresin demo-skeemassa
muutokset/       lääkemuutosten tunnistus (deterministinen) + lukureitit
kysymykset/      kysymykset ja LLM:n konteksti muutoksista; sisältö kannasta (varasto.py)
puhelutehtavat/  soittojono: tunnistus -> rivi (kysymykset, konteksti, prioriteetti), ajastin
puhelu/          (tulossa) Twilio-soitto + Media Streams -websocket + ääniagentti
dev/             VAIN DEV (DEV_TILA=true): testimuutokset, jonon nollaus, demonumero
(ei vielä)       puhelun jälkeinen osuus: triage, hoitajan tapaukset/jono, audit-loki
```
Sisältö (kysymystekstit, lääkeryhmät): kannassa, muokkaus `backend/sql/kysymyssisalto.sql`.
Skeema: Alembic (`backend/alembic/`), uusi migraatio mallimuutoksen jälkeen.

### Data ja adapterikerros (tärkein joustavuuskohta)
Kilpailudata on **CSV/JSON, EI FHIR**, 50 000 synteettistä profiilia (Veil.AI), saatavilla vasta
**13.10.** latauslinkkinä. Kenttänimiä ei tiedetä etukäteen. **Lue kaikki data `data/adapteri.py`:n
kautta**, älä kovakoodaa kenttänimiä muualle. Näin lähteen vaihto Synthea → Veil.AI on yhden
tiedoston muutos. Kirjaa oletukset datan muodosta kommentiksi adapteriin. Kehitä nyt Synthean datalla.

**Lääkkeen tunnistus perustuu ATC-koodiin** (WHO ATC, tuotannossa Kannan valmistetieto), ei nimen
tekstiin. Adapteri antaa jokaiselle lääkejaksolle `atc`-kentän. Synthealla ei ole ATC:ta, joten
RxNorm→ATC-vastaavuus on tiedostossa `backend/app/data/synthea_atc.json` (skripti
`backend/scripts/hae_synthea_atc.py`, monitulkintaiset tarkistettu käsin). Lääkeryhmä = pisin
osuva ATC-etuliite (`laakeryhman_atc`); nimen avainsanat vain jos ATC puuttuu. Annosmuutos = sama
7-merkkinen ATC. Riskiluokka ja tarkennukset eivät ole kliinisesti tarkistettuja: tuotannossa
Fimean Lääke 75+ ja kliininen hyväksyntä. Kun Veil.AI-data tulee, katso ensin onko siinä ATC.

### Muutosten tunnistus
Ajastettu (APScheduler). Vertaa uusinta tilaa edelliseen tilannekuvaan → **rakenteinen muutoslista**,
ei LLM-tulkintaa: uusi lääke, annosmuutos, lopetettu lääke, poikkeava mittaus. Laajenna laukaisimia
hiljaisiin signaaleihin (uusimatta jäänyt resepti, väliin jäänyt aika, tekemätön labra, kotiutus) —
nämä erottavat ratkaisun kilpailussa.

### Puhelu ja ääni (LUKITTU: Polku A = Azure Speech fi-FI -putki)
Ketju: `Twilio ↔ Azure STT (fi-FI) → LLM (paikallinen, Ollama) → Azure TTS (fi-FI) ↔ Twilio`.
Valittu luotettavuuden ja suomen laadun takia; antaa myös puhtaan litteroinnin triagelle. Twilio
vain kuljettaa äänen, se ei tee puheentunnistusta eikä -synteesiä. Polku B (Azure OpenAI Realtime,
gpt-realtime, Sweden Central) on venytys päivälle 2 VAIN jos suomi testaa hyvin — älä aloita siitä.

LLM-kutsu: puhelukoodi hakee soittotehtävän kysymykset ja kontekstin backendistä
(`GET /puhelutehtavat/{id}/soittotiedot`) ja liittää ne kehotteeseen. LLM:llä ei ole pääsyä
kantaan. Paikallisen mallin suomen laatu, viive ja ohjeiden noudattaminen MITATAAN demokoneella
ennen mallin lukitsemista (ei oleteta). Punaiset liput eivät koskaan nojaa LLM:ään.

Lukitut tekniset faktat (näiden kanssa putki toimii, näitä vastaan koodataan):
- **Twilio Media Streams -muoto on aina `audio/x-mulaw`, 8000 Hz, mono, base64.** Kaksisuuntainen
  striimi avataan TwiML:llä `<Connect><Stream url="wss://.../media"/></Connect>`.
- **Takaisin Twiliolle** lähetettävä `media.payload` on mulaw/8000 base64 — **ei** tiedosto-
  otsaketavuja (ei WAV-headeria).
- **Azure TTS ulostuloformaatti `Raw8Khz8BitMonoMulaw` täsmää Twilioon → ei uudelleennäytteistystä.**
  Tämä on se kohta joka yleensä kaataa ensimmäisen puhelun; käytä tätä formaattia.
- **Azure STT**: `SpeechRecognizer`, kieli `fi-FI`, jatkuva tunnistus, ääni push-streaminä.
- **Azure TTS -suomiäänet**: `fi-FI-SelmaNeural`, `fi-FI-HarriNeural`, `fi-FI-NooraNeural`.
- **Devissä** FastAPI julki `ngrok`illa, jotta Twilio pääsee `wss`-osoitteeseen.
- **Twilion trial** soittaa vain vahvistettuihin numeroihin — vahvista demonumerot etukäteen.

Agentin järjestelmäkehote: persoona (lämmin, kärsivällinen, selkokielinen), missio (muutoslistan
kysymykset) ja Ehdottomat säännöt yllä. Puhelulle maksimikesto.

### Triage
Litteroinnista rakenteinen yhteenveto (oire, voimakkuus, alkoiko lääkkeen jälkeen, varmuus).
Kolme tasoa: vihreä (kirjataan), keltainen (hoitaja soittaa tänään), punainen (heti). LLM ehdottaa
tason; **sääntöpohjaiset punaiset liput voivat vain nostaa sitä.** Tallenna vain yhteenveto.
EI VIELÄ BACKENDISSÄ: backend kattaa toistaiseksi vain soittoa edeltävän osuuden
(tunnistus -> soittojono). Kun triage tehdään, hälytyssanat kuuluvat versionhallittuun
datatiedostoon testipankin kanssa (turvallisuuskriittinen), ei vapaasti muokattavaan kantaan.

### Hoitajan näkymä
Jono kiireellisyysjärjestyksessä, yhteenveto + perustelu ("miksi keltainen"), Soita-painike,
kuittaus ja palautekenttä (luokitus oli oikea/väärä). Litterointia ei tallenneta eikä näytetä
(datan minimointi). Jokainen katselu audit-lokiin. EI VIELÄ BACKENDISSÄ (puhelun jälkeinen
osuus). Kirjautuminen: ei hackathonissa, tuotannossa Entra ID (MFA + roolit).

---

## Ensimmäinen puhelu läpi (de-riskaa tässä järjestyksessä, ÄLÄ rinnakkain)

1. **Azure Speech suomi erillään Twiliosta.** Syötä nauhoitettua/mikrofonipuhetta STT:lle (fi-FI),
   kuuntele TTS:n ulostulo (`fi-FI-SelmaNeural`). Varmista suomen laatu ENNEN puhelinkytkentää.
2. **Twilio-tili + vahvistettu numero.** Yksi ulossoitto, TwiML vain puhuu lauseen (ei striimiä).
3. **wss auki.** `ngrok http 8000`, FastAPI-websocket ottaa vastaan `connected/start/media/stop`.
4. **Striimi sisään.** Tulosta saapuvan `media`-viestin koko — varmista että ääntä tulee.
5. **Striimi ulos.** Lähetä Azure TTS:n `Raw8Khz8BitMonoMulaw` base64:na takaisin `media`-eventtinä
   `streamSid`:llä — "hei, kuuletko minut" kuuluu puhelimessa.
6. **Koko silmukka.** Ääni sisään → STT → LLM → TTS → ulos. Yksi kysymys-vastaus-vuoro riittää.
Kun tämä kulkee kerran, loppu on sisällön hiomista.

---

## Konventiot

- Pydantic-DTO:t erillään kannan malleista. Palvelut palauttavat DTO:ita, eivät ORM-olioita.
- Puhtaat funktiot triage- ja laskentalogiikalle (`triage/`): testattavissa ilman kantaa,
  Twilioa tai LLM:ää. Pidä IO (Twilio, Azure, LLM, kanta) reunoilla, logiikka keskellä.
- Konfiguraatio vain `config.py`:n Settings-luokan kautta, ei `os.environ`-kutsuja siellä täällä.
- Soft delete jos tarpeen: `on_aktiivinen = False`, ei kovaa poistoa.
- Ei emojeja koodissa, committiviesteissä eikä tässä tiedostossa.

### Python-tyyli
- Selkeys edellä: lohkomuotoiset funktiot, guard-lauseet ja aikaiset returnit ennen syvää sisennystä.
  Laske johdetut arvot ja boolean-tarkistukset nimettyihin muuttujiin ENNEN returnia; älä piilota
  aritmetiikkaa return-lauseeseen.
- Tyyppivihjeet kaikkiin funktioiden rajapintoihin. `ruff` hoitaa lintin + formatin.

---

## Kehityskäytännöt

### Uusi endpoint (FastAPI)
1. DTO `mallit.py`:hyn (Pydantic, suomenkieliset kentät).
2. Logiikka omaan moduuliinsa (`triage/`, `hoitaja/` jne.), palauttaa DTO:n.
3. Reitti moduulin `APIRouter`:iin. Lukuparametrit query, kirjoitukset body.
4. Sivuvaikutukset (Twilio, Azure, LLM, kanta) dependency-injektiona (`Depends`), jotta testi feikkaa ne.

### Uusi datalähde
Toteuta `data/adapteri.py`:n rajapinta uudelle lähteelle. Älä koske muuhun koodiin.

---

## Testaus

- `pytest`. Testaa triage-säännöt ja laskenta **puhtaina funktioina** ilman ulkoisia palveluja.
- **Feikkaa Twilio, Azure ja LLM reunoilla.** Ei verkkokutsuja testeissä.
- **Älä luota LLM:n determinismiin testeissä.** Testaa sääntöpohjainen logiikka (punaiset liput,
  tason nosto) suoraan; LLM:n osuutta testataan erikseen ja löyhemmin.
- E2E: "muutos → kysymys → (feikattu) puhelu → triage → jono" -polku testiin heti kun putki toimii —
  se on myös demon käsikirjoitus.

---

## Yleiset sudenkuopat

- **LLM ei saa olla punaisten lippujen ainoa portti.** Sääntö nostaa aina, LLM vain ehdottaa.
- **Suomen puheen laatua ei saa olettaa — mittaa ajamalla** oikealla, hitaalla puheella.
  Suurin tekninen riski. Testaa Azure Speech erillään ennen puhelinkytkentää.
- **Twilio trial soittaa vain vahvistettuihin numeroihin** — vahvista demonumerot etukäteen.
- **Twilio-ääni on mulaw/8000/mono/base64** — käytä Azure TTS:n `Raw8Khz8BitMonoMulaw`-ulostuloa,
  äläkä lähetä WAV-otsaketavuja. Testaa yksi soitto läpi aikaisin.
- **Älä kovakoodaa datan kenttänimiä adapterin ulkopuolelle** — Veil.AI:n muoto nähdään vasta 13.10.
- **Ei aitoja henkilötietoja edes testifikstuureihin** — generoi satunnaisesti tai käytä Syntheaa.
- **Pidä kaikki EU-alueella** — Azure-resurssit Sweden Central / West / North Europe.
