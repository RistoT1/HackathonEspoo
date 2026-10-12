"""Puhelun analyysi hoitajalle LLM:llä (Jere, puheavustaja.py: tee_analyysi).

Malli lukee puhelun litteroinnin ja sääntöjen löydökset ja palauttaa rakenteisen
yhteenvedon (yleisvointi, oireet, turvallisuushuolet, ehdotettu taso, perustelu).
Lopullinen taso = korkeampi säännön ja mallin tasosta: malli voi vain nostaa.
Jos malli epäonnistuu (kahdesti: JSON-skeema, sitten JSON-tila), hoitaja saa
sääntöpohjaisen varayhteenvedon, ei virhettä. Litterointia ei tallenneta.

Kutsu on synkroninen (httpx), koska se ajetaan taustasäikeessä puhelun jälkeen
(tyontekija.py): kesto kymmeniä sekunteja paikallisella mallilla, puhelu ei odota.
"""

import json
import logging
import re

import httpx

from app.mallit import Kiireellisyys, OireYhteenvedossa, Saantoloydokset, Yhteenveto
from app.puhelu.tietopohja import TASOT, korkeampi_taso

logger = logging.getLogger(__name__)

ANALYYSI_OHJE = """Analysoi alla oleva seurantapuhelu hoitajalle. Kirjoita suomeksi.
Kenttien arvot (lievä, kyllä, keltainen...) ovat aina täsmälleen kuten alla.
Käytä VAIN sitä, mitä henkilö itse sanoi. Älä arvaa äläkä diagnosoi. Kirjoita lyhyesti.
Oireiksi kirjataan vain tuntemukset ja vaivat. Turvallisuuteen liittyvät asiat (väkivalta,
huono kohtelu, itsetuhoiset ajatukset, liian suuri lääkemäärä, outoja aistimuksia,
toivottomuus) kirjataan kenttään turvallisuushuolet, ei oireisiin.
Lopussa on sääntöjen tunnistamat asiat: tarkista ne keskustelusta ja huomioi ne.
Palauta pelkkä JSON tässä muodossa:
{"yleisvointi": "lyhyt kuvaus henkilön omin sanoin kertomasta voinnista",
 "oireet": [{"oire": "...", "voimakkuus": "lievä|kohtalainen|voimakas|ei tiedossa",
   "alkoi_laakkeen_jalkeen": "kyllä|ei|ei tiedossa", "tilanne": "milloin tai missä tilanteissa",
   "varmuus": "selvä|epäselvä"}],
 "turvallisuushuolet": ["lyhyt kuvaus jokaisesta, henkilön sanoja lainaten"],
 "tunnetila": "miltä henkilö kuulosti, esim. huolestunut, alakuloinen, tyytyväinen",
 "annetut_ohjeet": ["avustajan antamat kotihoito-ohjeet lyhyesti"],
 "laakkeen_kaytto": {"otettu_ohjeen_mukaan": "kyllä|ei|ei tiedossa", "huomiot": "..."},
 "lopettamisen_syy": "jos henkilö halusi lopettaa, miksi; muuten tyhjä",
 "huolet": ["asiat, jotka hoitajan kannattaa huomioida"],
 "muuta_hoitajalle": "lyhyt lause tai tyhjä",
 "ehdotettu_taso": "vihreä|keltainen|punainen",
 "perustelu": "yksi lause, miksi tämä taso"}
Enintään 6 oiretta ja 5 asiaa kuhunkin listaan.
Taso: vihreä = ei huolta, keltainen = hoitajan hyvä soittaa tänään, punainen = heti."""

_LYHYT = {"type": "string", "maxLength": 300}
_LISTA = {"type": "array", "items": _LYHYT, "maxItems": 5}
_VALINTA_KYLLA_EI = {"type": "string", "enum": ["kyllä", "ei", "ei tiedossa"]}
# Strukturoitu tulos: malli voi tuottaa vain tämän muotoista JSONia. Pelkkä JSON-tila ei
# estä katkeamista eikä vääriä kenttiä (Jere, mitattu puhelussa).
ANALYYSI_SKEEMA = {
    "type": "object",
    "properties": {
        "yleisvointi": _LYHYT,
        "oireet": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "oire": _LYHYT,
                    "voimakkuus": {
                        "type": "string",
                        "enum": ["lievä", "kohtalainen", "voimakas", "ei tiedossa"],
                    },
                    "alkoi_laakkeen_jalkeen": _VALINTA_KYLLA_EI,
                    "tilanne": _LYHYT,
                    "varmuus": {"type": "string", "enum": ["selvä", "epäselvä"]},
                },
                "required": ["oire", "voimakkuus", "alkoi_laakkeen_jalkeen", "tilanne", "varmuus"],
            },
        },
        "turvallisuushuolet": _LISTA,
        "tunnetila": _LYHYT,
        "annetut_ohjeet": _LISTA,
        "laakkeen_kaytto": {
            "type": "object",
            "properties": {"otettu_ohjeen_mukaan": _VALINTA_KYLLA_EI, "huomiot": _LYHYT},
            "required": ["otettu_ohjeen_mukaan", "huomiot"],
        },
        "lopettamisen_syy": _LYHYT,
        "huolet": _LISTA,
        "muuta_hoitajalle": _LYHYT,
        "ehdotettu_taso": {"type": "string", "enum": list(TASOT)},
        "perustelu": _LYHYT,
    },
    "required": [
        "yleisvointi",
        "oireet",
        "turvallisuushuolet",
        "tunnetila",
        "annetut_ohjeet",
        "laakkeen_kaytto",
        "lopettamisen_syy",
        "huolet",
        "muuta_hoitajalle",
        "ehdotettu_taso",
        "perustelu",
    ],
}


class AnalyysiKelvoton(ValueError):
    pass


# --- Sietävä JSON-jäsennys (katkennut tai roskaa ympärillä) ---


def _sulje_rakenteet(runko: str) -> str | None:
    """Lisää puuttuvat sulkevat sulut. None, jos runko päättyy kesken merkkijonon."""
    pino: list[str] = []
    merkkijonossa = False
    ohita = False
    for merkki in runko:
        if merkkijonossa:
            if ohita:
                ohita = False
            elif merkki == "\\":
                ohita = True
            elif merkki == '"':
                merkkijonossa = False
            continue
        if merkki == '"':
            merkkijonossa = True
        elif merkki in "{[":
            pino.append("}" if merkki == "{" else "]")
        elif merkki in "}]":
            if not pino:
                return None
            pino.pop()
    if merkkijonossa:
        return None
    return runko.rstrip() + "".join(reversed(pino))


def jasenna_json(teksti: str) -> dict:
    """Korjaa yleisimmät virheet: ```json-aidat, pilkku ennen sulkua ja kesken katkennut
    JSON (pituusraja): katkaistaan viimeiseen ehjään kohtaan ja suljetaan sulut."""
    runko = re.sub(r"^```(?:json)?\s*|\s*```$", "", teksti.strip())
    alku = runko.find("{")
    if alku < 0:
        raise AnalyysiKelvoton("vastauksessa ei ole JSON-objektia")
    runko = runko[alku:]
    for ehdokas in (runko, re.sub(r",\s*([}\]])", r"\1", runko)):
        try:
            return json.loads(ehdokas)
        except json.JSONDecodeError:
            pass
    for loppu in range(len(runko), 0, -1):
        merkki = runko[loppu - 1]
        if merkki not in ',{["}]0123456789elsu':  # arvon tai rakenteen loppumerkit
            continue
        ehdokas = _sulje_rakenteet(runko[: loppu - 1] if merkki == "," else runko[:loppu])
        if ehdokas is None:
            continue
        try:
            return json.loads(ehdokas)
        except json.JSONDecodeError:
            continue
    raise AnalyysiKelvoton("JSONia ei saatu korjattua")


def _lista(arvo: object) -> list:
    if isinstance(arvo, str):
        return [arvo] if arvo.strip() else []
    return arvo if isinstance(arvo, list) else []


def _teksti(arvo: object) -> str:
    return arvo if isinstance(arvo, str) else ("" if arvo is None else str(arvo))


def yhteenveto_analyysista(analyysi: dict, saanto: Saantoloydokset) -> Yhteenveto:
    """Mallin JSON -> Yhteenveto. Tuntemattomat arvot turvalliseen suuntaan."""
    if not isinstance(analyysi, dict):
        raise AnalyysiKelvoton("analyysi ei ole JSON-objekti")
    ehdotus = analyysi.get("ehdotettu_taso")
    mallin_taso = ehdotus if ehdotus in TASOT else "keltainen"  # epävarma: hoitaja katsoo
    oireet = [
        OireYhteenvedossa(
            **{
                kentta: _teksti(oire.get(kentta))
                for kentta in OireYhteenvedossa.model_fields
                if oire.get(kentta) is not None
            }
        )
        for oire in _lista(analyysi.get("oireet"))
        if isinstance(oire, dict) and oire.get("oire")
    ]
    kaytto = analyysi.get("laakkeen_kaytto")
    kaytto = kaytto if isinstance(kaytto, dict) else {}
    return Yhteenveto(
        taso=Kiireellisyys(korkeampi_taso(saanto.taso.value, mallin_taso)),
        saanto=saanto,
        mallin_taso=Kiireellisyys(mallin_taso),
        analyysi_valmis=True,
        yleisvointi=_teksti(analyysi.get("yleisvointi")),
        oireet=oireet,
        turvallisuushuolet=[_teksti(x) for x in _lista(analyysi.get("turvallisuushuolet"))],
        tunnetila=_teksti(analyysi.get("tunnetila")),
        annetut_ohjeet=[_teksti(x) for x in _lista(analyysi.get("annetut_ohjeet"))],
        laakkeen_kaytto=_teksti(kaytto.get("otettu_ohjeen_mukaan")) or "ei tiedossa",
        laakkeen_huomiot=_teksti(kaytto.get("huomiot")),
        lopettamisen_syy=_teksti(analyysi.get("lopettamisen_syy")),
        huolet=[_teksti(x) for x in _lista(analyysi.get("huolet"))],
        muuta_hoitajalle=_teksti(analyysi.get("muuta_hoitajalle")),
        perustelu=_teksti(analyysi.get("perustelu")),
    )


def saantoyhteenveto(saanto: Saantoloydokset, analyysi_valmis: bool = False) -> Yhteenveto:
    """Pelkät säännöt: heti puhelun jälkeen ja varalla, jos malli epäonnistuu."""
    huolet = [*saanto.hatamerkit, *saanto.vakavat_huolet]
    if saanto.huolen_ilmaukset:
        huolet.append("henkilö ilmaisi huolta")
    return Yhteenveto(
        taso=saanto.taso,
        saanto=saanto,
        analyysi_valmis=analyysi_valmis,
        oireet=[OireYhteenvedossa(oire=oire) for oire in saanto.tunnistetut_oireet],
        turvallisuushuolet=[*saanto.hatamerkit, *saanto.vakavat_huolet],
        tunnetila="huolestunut" if saanto.huolen_ilmaukset else "",
        huolet=huolet,
        perustelu="; ".join(saanto.perusteet) or "Säännöt eivät löytäneet huolestuttavaa.",
    )


def varayhteenveto(saanto: Saantoloydokset, syy: str) -> Yhteenveto:
    """Malli epäonnistui: sääntöjen yhteenveto, vähintään keltainen (hoitaja tarkistaa)."""
    yhteenveto = saantoyhteenveto(saanto, analyysi_valmis=True)
    yhteenveto.taso = Kiireellisyys(korkeampi_taso(saanto.taso.value, "keltainen"))
    yhteenveto.varayhteenveto = True
    yhteenveto.muuta_hoitajalle = (
        "Automaattinen analyysi ei onnistunut, yhteenveto on tehty säännöillä. "
        "Soita ja tarkista tilanne."
    )
    logger.warning("Analyysi epäonnistui, varayhteenveto: %s", syy)
    return yhteenveto


def analyysin_viestit(litterointi: str, saanto: Saantoloydokset) -> list[dict[str, str]]:
    loydokset = saanto.model_dump(exclude={"taso", "perusteet"}, exclude_defaults=True)
    saannoista = json.dumps(loydokset, ensure_ascii=False)
    kayttaja = (
        "PUHELUN TYYPPI: lääkeseuranta (lääkitykseen tullut muutos)\n\n"
        f"PUHELU:\n{litterointi}\n\nSÄÄNTÖJEN TUNNISTAMAT: {saannoista}"
    )
    return [{"role": "system", "content": ANALYYSI_OHJE}, {"role": "user", "content": kayttaja}]


class LlmAnalysoija:
    """OpenAI-yhteensopiva rajapinta (demossa Ollama, tuotannossa EU-hostattu malli)."""

    def __init__(
        self,
        base_url: str,
        malli: str,
        api_avain: str,
        aikaraja_s: float = 180,
        transport: httpx.BaseTransport | None = None,
        ajattelu: bool = False,
        pida_muistissa: bool = False,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        # Sama keep_alive kuin puhelussa: ilman sitä analyysi palauttaisi 5 min oletuksen.
        self._pida_muistissa = pida_muistissa
        self._malli = malli
        self._otsakkeet = {"Authorization": f"Bearer {api_avain}"}
        self._aikaraja_s = aikaraja_s
        self._transport = transport
        # Ajattelu parantaa tarkkuutta, mutta vie minuutteja: aikaraja ja tokenit sen mukaan.
        self._ajattelu = ajattelu
        if ajattelu:
            self._aikaraja_s = max(aikaraja_s, 900)

    def _kysy(self, viestit: list[dict[str, str]], muoto: dict | None) -> dict:
        runko: dict[str, object] = {
            "model": self._malli,
            "messages": viestit,
            "temperature": 0.1,
            # 600 katkaisi JSONin kesken (Jere); ajattelu kuluttaa samasta budjetista.
            "max_tokens": 6000 if self._ajattelu else 1800,
        }
        if not self._ajattelu:
            runko["reasoning_effort"] = "none"
        if self._pida_muistissa:
            runko["keep_alive"] = -1
        if muoto is not None:
            runko["response_format"] = muoto
        with httpx.Client(timeout=self._aikaraja_s, transport=self._transport) as client:
            vastaus = client.post(self._url, json=runko, headers=self._otsakkeet)
        vastaus.raise_for_status()
        return jasenna_json(vastaus.json()["choices"][0]["message"]["content"] or "")

    def analysoi(self, litterointi: str, saanto: Saantoloydokset) -> Yhteenveto:
        """Ei koskaan pelkkää virhettä: lopulta sääntöpohjainen varayhteenveto."""
        viestit = analyysin_viestit(litterointi, saanto)
        # Ollaman mlx-ajuri (Gemma 4 Macilla) ei tue pakotettua muotoa (501, mitattu 11.10.):
        # viimeisenä ilman sitä, jolloin ohje pyytää JSONin ja jasenna_json korjaa sen.
        muodot: tuple[dict | None, ...] = (
            {"type": "json_schema", "json_schema": {"name": "analyysi", "schema": ANALYYSI_SKEEMA}},
            {"type": "json_object"},
            None,
        )
        viimeisin: Exception | None = None
        for muoto in muodot:
            try:
                return yhteenveto_analyysista(self._kysy(viestit, muoto), saanto)
            except (httpx.HTTPError, AnalyysiKelvoton, KeyError, ValueError) as virhe:
                viimeisin = virhe
                tapa = muoto["type"] if muoto else "vapaa"
                logger.warning("Analyysiyritys epäonnistui (%s): %s", tapa, virhe)
        return varayhteenveto(saanto, str(viimeisin))
