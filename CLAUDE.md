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
# Asennus:  uv sync
# Aja:      uv run uvicorn app.main:app --reload --port 8000   (ajaa migraatiot kaynnistyksessa)
# Testit:   uv run pytest
# Lint:     uv run ruff check . && uv run ruff format .
# Migraatio mallimuutoksen jalkeen:  uv run alembic revision --autogenerate -m "kuvaus"
# Synthean ATC-vastaavuus uudelleen:  PYTHONPATH=. uv run python scripts/hae_synthea_atc.py
# Puhelun LLM (Ollama natiivisti Macilla, EI kontissa: Docker Macilla ei näe GPU:ta):
#   ollama pull gemma4:12b && ollama create hyvinvointi-gemma12 -f ollama/Modelfile.gemma12
#   .env: LLM_MALLI=hyvinvointi-gemma12, LLM_AJATTELU_POIS=true, LLM_VUORON_AIKARAJA_S=120
#   palvelin:  OLLAMA_KEEP_ALIVE=-1 ollama serve   (oletus 5 min pudottaa mallin + välimuistin)
# Puhelun tarkkuus oikeaa mallia vasten (8 skenaariota, ~10-15 min, raportti lokit/arviot/):
#   uv run python scripts/arvioi_keskustelut.py [skenaario ...]   (LLM_LAMPOTILA=0.3 vertailuun)
# Testipankki (backend/arviointi/, synteettinen): 400 keskustelupohjaa + 1484 yksittäistä
# vastausta kannan kysymyksiin; simulaattori improvisoi vanhuksen vastaukset persoonan faktoista.
#   uv run python -m arviointi.aja generoi --persoonia 400     (vaatii kannan)
#   uv run python -m arviointi.aja keskustelut --maara 50 [--sekoita] [--tapahtuma hata]
#   uv run python -m arviointi.aja yksittaiset --maara 300 --sekoita
#   raportti: lokit/testipankki/<ajo>/raportti.md, keskeytetty jatkuu: --ajo <nimi>
#   Modelfile = vain ajoasetukset (num_ctx 8192!); järjestelmäohje on puhelu/tietopohja.py:ssä
#   Malli vie ~8 Gt: sulje raskaat ohjelmat (Chrome, VS Code) 16 Gt Macilla testin ajaksi.
# Suomen tarkistus (Voikko):  brew install libvoikko   (VOIKKO_KIRJASTO, oletus /opt/homebrew/lib)
# Testisoitot DEMO_PUHELINNUMEROON (ngrok käyntiin ensin: ngrok http --url=<domain> 8000):
#   lukee vain:        curl -X POST http://127.0.0.1:8000/dev/puhelu/testi
#   puheentunnistus:   curl -X POST http://127.0.0.1:8000/dev/puhelu/kaiku   -> GET /dev/puhelu/kaiut
#   koko keskustelu:   curl -X POST http://127.0.0.1:8000/dev/puhelu/keskustelu -> GET /dev/puhelu/tulokset
#   puhelun aikajana (PUHELU_LOKI=true):  uv run python scripts/puheluloki.py [--teksti] [tiedosto]  (--teksti = keskustelu)
# Dev-reitit (/dev/...): DEV_TILA=true .env:iin. API-dokumentaatio: http://127.0.0.1:8000/docs
# DEMO: laturi kiinni + caffeinate (Mac nukahti akulla 3 min jouten kesken puhelun, 11.10.)
#   ja uvicorn ILMAN --reload (reload käynnistää palvelimen uudelleen jokaisesta .py-
#   tallennuksesta tai git pullista, ja käynnissä oleva puhelu katkeaa: mitattu 11.10.):
#   caffeinate -dims uv run uvicorn app.main:app --port 8000
#   (devissä sama: caffeinate -dims uv run uvicorn app.main:app --reload --port 8000)
#   Kansi auki: kannen sulkeminen nukuttaa koneen caffeinatesta huolimatta.
#   ngrok lokilla (näkee katkot): ngrok http --url=<domain> 8000 --log=../lokit/ngrok.log
#   esitarkistus (asetukset, tunneli julkisen osoitteen kautta, jono, malli, muisti, Twilio):
#   uv run python scripts/esitarkistus.py --lammita   (VIRHE = paluukoodi 1)
#   Mallin lämmitys (puhelu/lammitys.py): käynnistys ODOTTAA, kunnes malli on lämmin, ja
#   ajaa simuloidun puhelun (kylmänä ~2 min; LLM_LAMMITA_KAYNNISTYKSESSA=false ohittaa).
#   Sen jälkeen vahti (LLM_LAMMITYSVALI_S=60) lataa mallin, jos Ollama käynnistetään
#   uudelleen; /dev/puhelu/keskustelu = 503, kun malli on kylmä. Lämmitys kahdella eri
#   ikäluokan esimerkillä: yhdellä välimuisti ei oppinut haarakohtaa (12.10. mitattu:
#   kahdella erilainen kolmas puhelu lämpeni 3,9 s:ssa).
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
puhelu/          Twilio-puhelu (relay tai <Gather>-webhookit; vaatii JULKINEN_URL/ngrok).
                 Keskustelu Jeren mallin mukaan (puheavustaja/puheavustaja.py), keskustelu.py:
                 1) säännöt ennen mallia, 2) selkeys, 3) piilo-ohjeet, 4) malli vapaalla tekstillä
  tietopohja.py + tieto/*.json  Jeren tietopohja: järjestelmäohje (persoonallisuus, ikäluokka,
                 tehtävä + kannan kysymykset), hätämerkit kiellon tulkinnalla, vakavat huolet,
                 huoli, lopetuspyyntö, loukkaus, 20 oiretta jatkokysymyksineen ja kotihoito-
                 ohjeineen, kiinteät repliikit (versiot), numerosuodatin, puhelun pituus
  saannot.py     hatasanat.json (ei tulkitse kieltoa) + tietopohja, hoitajapyyntö,
                 lääkeneuvo, usein kysytyt (fraasit.json: kuka soittaa, mikä lääke)
  agentti.py     LLM: lammita(järjestelmäohje) avauksen aikana, vastaa(viestit) -> teksti
  keskustelu.py  tilakone (aloita, kasittele): yksi kysymys kerrallaan, varatoiminta ilman
                 mallia. Puhelun tila istunto.py:ssä (Puheluistunto, Vuoro, Rajat).
  kysymyslista.py  kannan kysymysten kirjanpito (malli saa "kysy seuraavaksi", jumissa ->
                 kysymys lisätään perään), viimeksi_kysytty lääkemuutoksen poikkeamalle
  lopetus.py     mallin LOPETUKSEN estot yhtenä puhtaana sääntönä (henkilö kysyi, oire
                 kesken, huono vointi, lista kesken) ja lopetuksen sanat
  kaiku.py       VAIN DEV: puheentunnistuksen kaikutesti
  kielentarkistus.py  Voikko: mallin suomi vain lokiin (kieli:epavarma), ei estä puhetta;
                 korjaa_kieli + kielikorjaukset.json: mallin toistuvat virheet korjataan
                 ennen puhetta (kieli:korjattu). Taulukko käsin Voikon löydöksistä (Voikon
                 ehdotukset usein vääriä). 12.10.: Voikon virhe 15 % -> 0 % samassa aineistossa,
                 uusissa vastauksissa tulee uusia: täydennä testipankin ajojen jälkeen.
  hatasanat.json hätämerkit säännöllä (testipankki tests/test_hatasanat.py)
  istunnot.py    käynnissä olevat puhelut ja dev-tulokset muistissa; puhelun triage-yhteenveto
                 tallennetaan puhelun_tulos-tauluun, raakavastauksia ei tallenneta
  reitit.py      /vuoro: hidas LLM -> "Hetkinen..." + <Redirect> /odota (aikaa ~19 s, ei aikakatkaisua)
  relay.py       PUHELU_TAPA=relay: Twilio ConversationRelay (wss): AI puhuu loppuun JA kuuntelee;
                 sama tilakone, välitäyte suoraan, lopetus <Connect action> -> /relay-loppu
  twilio_tarkistus.py  X-Twilio-Signature; soittaja.py = ainoa Twilio-reuna
dev/             VAIN DEV (DEV_TILA=true): testimuutokset, jonon nollaus, demonumero
triage/          puhelun jälkeen: saannot.py sääntötaso heti (punainen/keltainen/vihreä +
                 perusteet), analyysi.py Jeren LLM-analyysi taustasäikeessä (tyontekija.py),
                 taso = korkeampi säännön ja mallin tasosta. Yhteenveto tallentuu
                 puhelun_tulos-tauluun ja päivittyy mallianalyysin valmistuttua
hoitaja/         soittotehtävänäkymä + tallennettu yhteenveto, katseluloki demo-katsojana;
                 palvelu.py: tehtävän uusin tulos DTO:na (sama jonolle ja tapaussivulle)
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

### Puhelu ja ääni

**Toteutettu (demo, 10.10.):** Twilio `<Gather input="speech" language="fi-FI">` + `<Say>`
(`Polly.Suvi-Neural`) webhookeilla. Twilio tekee puheentunnistuksen (Google/Deepgram) ja
-synteesin (Amazon Polly) = US-käsittelijät; kerrotaan pitchissä rehellisesti. Mitattu
kaikutestillä: suomi tunnistui varmuudella 0,74–0,97. Tilakone, hätäsanat ja agentti ovat
ääniputkesta riippumattomia, joten vaihto alla olevaan putkeen muuttaa vain reunan.

**Tuotannon polku (ei toteutettu): Polku A = Azure Speech fi-FI -putki**
Ketju: `Twilio ↔ Azure STT (fi-FI) → LLM (paikallinen, Ollama) → Azure TTS (fi-FI) ↔ Twilio`.
Valittu luotettavuuden ja suomen laadun takia; antaa myös puhtaan litteroinnin triagelle. Twilio
vain kuljettaa äänen, se ei tee puheentunnistusta eikä -synteesiä. Polku B (Azure OpenAI Realtime,
gpt-realtime, Sweden Central) on venytys päivälle 2 VAIN jos suomi testaa hyvin — älä aloita siitä.

**LLM puhelussa (Jeren malli, 11.10.):** malli käy keskustelun vapaasti tekstinä; järjestelmä-
ohje + historia viesteinä, piilo-ohjeet "(Ohje: ...)" / "(Tietopohja: ...)" henkilön viestin
perässä, LOPETUS ja UUDELLEEN merkkeinä. Malli: **Gemma 4 12B** (`hyvinvointi-gemma12`, valittu
11.10.: 8B-mallit tulkitsivat vastauksia väärin ja ohittivat ohjeita; 12B ei vielä mitattu tällä
puhelulla). Aiemmat mallit ja opit:
- Jere kehitti ohjeensa gemma4:e4b:llä. Mitattu e4b (M2, JSON-arvio): vuoro 1,8–5,2 s.
- Poro 2 8B (suomeen koulutettu Llama 3.1): Jeren mallissa vuoro 2–9 s, mutta tulkinnat ja
  ohjeiden noudattaminen heikkoja (kolme kysymystä kerralla, "jes" luettu väärin).
- **num_ctx vähintään 8192**: järjestelmäohje on 2788 tokenia (mitattu); 4096:lla Ollama pudotti
  alusta osan ja malli "unohti" kysymyslistan.
- Gemma 4 12B mitattu 11.10. (M2 16 Gt, Ollama 0.40, mlx-ajuri, nvfp4 7,7 Gt): lämmin vuoro
  3–5 s; järjestelmäohjeen (~2840 tok) käsittely tyhjästä 41–48 s (~60 tok/s). Muisti on
  pullonkaula: ajuri 15 Gt (painot + välimuistit) + Dockerin VM 8 Gt -> swap 16,8 Gt.
- Välimuisti (mlx, mitattu 11.10.): ajuri käyttää uudelleen vain tallettamiaan tiloja eikä kelaa
  mielivaltaisesti taaksepäin (avauksen vaihto 40 tok lopussa -> palasi 2693:een). Haarakohdat
  se oppii: kun kaksi puhelua on eronnut samassa kohdassa, seuraava käyttää sen. Siksi
  puhelukohtaiset osat (ikäluokan puhetapa, lääkemuutos, kysymykset) ovat ohjeen LOPUSSA
  (tietopohja._puhelun_tiedot): uusi puhelu, jonka kaikki tiedot eri, lämpeni 7,7 s (2545/2853
  tok välimuistista); ikäohje alussa sama oli 47,6 s (4 tok). Mallin uudelleenlatauksen jälkeen
  kaksi ensimmäistä puhelua ovat kylmiä. OLLAMA_KEEP_ALIVE (oletus 5 min) pudottaa mallin ja
  välimuistin: aja `OLLAMA_KEEP_ALIVE=-1 ollama serve`.
- Lämmitys soiton alkaessa (lammita_ja_merkitse) lähettää järjestelmäohjeen + valitun avauksen,
  eli täsmälleen ensimmäisen vuoron alun: ensimmäinen vuoro 3,1 s (ohje + "Hei" -lämmityksellä
  21,3 s). Ensimmäinen vuoro odottaa lämmitystä välitäytteiden kanssa.
- Relay, henkilön puheen luokittelu (relay.Relayistunto.ota_vastaan), järjestyksessä:
  1) AI:n oman puheen kaiku hylätään (vähintään 2 sanaa peräkkäin AI:n tekstistä, `kaiku`).
  2) Puhe, joka ALKOI ennen kuin AI ehti repliikin kysymykseen, ohitetaan (`puhe_ai_puhuessa`).
  3) Mallin miettiessä: jatkoksi kelpaa vain ennen ensimmäistä välitäytettä alkanut puhe (tauko
     kesken lauseen) -> vuoro alusta koko puheella (`vuoro_alusta`, MAX_JATKOT). Välitäytteen
     jälkeen tullut on sivupuhetta ja ohitetaan (12.10.: "siinä on vieläkin tää bugi" aloitti
     vuoron alusta, vuoro 52 s). Hätämerkki ja vakava huoli käsitellään aina heti.
  Alkuhetki osittaisista tunnistuksista (partialPrompts, RELAY_OSITTAISET), muuten arvio. Kesto-
  arviot 14 merkkiä/s (ei mitattu Twiliosta). Väärä luokitus ei jumita: hiljaisuusvahti toistaa
  kysymyksen 15 s AI:n puheen jälkeen (`hiljaisuus`, toisella lopetus). Ollama keskeyttää
  generoinnin, kun pyyntö perutaan (mitattu).
- Säännöt (11.10. ilta): kiroilu oireen yhteydessä ei ohita oiretta (malli saa tietopohjan),
  ja mallin LOPETUS ohitetaan, jos henkilön viimeinen vastaus on huono vointi ("No huonosti").
- Lääkemuutoksen noudattaminen (12.10.): juuri kysytyn muutoskysymyksen vastaus kuvioilla
  (keskustelusaannot.json `laakemuutoksen_poikkeama`, kysymyksen tunnisteen mukaan, esim.
  lopetettu lääke "jatkanut") -> keltainen. Agentti ei neuvo eikä kysele syytä (moite,
  hoitaja selvittää). Keltaisen löydöksen jälkeen lopetuslause lupaa hoitajan soiton tänään
  (`lopetuslause_huolestuttava`), ei "tarvittaessa". Puhelussa vain jälkikäteinen analyysi huomasi.
- Muisti 16 Gt Macilla (mitattu 11.10. ilta): Dockerin VM 8,2 Gt + Ollama 15 Gt + VS Code ~3 Gt
  -> swap 15 Gt, lämmin vuoro 6-8 s (vs 3-5 s), yksi vuoro 51 s. Docker Desktopin muisti 2 Gt:hen
  (Postgres riittää) ja raskaat ohjelmat kiinni soittojen ajaksi. Mallin sivut jäivät pakattuina
  muistiin vielä Dockerin vapautettua muistia (vuoro 10-17 s): `ollama stop hyvinvointi-gemma12`
  ja uusi lataus -> lämmin vuoro 4,5-6,7 s; kaksi kylmää lämmitystä 46-54 s, kolmas 6,2 s.
- `keep_alive: -1` lähtee jokaisessa mallipyynnössä (LLM_PIDA_MUISTISSA, oletus true): malli ei
  putoa muistista testisoittojen välissä, eikä `OLLAMA_KEEP_ALIVE` ole enää pakollinen.
- Järjestelmäohje ja tehtävä ovat Jeren tekstejä SANATARKASTI (tietopohja.JARJESTELMAOHJE,
  puhelun_tehtava), mutta puhelukohtaiset tiedot on siirretty loppuun (välimuisti, ks. yllä);
  lisätty vain sinuttelusääntö, faktat ja avauksen soiton syy. Meidän
  lisäämämme ohjaus: kysymyslistan kirjanpito ("kysy seuraavaksi listalta"), oireen
  käsittely ennen listaa (2 vuoroa, sitten kotihoito-ohjeet), yksi kysymys kerrallaan,
  LOPETUS ei kelpaa, jos henkilö kysyi juuri jotain. Arvioinnilla perusteltu (11.10.).
- mlx-ajuri ei tue pakotettua JSON-muotoa (501): triage-analyysi kokeilee lopuksi vapaana.
- Gemma teitittelee oletuksena: sinuttelu on ohjeen alussa omana sääntönään.
- **Tarkkuus ennen nopeutta (päätetty 11.10.)**, mutta ei minuutin hiljaisuuksia: välitäytteet
  6 s ja sen jälkeen 9 s välein; ensimmäinen vuoro odottaa soiton aikana alkanutta lämmitystä
  enintään 12 s, sitten listan kysymys säännöllä (11.10. puhelu kuunteli täytteitä 70 s).
  Ajattelu pois puhelussa (Gemma 12B: 121 s/vuoro) ja oletuksena myös analyysissa
  (LLM_ANALYYSI_AJATTELU): Ollama palvelee yhtä pyyntöä kerrallaan, joten analyysi odottaa,
  kunnes puheluita ei ole käynnissä. Muutokset mitataan testipankilla ennen käyttöönottoa.
- llama.cpp/GGUF-ajuri ei ollut nopeampi (mitattu 11.10.), joten pysytään mlx:ssä.
- `reasoning_effort: "none"` (gemma "ajattelee" muuten); mlx-ajurilla ei structured outputia.
- Säännöillä tulkitseminen ilman asiayhteyttä rikkoi puheluita ("lopetin sen" vastauksena
  kysymykseen lopettamisesta): säännöt vain turvallisuuteen ja kiinteisiin vastauksiin.

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

**Puhuttelu: sinuttelu** (päätetty 11.10., kun Jeren tietopohja yhdistettiin): kiinteät
repliikit ja mallin ohje sinuttelevat; kielentarkistus kirjaa mallin teitittelyn lokiin. Jeren polut tilakoneessa (keskustelu.py): itsetuhoisuus ja vakavat huolet ->
tukitila (kuuntelu, turvallisuus, ei kysymyksiä; malli vapaalla tekstillä, varalla kiinteä),
kieltomuotoinen itsetuho -> varmistuskysymys, lopetuspyyntö (lopetus heti, 12.10.; itsetuhoisen
puheen jälkeen ensin yksi turvallisuuskysymys), loukkaukset (3. kerralla lopetus), toistopyyntö,
kotihoito-ohjeet oireeseen (malli saa ne tietopohjasta piilo-ohjeena, ei keksi omia;
yleisiä omahoito-ohjeita, ei lääkeneuvoja). Puhelinnumerot luetaan numero
kerrallaan (puhe.puheeksi). puheavustaja/ jää Jeren erilliseksi mikrofonidemoksi
(Whisper + Piper), backend ei tuo sieltä koodia.

### Triage
Litteroinnista rakenteinen yhteenveto (oire, voimakkuus, alkoiko lääkkeen jälkeen, varmuus).
Kolme tasoa: vihreä (kirjataan), keltainen (hoitaja soittaa tänään), punainen (heti). LLM ehdottaa
tason; **sääntöpohjaiset punaiset liput voivat vain nostaa sitä.** Tallenna vain yhteenveto.
Toteutettu (11.10., app/triage/): sääntötaso heti puhelun päätyttyä, Jeren LLM-analyysi
(JSON-skeema, varalla sääntöjen yhteenveto) taustalla; mitattu Poro M2:lla 30,5 s.
Tulos GET /dev/puhelu/tulokset näyttää kehityksessä myös muistissa olevat vastaukset;
kantaan tallennetaan vain hoitajalle tarkoitettu yhteenveto. Hälytyssanat versionhallituissa datatiedostoissa
testipankin kanssa (hatasanat.json + tests/test_hatasanat.py, tieto/ + tests/test_tietopohja.py).

### Hoitajan näkymä
Jono kiireellisyysjärjestyksessä, yhteenveto + perustelu ("miksi keltainen"), Soita-painike,
kuittaus ja palautekenttä (luokitus oli oikea/väärä). Yhteenveto näkyy tehtävänäkymässä;
litterointia ei tallenneta eikä näytetä (datan minimointi). Tehtävänäkymän katselut kirjataan
audit-lokiin demo-katsojana, koska hackathonissa ei ole kirjautumista. Tuotannossa Entra ID
(MFA + roolit).

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
- **"Application error" kesken puhelun = Mac nukahti** (11.10., kahdesti): akulla näyttö sammuu
  2 min ja kone nukahtaa 1 min myöhemmin, kun puhelun aikana ei kosketa konetta. Twilion
  paluukutsut (`/puhelu/relay-loppu`, `/puhelu/tila`) saivat 404:n ngrokin reunalta (virhe 11200).
  Tunnistus: puheluloki loppuu ilman `relay_paattyi`-riviä, `pmset -g log | grep "Idle Sleep"`
  näyttää lepotilan samalta minuutilta. Claude Coden oma `caffeinate -t 300` ei riitä. Korjaus:
  laturi + `caffeinate -dims` uvicornin edessä (esitarkistus tarkistaa). Sama 404 tulee myös
  oikeasta ngrok- tai verkkokatkosta: varaverkko (puhelimen hotspot) Dipoliin.
