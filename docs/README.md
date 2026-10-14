# Tekninen dokumentaatio: tietoturva, mallit, toimivuus ja rakenne

Agenttinen hyvinvointikumppani · SOTE AI Hackathon 2026, haaste 3.1  
Tiimi: Savon Ammattiopisto (Risto, Justus, Jere)

Tämä dokumentti kokoaa yhteen, **mitä koodissa on oikeasti toteutettu**. Projektin yleiskuva ja käynnistysohjeet: [../README.md](../README.md).
Sääntelyanalyysi perusteluineen: [../SAANTELY.md](../SAANTELY.md).

## Sisällys

- [Yleiskuva](#yleiskuva)
- [Tietoturva ja tietosuoja](#tietoturva-ja-tietosuoja)
- [Käytetyt kielimallit (LLM)](#käytetyt-kielimallit-llm)
- [Käytetyt äänimallit (puheentunnistus ja puhesynteesi)](#käytetyt-äänimallit-puheentunnistus-ja-puhesynteesi)
- [Koodin toimivuus](#koodin-toimivuus)
- [Kansiorakenne](#kansiorakenne)

---

## Yleiskuva

Muutos synteettisessä terveysdatassa (esim. uusi lääke tai annosmuutos) laukaisee
puhelinsoiton ikäihmiselle. Ääniagentti keskustelee suomeksi, triage luokittelee
kiireellisyyden (vihreä / keltainen / punainen) ja hoitaja saa priorisoidun jonon,
potilassivun ja oirehistorian. Ihminen tekee aina hoitopäätöksen.

```
synteettinen data ─▶ lääkemuutosten tunnistus ─▶ soittojono + kysymykset
   (Postgres)          (deterministinen, ATC)        (sisältö kannasta)
                                                           │
hoitaja soittaa ◀── hoitajan näkymä ◀── triage ◀── puhelu (Twilio + paikallinen LLM)
                 (jono, potilassivu,  (sääntö + LLM)   (suomeksi, sinutellen,
                  oirehistoria)                          muistaa edellisen oireen)
```

Suunnitteluperiaate: **LLM vain siellä, missä sitä tarvitaan.** Muutosten tunnistus,
hätämerkit, hälytysrajat, triagen sääntötaso ja oireen seurannan luokittelu ovat
deterministisiä. LLM käy keskustelun ja tiivistää puhelun hoitajalle. Sääntö voi vain
**nostaa** kiireellisyyttä, ei laskea.

---

## Tietoturva ja tietosuoja

### Toteutettu koodissa

| Alue | Toteutus | Missä |
|---|---|---|
| Vain synteettinen data | Synthea-profiilit, ei aitoja henkilötietoja koodissa, testeissä eikä demoissa. Hoitajan näkymän nimet ja henkilötunnukset ovat **synteettisiä**: ne johdetaan deterministisesti `vanhus_id`:stä yleisistä suomalaisista nimistä, ja henkilötunnus on vain muodoltaan oikea. Täyttö tehdään vain dev-tilassa (käynnistyksessä tai `POST /dev/henkilotiedot/tayta`). Soitot menevät tiimin omaan, Twilioon vahvistettuun demonumeroon. | `backend/app/dev/henkilotiedot.py`, `main.py`, `DEMO_PUHELINNUMERO` |
| Pseudonymisointi | Tunnistetiedot (nimi, henkilötunnus) ovat omassa taulussaan `vanhuksen_henkilotieto` ja puhelinnumero taulussa `vanhuksen_yhteystieto`. Terveystiedot (soittotehtävät, puhelun tulokset) linkittyvät niihin vain `vanhus_id`:llä. Migraatio `e9b1c2d3a4f5` siirsi nimet pois `puhelutehtava`-taulusta. | `backend/alembic/versions/`, `tietokanta.py` |
| PII ja LLM | Nimeä, henkilötunnusta tai puhelinnumeroa ei lähetetä mallille. Puhelukoodi (`puhelu/`) ei lue tunnistetietotaulua lainkaan; LLM saa vain kysymykset, lääkemuutoksen kontekstin ja edellisen puhelun oireen. LLM:llä ei ole pääsyä kantaan. | `puhelu/tietopohja.py`, `puhelu/agentti.py` |
| Paikallinen LLM | Demossa malli ajetaan Ollamalla omalla koneella: puhelun sisältö ei lähde koneelta mallille. | `ollama/`, `LLM_BASE_URL` |
| Salaisuudet | Kaikki avaimet ja salasanat vain `.env`-tiedostossa, joka on `.gitignore`ssa. Repossa vain `.env.example`. Konfiguraatio luetaan yhdestä paikasta (`config.py`, pydantic-settings). | `.gitignore`, `backend/app/config.py` |
| Datan minimointi | Kantaan tallennetaan vain hoitajalle tarkoitettu puhelun yhteenveto (`puhelun_tulos`). Litterointia ja raakavastauksia ei tallenneta, äänitallenteita ei ole. | `puhelu/istunnot.py`, `triage/` |
| Muistin säilytysraja | Agentti muistaa edellisen puhelun oireen vain `MUISTI_PAIVAT` päivän ajan (oletus 30). Muistiin otetaan säännöllä vain oire, voimakkuus ja päivät, ei vakavia huolia eikä litterointia. Seurannan vastaus tallentuu rakenteisena luokkana (poistunut / lievittynyt / ennallaan / pahentunut / epäselvä). | `backend/app/muisti/` |
| Audit-loki | Sekä tapausnäkymän että potilassivun jokainen katselu kirjataan `katseluloki`-tauluun (toiminto ja `tehtava_id` tai `vanhus_id`). Katsoja on "demo", koska hackathonissa ei ole kirjautumista. | `hoitaja/palvelu.py`, `hoitaja/reitit.py` |
| Ei kolmannen osapuolen skriptejä | Oirehistorian kaaviokirjasto ECharts 5.5.1 on tallennettu repoon (`static/vendor/`, Apache 2.0 -lisenssi mukana). Hoitajan selain ei hae mitään ulkoisesta CDN:stä, joten potilassivulta ei vuoda pyyntöjä ulos. | `backend/app/static/vendor/` |
| Twilio-webhookien aitous | Jokaisen Twilio-kutsun `X-Twilio-Signature` tarkistetaan; väärä allekirjoitus = 403. Estää "vastausten" syöttämisen puheluun ngrok-osoitteen kautta. | `puhelu/twilio_tarkistus.py` |
| Salattu yhteys | Twilio ↔ backend vain `https`/`wss` (ngrok devissä). | `JULKINEN_URL`, `puhelu/relay.py` |
| Dev-reitit lukossa | `/dev/...`-reitit (testimuutokset, testisoitot, henkilötietojen täyttö) toimivat vain, kun `DEV_TILA=true`. Puhelukohtainen litterointiloki vaatii lisäksi `PUHELU_LOKI=true` ja on gitignoressa (`lokit/`). | `dev/reitit.py`, `puhelu/loki.py` |
| Docker-dev | Portit vain `127.0.0.1`:ssä (ei näy hackathonin wifiin), salasanat pakollisia (compose ei käynnisty ilman `.env`:iä), Postgres `scram-sha-256`, pgAdmin kirjautumisen takana eikä tallenna kannan salasanaa, kiinnitetyt image-versiot. | `docker-compose*.yml` |
| Turvallisuussäännöt puhelussa | Agentti kertoo heti olevansa tekoäly (AI Act art. 50). Hätämerkit (rintakipu, hengenahdistus, halvausoireet) ja itsetuhoinen puhe tunnistetaan **säännöllä** reaaliajassa; hätämerkissä ohjataan soittamaan 112. Agentti ei diagnosoi eikä anna lääkeneuvoja ("Välitän tämän hoitajalle"). | `puhelu/hatasanat.json`, `puhelu/saannot.py`, `puhelu/keskustelu.py` |

### Tunnetut heikkoudet (rehellisesti)

- **Puhelinverkko (PSTN) ei ole päästä päähän salattu.** Siksi puhelussa ei käsitellä
  henkilötunnusta eikä diagnooseja.
- **Twilio on yhdysvaltalainen käsittelijä**, ja demossa myös puheentunnistus ja -synteesi
  tapahtuvat Twilion kautta (Google / Amazon Polly). Tuotannossa: DPA + SCC/DPF, tai
  EU-natiivi operaattori (46elks, Sinch, Telnyx EU) ja Azure Speech Sweden Centralissa.
- **Hoitajan näkymässä ei ole kirjautumista**, vaikka se näyttää nimen ja (synteettisen)
  henkilötunnuksen. Demossa tämä on hyväksyttävää, koska data on synteettistä ja palvelin
  ajetaan paikallisesti. Tuotannossa pääsy Entra ID:n kautta (MFA + roolipohjainen pääsy);
  audit-loki on jo valmiina.
- Tunnistetiedot ja terveystiedot ovat erillisissä tauluissa mutta **samassa kannassa**.
  Tuotannossa erillinen rekisteri ja oma pääsynhallinta.
- Lääkkeiden riskiluokat, oireiden kotihoito-ohjeet ja oireen seurannan luokittelu **eivät ole
  kliinisesti tarkistettuja** (tuotannossa Fimean Lääke 75+ ja kliininen hyväksyntä).

### Tuotannon polku (ei toteutettu)

Azure Database for PostgreSQL (Sweden Central), salaisuudet Azure Key Vaultissa, pääsy Entra
ID:llä, LLM EU-hostattuna (esim. Azure OpenAI EU Data Zone + nollasäilytyspyyntö), DPIA
(GDPR art. 35). MDR: oireiden vakavuutta arvioiva ohjelmisto on todennäköisesti luokan IIa
lääkinnällinen laite; tuote on rajattu hoitajan päätöksen tueksi. Tarkemmin: `SAANTELY.md`.

---

## Käytetyt kielimallit (LLM)

Koodi kutsuu mallia **OpenAI-yhteensopivan rajapinnan** kautta (`LLM_BASE_URL`, `LLM_MALLI`),
joten mallin tai palveluntarjoajan vaihto on pelkkä asetusmuutos. Kuluttaja-API:ita (suora
OpenAI / Gemini / Claude) ei käytetä potilassisältöön.

| Malli | Ollama-nimi (Modelfile) | Tila | Lisenssi | Huomiot |
|---|---|---|---|---|
| **Gemma 4 12B, GGUF Q4_K_M** (Google) | `hyvinvointi-gemma12-gguf` (`ollama/Modelfile.gemma12-gguf`, pohjana `gemma4:12b-it-q4_K_M`) | **Oletus** | Apache 2.0 | Valittu M2 16 Gt -vertailulla: kaksi kahdeksan skenaarion ajoa 24/24 sisältöodotusta, painotettu mallivuoro 5,9–6,0 s, kokonaisaika 112–159 s. llama.cpp-ajuri. |
| Gemma 4 12B, NVFP4 (MLX) | `hyvinvointi-gemma12` (`ollama/Modelfile.gemma12`) | Varamalli | Apache 2.0 | Aiempi oletus. Samassa vertailussa 24/24, mutta vuoro 8,4 s (q8-KV + Flash) tai 17,5 s (alkuperäinen), kokonaisaika 300–367 s. |
| Gemma 4 E4B (Google) | `hyvinvointi-gemma` (`ollama/Modelfile`) | Varamalli | Apache 2.0 | Jere kehitti keskusteluohjeet tällä. Nopea (vuoro 1,8–5,2 s), ymmärtää heikommin. Käytössä myös `puheavustaja/`-mikrofonidemossa. |
| Poro 2 8B Instruct (LumiOpen, Llama 3.1 -pohja) | `hyvinvointi-poro` (`ollama/Modelfile.poro`) | Varamalli | Llama 3.1 Community License | Suomeen koulutettu, vuoro 2–9 s, mutta tulkinnat ja ohjeiden noudattaminen heikkoja (esim. kolme kysymystä kerralla). |

Ajoasetukset (oletusmalli):

- Modelfile: `num_ctx 8192` (järjestelmäohje ~2800 tokenia; 4096:lla malli "unohti"
  kysymyslistan), `num_predict 120`, `top_p 0.95`, `top_k 64`.
- Backend: lämpötila 0,6 (`LLM_LAMPOTILA`), vastauksen enimmäispituus 120 tokenia
  (`LLM_VASTAUKSEN_MAX_TOKENIT`, vastaus luetaan ääneen), ajattelu pois
  (`LLM_AJATTELU_POIS=true`, muuten vuoro ~121 s), `keep_alive -1`, kylmän mallin lämmityksen
  aikaraja 300 s (`LLM_LAMMITYS_AIKARAJA_S`).
- Ollama-palvelin: `OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_KEEP_ALIVE=-1
  OLLAMA_NUM_PARALLEL=1 ollama serve` (vain yksi palvelin kerrallaan 16 Gt koneella).

**Mihin LLM:ää käytetään:**
1. **Puhelun keskustelu** (`puhelu/agentti.py`, `puhelu/keskustelu.py`): vapaa suomenkielinen
   keskustelu järjestelmäohjeen ja tietopohjan (`puhelu/tieto/*.json`) varassa. Tilakone ja
   säännöt ajetaan ennen mallia; ilman mallia puhelu jatkuu varatoiminnalla.
2. **Puhelun jälkeinen analyysi** (`triage/analyysi.py`): JSON-yhteenveto hoitajalle
   taustasäikeessä. Lopullinen taso = korkeampi säännön ja mallin tasosta.

**Mihin LLM:ää EI käytetä:** lääkemuutosten tunnistus, hätämerkit, triagen sääntötaso,
puhelun lopetuksen estot, muisti, oireen seurannan luokittelu (`muisti/seuranta.py`) ja
oirenimien yhtenäistäminen oirehistoriaan (`hoitaja/historia.py`) ovat sääntöpohjaisia.

**Mallin suomen tarkistus:** Voikko-oikolukija (`puhelu/kielentarkistus.py`) kirjaa mallin
epävarmat sanat lokiin, ja `kielikorjaukset.json` korjaa toistuvat virheet ennen puhetta.

**Arviointi:** `backend/arviointi/` sisältää synteettisen testipankin (400 persoonaa,
keskustelupohjat ja ~1500 yksittäistä vastausta) ja simulaattorin, jolla mallia mitataan
oikeaa Ollamaa vasten (`uv run python -m arviointi.aja ...`,
`PYTHONPATH=. uv run python scripts/arvioi_keskustelut.py`).

---

## Käytetyt äänimallit (puheentunnistus ja puhesynteesi)

### Varsinainen puhelu (backend, Twilio)

Puhelun tapa valitaan `PUHELU_TAPA`-asetuksella. Kummassakin Twilio hoitaa puheentunnistuksen
ja -synteesin; backend käsittelee vain tekstiä.

| Tapa | Puheentunnistus (STT) | Puhesynteesi (TTS) | Huomiot |
|---|---|---|---|
| `gather` (oletus) | Twilio `<Gather input="speech" language="fi-FI">`, palveluntarjoajana Google/Deepgram. Malli `STT_MALLI` (tyhjä = Twilion oletus; suomi vahvistettu: `experimental_conversations`, `experimental_utterances`). | Twilio `<Say>`, ääni **`Polly.Suvi-Neural`** (Amazon Polly). Vaihtoehto `Google.fi-FI-Wavenet-B`. | Webhookit. Mitattu kaikutestillä: suomi tunnistui varmuudella 0,74–0,97. Alle 0,6 varmuus → pyydetään toistamaan. Hidas LLM → "Hetkinen..." ja uudelleenhaku. |
| `relay` | Twilio ConversationRelay, `RELAY_STT=Google` (Googlen oletusmalli; vihjeet pois, koska ne kaatoivat puhelun virheeseen 64101). | `RELAY_TTS=Amazon`, ääni **`Suvi-Neural`**. | Websocket (`wss`): AI puhuu lauseen loppuun ja kuuntelee samaan aikaan. Puheen tauko 1500 ms ikäihmisille. Oman puheen kaiku suodatetaan. |

Puheentunnistuksen vihjesanasto: `puhelu/vihjesanat.json` (+ `STT_LISAVIHJEET`). Numerot
(esim. 112) luetaan numero kerrallaan (`puhelu/puhe.py`).

**Suunniteltu tuotantopolku (ei toteutettu):** Azure Speech fi-FI Sweden Centralissa
(`fi-FI-SelmaNeural`, ulostulo `Raw8Khz8BitMonoMulaw`) Twilio Media Streamsin kautta, jolloin
Twilio vain kuljettaa äänen. Tilakone, hätäsanat ja agentti eivät riipu ääniputkesta, joten
vaihto muuttaa vain reunan.

### Mikrofonidemo (`puheavustaja/`, Jeren erillinen prototyyppi)

Toimii kokonaan paikallisesti ilman puhelinta. Backend ei tuo tästä koodia; tietopohja
on siirretty backendiin.

| Osa | Malli |
|---|---|
| STT | **faster-whisper**: `large-v3-turbo` (float16) NVIDIA-GPU:lla, muuten `medium` (int8) CPU:lla; kieli fi, beam 5 |
| LLM | Ollama `gemma4:e4b` |
| TTS | **Piper**: `fi_FI-harri-medium` (oletus), lisäksi `sv_SE-nst-medium` ja `en_GB-alan-medium`, kun henkilö puhuu ruotsia tai englantia. Puhenopeus 0,85 ikäihmisille. |

---

## Koodin toimivuus

### Testit ja laatu

```
cd backend
uv sync
uv run pytest          # 585 passed (~2,5 min), ei verkkoa eikä Postgresia: SQLite + migraatiot
uv run ruff check .    # All checks passed!
```

Testit feikkaavat Twilion ja LLM:n reunoilla. Katettu mm.: hätäsanat (testipankki),
tietopohja, triage-säännöt, priorisointi, lääkemuutokset, muisti ja oireen seuranta,
oirehistoria, potilassivu, migraatiot, puhelun tilakone ja lopetus, relay, kielikorjaukset,
reitit sekä hoitajan näkymän HTML-tilannekuvat (`tests/tilannekuvat/`). Harmiton varoitus:
Voikko-kirjaston siivousvaroitus ympäristössä, jossa libvoikkoa ei ole asennettu.

### Mikä toimii päästä päähän

1. Lääkemuutosten tunnistus demodatasta (ATC-koodeihin perustuen) ajastettuna
   (`TUNNISTUS_VALI_MINUUTIT`, oletus 15) tai käsin `POST /puhelutehtavat/tunnista`.
2. Soittojono prioriteetteineen ja kysymyksineen (`GET /puhelutehtavat`).
3. Ulossoitto Twiliolla, suomenkielinen keskustelu paikallisella Gemma 4 12B:llä, hätämerkkien
   tunnistus kesken puhelun. Agentti kysyy edellisessä puhelussa kerrotusta oireesta, ja vastaus
   tallentuu rakenteisesti (oire jatkuu → keltainen).
4. Triage heti puhelun jälkeen (sääntötaso) ja tarkennus LLM-analyysillä taustalla.
5. Hoitajan näkymä `http://127.0.0.1:8000/hoitaja/`:
   - takaisinsoittojono kiireellisyysjärjestyksessä
   - tapaussivu: yhteenveto, perustelut, edellinen puhelu ja oireen muutos
   - potilaslista `/hoitaja/potilaat/` ja potilassivu: perustiedot, käytössä olevat lääkkeet,
     soittohistoria ja soittotehtävät
   - "Oireet puheluissa" -kaavio (ECharts) tapaus- ja potilassivulla
   - katseluloki
6. Demon oirehistorian voi luoda skriptillä `scripts/luo_demo_oirehistoria.py`.

### Ajoympäristön vaatimukset ja rajoitukset

- Python 3.12 + `uv`, Docker (Postgres 17), Ollama natiivisti (Macilla ei kontissa, koska
  Docker ei näe GPU:ta). 16 Gt koneella raskaat ohjelmat kiinni, Dockerin muisti pieneksi ja
  vain yksi Ollama-palvelin.
- Puheluun tarvitaan Twilio-tili, vahvistettu demonumero ja julkinen `https`-osoite (ngrok).
- Synteettinen data (`data/csv/`, ~80 Mt) ei ole repossa, vaan se jaetaan tiimin kesken.
- Demossa uvicorn **ilman** `--reload`ia ja kone hereillä (`caffeinate -dims`): lepotila tai
  uudelleenlataus katkaisee käynnissä olevan puhelun.
- Esitarkistus ennen demoa: `uv run python scripts/esitarkistus.py --lammita`.
- Ei vielä testattu oikealla puhelulla: `RELAY_OSITTAISET=true` (osittaiset tunnistukset).
- Paikallisen mallin suomi on heikompi kuin suurten mallien; siksi turvallisuus ei nojaa
  malliin.

---

## Kansiorakenne

```
HackathonEspoo/
├── README.md                  Projektin yleiskuva, käynnistys, aikataulu
├── SAANTELY.md                Sääntely (GDPR, AI Act, MDR, EHDS) ja tietoturvalinjaukset
├── CLAUDE.md                  Kehitysohjeet, lukitut tekniset päätökset ja mittaukset
├── .env.example               Asetuspohja (oikea .env ei repossa)
├── docker-compose.yml         Postgres 17 (vain dev, localhost)
├── docker-compose.dev.yml     + pgAdmin ja synteettisen demodatan lataus
├── docker/
│   ├── postgres/init-dev/     CSV:t -> skeema "demo" ensimmäisellä käynnistyksellä
│   └── pgadmin/servers.json
├── ollama/                    Modelfilet: gemma12-gguf (oletus), gemma12 MLX, gemma e4b, Poro
├── docs/                      Tämä dokumentti, UI-suunnitelma, logo, luonnokset
├── puheavustaja/              Jeren mikrofonidemo: Whisper -> Ollama -> Piper
│   └── knowledge/             Tietopohjan alkuperäiset JSONit
└── backend/                   FastAPI-sovellus (Python 3.12, uv)
    ├── pyproject.toml, uv.lock
    ├── alembic/               Tietokantamigraatiot (skeema, kysymyssisältö, henkilötietotaulu)
    ├── sql/                   Kysymyssisällön muokattava SQL
    ├── scripts/               Esitarkistus, puhelulokin luku, mallin arviointi, ATC-haku,
    │                          demon yhteenvedot ja oirehistoria
    ├── arviointi/             Synteettinen testipankki + vanhussimulaattori LLM:n mittaukseen
    ├── tests/                 pytest (585 testiä) + HTML-tilannekuvat
    └── app/
        ├── main.py            Sovellus, reitit, migraatiot, ajastin, dev-tilan demonimet
        ├── config.py          Kaikki asetukset (.env, pydantic-settings)
        ├── mallit.py          Pydantic-DTO:t (rajapintasopimus)
        ├── tietokanta.py      SQLAlchemy-taulut (jono, tulokset, tunnistetiedot, katseluloki)
        ├── data/              ADAPTERIKERROS: kaikki terveysdata luetaan tätä kautta
        ├── muutokset/         Lääkemuutosten tunnistus (deterministinen, ATC)
        ├── kysymykset/        Kysymykset ja LLM-konteksti muutoksista (sisältö kannasta)
        ├── puhelutehtavat/    Soittojono, priorisointi, ajastus, tunnistetietojen varasto
        ├── puhelu/            Twilio-puhelu: tilakone, säännöt, hätäsanat, agentti (LLM),
        │   │                  relay/gather, allekirjoituksen tarkistus, kielentarkistus
        │   └── tieto/         Tietopohja: keskustelusäännöt, oireohjeet, puhelutyypit
        ├── triage/            Sääntötaso + LLM-analyysi taustasäikeessä
        ├── muisti/            Edellisen puhelun oire (muisti.py) ja sen seuranta (seuranta.py)
        ├── hoitaja/           Jono (jono.py), tapaus (palvelu.py), potilaslista ja -sivu
        │                      (potilas.py), oirehistoria (historia.py), ajat (ajat.py), reitit
        ├── dev/               VAIN DEV (DEV_TILA=true): testimuutokset, testisoitot,
        │                      synteettiset henkilötiedot (henkilotiedot.py)
        ├── templates/         Jinja2-sivupohjat (jono, tapaus, potilaat, potilas, osat/)
        └── static/            CSS, JS (hoitaja, oirehistoria), logot, vendor/ (ECharts)
```

Gitignoressa (ei repossa): `.env`, `data/csv/`, `lokit/`, `puheavustaja/aanet/`,
`puheavustaja/yhteenvedot/`, `puheavustaja/knowledge/soittoprofiilit.json`.
