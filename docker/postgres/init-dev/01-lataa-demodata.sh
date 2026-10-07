#!/usr/bin/env bash
# VAIN DEV. Lataa /demodata/*.csv (synteettinen data) skeemaan "demo" katseltavaksi pgAdminissa.
#
# Kenttanimiriippumaton: taulu luodaan CSV:n otsakerivista, kaikki sarakkeet TEXT.
# Toimii siis seka Synthealle etta Veil.AI:n CSV:lle ilman muutoksia.
# Tama on raakadataa katseluun; sovellus lukee datan edelleen data/adapteri.py:n kautta.
set -euo pipefail

if ! compgen -G "/demodata/*.csv" > /dev/null; then
  echo "demodata: ei CSV-tiedostoja hakemistossa /demodata, ohitetaan"
  exit 0
fi

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE SCHEMA IF NOT EXISTS demo;"

for tiedosto in /demodata/*.csv; do
  taulu="$(basename "$tiedosto" .csv | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_\n' '_')"
  otsake="$(head -n 1 "$tiedosto" | tr -d '\r\357\273\277')"

  sarakkeet="$(echo "$otsake" | tr ',' '\n' \
    | tr -d '"' \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9_]/_/g; s/.*/"&" TEXT/' \
    | paste -sd, -)"

  echo "demodata: $tiedosto -> demo.$taulu"
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
DROP TABLE IF EXISTS demo."$taulu";
CREATE TABLE demo."$taulu" ($sarakkeet);
\copy demo."$taulu" FROM '$tiedosto' WITH (FORMAT csv, HEADER true)
SQL
done

echo "demodata: valmis"
