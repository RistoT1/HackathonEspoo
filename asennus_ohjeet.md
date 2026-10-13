# Asennusohjeet

Paikallisen kehitys- ja demoympäristön pystytys. Ympäristö on tarkoitettu vain
kehitykseen ja synteettiselle datalle, ei tuotantoon.

## Pikakäynnistys Dockerilla

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

## Tietokannan alustus uudella koneella

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

Postgres luo tietokannan ja käyttäjän `.env`:n arvoilla ja ajaa sitten
`docker/postgres/init-dev/`-skriptit. Skripti luo skeeman `demo` ja jokaisesta CSV:stä
taulun (nimi tiedostosta, sarakkeet otsakeriviltä, kaikki `TEXT`).

**Alustus ajetaan vain kerran, tyhjään volyymiin.** Siksi:

- Uusi data tai muutettu init-skripti: vaihda CSV:t `data/csv/`:hen ja nollaa kanta:
  `docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v`, sitten `up`
  uudelleen.
- `POSTGRES_PASSWORD`:n vaihtaminen `.env`:ssä jälkikäteen ei muuta kannan salasanaa.
  Nollaa kanta (`down -v`) tai vaihda salasana SQL:llä
  (`ALTER USER hyvinvointi PASSWORD '...'`).
- `down -v` poistaa myös sovelluksen omat taulut (jono, triage) ja pgAdminin asetukset.

Vianetsintä: `docker compose logs postgres` näyttää init-skriptin virheet. Jos alustus
kaatui puolivälissä, kanta jää vajaaksi: korjaa syy ja aja `down -v` + `up`.

Tämä on turvallinen dev-tapa, koska portit ovat vain `127.0.0.1`:ssä, compose kaatuu
ilman `.env`:iä, pgAdmin vaatii kirjautumisen ja käytössä on vain synteettistä dataa.
Tuotannossa ei käytetä composea eikä pgAdminia: Postgres on hallinnoitu Sweden Centralissa
ja salaisuudet ovat Azure Key Vaultissa (ks. [SAANTELY.md](SAANTELY.md)).

`data/csv/` on `.gitignore`ssa (koko noin 80 Mt ja datan käyttöehdot), joten se jaetaan
tiimin kesken erikseen.

## Backend käyntiin

Edellytykset: kanta on pystyssä yllä olevilla ohjeilla ja `uv` on asennettu
(`brew install uv`).

```bash
# 1. Lisää .env:iin dev-asetus, jos se puuttuu
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

Tarkista:

- Terveystarkistus: <http://127.0.0.1:8000/terveys> vastaa `{"tila":"ok"}`.
- Swagger: <http://127.0.0.1:8000/docs> näyttää reitit.
- Soittojono: `GET /puhelutehtavat` tai käsin `POST /puhelutehtavat/tunnista`
  bodylla `{}`.

## Hoitajan käyttöliittymä

Käyttöliittymä tarjoillaan samasta FastAPI-backendistä, joten erillistä
frontend-palvelinta ei tarvitse käynnistää.

- Hoitajan näkymä: <http://127.0.0.1:8000/hoitaja/>
- Swagger-rajapinta: <http://127.0.0.1:8000/docs>

Jos sivu ei aukea, varmista ensin, että backendin käynnistyskomento on yhä käynnissä,
Postgres on käynnissä ja migraatiot on ajettu.

Kun uusi migraatio tulee `git pull` -komennolla, aja `uv run alembic upgrade head` tai
käynnistä backend uudelleen. Jos dev-kanta on sekaisin, nollaa kanta (`docker compose ...
down -v` + `up`) ja aja migraatiot uudelleen.

Valinnaiset `.env`-asetukset: `DEMO_PUHELINNUMERO` (Twilioon vahvistettu numero),
`TUNNISTUS_VALI_MINUUTIT` (oletus 15, 0 = pois), `LLM_BASE_URL` ja `LLM_MALLI`.
Kaikki asetukset on dokumentoitu tiedostossa [.env.example](.env.example).

## Paikallinen puhelumalli Ollamalla

Ajuri vie välimuisteineen noin 15 Gt. Sulje 16 Gt Macilla raskaat ohjelmat ja pienennä
tarvittaessa Docker Desktopin muistia.

```bash
ollama pull gemma4:12b
ollama create hyvinvointi-gemma12 -f ollama/Modelfile.gemma12
OLLAMA_KEEP_ALIVE=-1 ollama serve   # malli ja välimuisti pysyvät muistissa
```

Aseta `.env`:iin:

```dotenv
LLM_MALLI=hyvinvointi-gemma12
LLM_AJATTELU_POIS=true
LLM_VUORON_AIKARAJA_S=120
```

Palaa [README-tiedostoon](README.md).
