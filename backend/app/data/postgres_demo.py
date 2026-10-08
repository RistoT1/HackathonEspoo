"""Datalahde Postgresin demo-skeemasta (Synthea-CSV:t ladattuna, ks. docker/postgres/init-dev).

Oletukset datan muodosta (Synthea CSV, tarkistettu 7.10.):
- Kaikki sarakkeet ovat TEXT; puuttuva arvo on NULL (COPY csv), varmuuden vuoksi myos ''.
- demo.patients: id, birthdate (YYYY-MM-DD), deathdate (tyhja = elossa), gender (M/F).
- demo.medications: start, stop (ISO 8601 + 'Z', tyhja stop = kaytto jatkuu), patient, code,
  description (annos koodattu nimeen, esim. "lisinopril 10 MG Oral Tablet"), reasondescription.
- Pitkaaikaislaake uusitaan uutena rivina samalla koodilla (ks. muutokset/laakkeet.py).
- ATC-koodia ei ole Synthean CSV:ssa: se haetaan RxNorm-koodilla tiedostosta
  synthea_atc.json (tuotettu skriptilla scripts/hae_synthea_atc.py). Tuotannon datassa
  (Kanta) ATC tulee valmisteen mukana, eika vastaavuustaulua tarvita.
Kenttanimet ovat VAIN tassa tiedostossa.
"""

import json
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, bindparam, text

from app.data.adapteri import DataEiSaatavilla
from app.mallit import Laakejakso, Vanhus

_VANHUS_SQL = "SELECT id, birthdate, deathdate, gender FROM demo.patients"
_LAAKE_SQL = (
    "SELECT patient, code, description, start, stop, reasondescription FROM demo.medications"
)


# Dev-testimuutoksilla lisatyt rivit tunnistetaan tasta (payer-sarake).
TESTIMERKKI = "DEV-TESTI"


def _iso(aika: datetime) -> str:
    """Samaan muotoon kuin data: 2020-04-28T18:58:37Z (vertailu merkkijonoina toimii)."""
    return aika.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paiva(arvo: str | None) -> date | None:
    if not arvo:
        return None
    return date.fromisoformat(arvo)


def _aika(arvo: str | None) -> datetime | None:
    if not arvo:
        return None
    return datetime.fromisoformat(arvo)


def _vanhus(rivi) -> Vanhus:
    return Vanhus(
        vanhus_id=rivi.id,
        syntymapaiva=_paiva(rivi.birthdate),
        kuolinpaiva=_paiva(rivi.deathdate),
        sukupuoli=rivi.gender,
    )


# Postgresin parametriraja on 65535; Veil.AI-datassa on 50 000 profiilia. Haetaan erissa.
_HAKUERA = 1000

_ATC_TIEDOSTO = Path(__file__).resolve().parent / "synthea_atc.json"


@lru_cache
def _atc_vastaavuus() -> dict[str, str | None]:
    return json.loads(_ATC_TIEDOSTO.read_text(encoding="utf-8"))["atc"]


def _laakejakso(rivi) -> Laakejakso:
    return Laakejakso(
        koodi=rivi.code,
        nimi=rivi.description,
        alkoi=_aika(rivi.start),
        paattyi=_aika(rivi.stop),
        syy=rivi.reasondescription or None,
        atc=_atc_vastaavuus().get(rivi.code),
    )


class PostgresDemoLahde:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def uusin_paiva(self) -> datetime:
        with self._engine.connect() as yhteys:
            uusin = yhteys.execute(text("SELECT max(start) FROM demo.medications")).scalar_one()
        if not uusin:
            raise DataEiSaatavilla(
                "Lääkedataa ei löydy (demo.medications on tyhjä). Lataa CSV:t ja nollaa kanta."
            )
        return _aika(uusin)

    def hae_vanhukset(self) -> list[Vanhus]:
        with self._engine.connect() as yhteys:
            rivit = yhteys.execute(text(_VANHUS_SQL)).all()
        return [_vanhus(rivi) for rivi in rivit]

    def hae_vanhus(self, vanhus_id: str) -> Vanhus | None:
        kysely = text(f"{_VANHUS_SQL} WHERE id = :id")
        with self._engine.connect() as yhteys:
            rivi = yhteys.execute(kysely, {"id": vanhus_id}).first()
        if rivi is None:
            return None
        return _vanhus(rivi)

    def hae_laakejaksot(self, vanhus_id: str) -> list[Laakejakso]:
        return self.hae_laakejaksot_monelle([vanhus_id]).get(vanhus_id, [])

    def hae_laakejaksot_monelle(self, vanhus_idt: list[str]) -> dict[str, list[Laakejakso]]:
        if not vanhus_idt:
            return {}
        kysely = text(f"{_LAAKE_SQL} WHERE patient IN :idt").bindparams(
            bindparam("idt", expanding=True)
        )
        jaksot: dict[str, list[Laakejakso]] = {vanhus_id: [] for vanhus_id in vanhus_idt}
        with self._engine.connect() as yhteys:
            for alku in range(0, len(vanhus_idt), _HAKUERA):
                era = vanhus_idt[alku : alku + _HAKUERA]
                for rivi in yhteys.execute(kysely, {"idt": era}):
                    jaksot[rivi.patient].append(_laakejakso(rivi))
        return jaksot

    # --- VAIN DEV: testimuutokset (Testikirjoittaja) ---
    # Lisatyt rivit merkitaan payer-kenttaan, lopetettujen alkuperainen stop talletetaan
    # dev-skeemaan, joten poista_testimuutokset() palauttaa demodatan ennalleen.

    def _varmista_dev_taulu(self, yhteys) -> None:
        yhteys.execute(text("CREATE SCHEMA IF NOT EXISTS dev"))
        yhteys.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dev.lopetetut_alkuperaiset "
                "(patient TEXT, code TEXT, start TEXT, stop TEXT)"
            )
        )

    def lisaa_laake(self, vanhus_id: str, koodi: str, nimi: str, alkoi: datetime) -> None:
        kysely = text(
            "INSERT INTO demo.medications (start, stop, patient, payer, code, description) "
            "VALUES (:start, NULL, :patient, :merkki, :code, :description)"
        )
        arvot = {
            "start": _iso(alkoi),
            "patient": vanhus_id,
            "merkki": TESTIMERKKI,
            "code": koodi,
            "description": nimi,
        }
        with self._engine.begin() as yhteys:
            yhteys.execute(kysely, arvot)

    def lopeta_laake(
        self, vanhus_id: str, koodi: str, paattyi: datetime, viitepaiva: datetime
    ) -> bool:
        viite = _iso(viitepaiva)
        hae = text(
            "SELECT ctid, start, stop FROM demo.medications "
            "WHERE patient = :patient AND code = :code AND start <= :viite "
            "AND (stop IS NULL OR stop = '' OR stop > :viite) ORDER BY start DESC LIMIT 1"
        )
        with self._engine.begin() as yhteys:
            arvot = {"patient": vanhus_id, "code": koodi, "viite": viite}
            rivi = yhteys.execute(hae, arvot).first()
            if rivi is None:
                return False

            # Lopetus ei voi olla ennen aloitusta; silloin lopetetaan viitepaivana.
            stop = _iso(paattyi)
            if stop <= rivi.start:
                stop = viite

            self._varmista_dev_taulu(yhteys)
            # Talletetaan vain ensimmainen alkuperainen stop, jos samaa rivia muutetaan uudelleen.
            yhteys.execute(
                text(
                    "INSERT INTO dev.lopetetut_alkuperaiset (patient, code, start, stop) "
                    "SELECT :patient, :code, :start, :stop WHERE NOT EXISTS ("
                    "SELECT 1 FROM dev.lopetetut_alkuperaiset "
                    "WHERE patient = :patient AND code = :code AND start = :start)"
                ),
                {"patient": vanhus_id, "code": koodi, "start": rivi.start, "stop": rivi.stop},
            )
            yhteys.execute(
                text("UPDATE demo.medications SET stop = :stop WHERE ctid = CAST(:ctid AS tid)"),
                {"stop": stop, "ctid": rivi.ctid},
            )
        return True

    def poista_testimuutokset(self) -> tuple[int, int]:
        palauta = text(
            "UPDATE demo.medications m SET stop = a.stop FROM dev.lopetetut_alkuperaiset a "
            "WHERE m.patient = a.patient AND m.code = a.code AND m.start = a.start "
            "AND m.payer IS DISTINCT FROM :merkki"
        )
        poista = text("DELETE FROM demo.medications WHERE payer = :merkki")
        with self._engine.begin() as yhteys:
            self._varmista_dev_taulu(yhteys)
            palautettuja = yhteys.execute(palauta, {"merkki": TESTIMERKKI}).rowcount
            yhteys.execute(text("DELETE FROM dev.lopetetut_alkuperaiset"))
            poistettuja = yhteys.execute(poista, {"merkki": TESTIMERKKI}).rowcount
        return palautettuja, poistettuja
