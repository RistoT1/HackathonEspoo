#!/usr/bin/env python3
"""
Puheavustaja: mikrofoni -> faster-whisper (STT, suomi) -> Ollama gemma4:e4b -> Piper (TTS, suomi) -> kaiutin

Soittaa (simuloidusti) kuulumispuhelun henkilölle, jolle on aloitettu uusi lääke.
Aloittaa kysymällä, miten menee, ja etenee henkilön vastausten mukaan: jatkokysymykset,
kotihoito-ohjeet oireisiin (knowledge/oireohjeet.json) ja välillä lääkkeen haittavaikutus-
kysymykset (knowledge/soittoprofiilit.json, rakennetaan: python rakenna_tietopohja.py).
Hätämerkit (rintakipu, hengenahdistus, halvausoireet...) tunnistetaan säännöillä
jokaisesta puheenvuorosta ennen mallia -> ohjataan heti numeroon 112.
Lopetuspyyntö: 1. kerralla kysytään syy, 2. kerralla lopetetaan (ellei puhelussa ole huolta),
3. kerralla lopetetaan aina. Puhelusta tallennetaan aina analyysi JSON:na (tila/saanto/analyysi).

Asennus (tai aja vain: python autorun.py, joka hoitaa nämä):
    pip install faster-whisper sounddevice numpy requests piper-tts
    ollama pull gemma4:e4b
    python -c "import aani; aani.lataa_aani()"   # suomenkielinen ääni aanet/-kansioon

Käyttö:
    python rakenna_tietopohja.py               # kerran: data/csv -> knowledge/soittoprofiilit.json
    python puheavustaja.py --listaa            # näytä soittoprofiilit
    python puheavustaja.py                     # puhelu tuoreimman lääkealoituksen saaneelle
    python puheavustaja.py --potilas 1d604da9  # puhelu tietylle profiilille (tunnuksen alku)
    python puheavustaja.py --no-tts            # vastaukset vain tekstinä
    python puheavustaja.py --teksti            # kirjoita vastaukset, avustaja puhuu silti ääneen
    python puheavustaja.py --whisper medium    # puheentunnistusmalli (oletus auto)
    python puheavustaja.py --aani fi_FI-harri-low --puhenopeus 0.8
Lopetus: Ctrl+C tai sano esim. "lopetetaan nyt" (kahdesti).
"""

import argparse
import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import requests

import aani
import tietopohja

try:                                   # ääni-kirjastot vain puhetilassa (--teksti toimii ilman)
    import sounddevice as sd
except (ImportError, OSError):
    sd = None

# Windows ilman kehittäjätilaa ei tue Hugging Facen välimuistin symlinkkejä. Mallit
# tallentuvat silti normaalisti (vain enemmän levytilaa), joten varoitus piilotetaan.
# Asetettava ennen faster_whisperin tuontia (tehdään main()-funktiossa).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# ---------------------------------------------------------------- ASETUKSET
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma4:e4b"

# Puheentunnistus: "auto" = large-v3-turbo jos NVIDIA-GPU, muuten medium (CPU:lla
# large-v3-turbo on liian hidas puheluun). Kieli on aina suomi.
WHISPER_MODEL = "auto"         # auto / large-v3-turbo / medium / small
WHISPER_LANGUAGE = "fi"
WHISPER_BEAM = 5               # suurempi = tarkempi mutta hitaampi

RATE = 16000
BLOCK_SEC = 0.03
SILENCE_LIMIT = 1.4            # näin pitkä tauko päättää puheenvuoron (s). Ikäihmiset
                               # pitävät ajatustaukoja: liian lyhyt katkaisee lauseen.
MAX_UTTERANCE = 45.0           # pisin sallittu puheenvuoro (s)
MAX_TURNS = 10                 # montako viimeisintä kysymys–vastaus-paria muistetaan
MAX_PUHELU_VUOROT = 24         # puhelun maksimikesto vuoroina, sitten kohtelias lopetus
LOPETUSMERKKI = "LOPETUS"      # malli sanoo tämän, kun kysymykset on käyty
UUDELLEENMERKKI = "UUDELLEEN"  # malli aloittaa tällä, kun kysyy saman kysymyksen uudelleen
YHTEENVEDOT = Path(__file__).resolve().parent / "yhteenvedot"
TASOT = ["vihreä", "keltainen", "punainen"]


# Järjestelmäohje: pitää mallin roolissa ja aiheessa. Muokkaa tarpeesi mukaan.
SYSTEM_PROMPT = """Olet hyvinvointialueen hoitotiimin suomenkielinen tekoälyavustaja, joka soittaa
seurantapuhelun. Olet lämmin, kärsivällinen ja puhut selkokieltä. Olet tekoäly, et ihminen,
etkä koskaan väitä muuta.

Puhut ikäihmisen kanssa puhelimessa. Puhu niin, että se on helppo kuulla ja ymmärtää:
- Lyhyet lauseet, alle kymmenen sanaa. Yksi asia kerrallaan.
- Tavalliset arkisanat. Ei vierasperäisiä sanoja eikä lääketieteen termejä.
- Kysy vain yksi asia kerrallaan, ja laita kysymys vastauksen loppuun.
- Kun annat ohjeen, sano se rauhallisesti ja selkeästi, enintään kaksi ohjetta kerralla.
- Ole ystävällinen ja kiireetön. Kiitä vastauksista ja myötäelä lyhyesti.
- Puhuttele sinuttelemalla.

Säännöt:
- Vastaa AINA suomeksi: yleensä 1–3 lyhyttä lausetta, ohjeita antaessasi enintään 4.
- Vastauksesi luetaan ääneen. Älä käytä markdownia, luettelomerkkejä,
  otsikoita, emojeja, taulukoita tai koodia. Kirjoita numerot ja lyhenteet
  niin kuin ne sanotaan ääneen.
- Käyttäjän teksti tulee puheentunnistuksesta ja voi sisältää virheitä.
  Päättele tarkoitus asiayhteydestä. Jos et ymmärrä, kysy lyhyesti tarkennusta.
- Pysy keskustelun aiheessa ja muista aiemmin sanottu. Älä aloita uusia
  aiheita omatoimisesti.
- Jos et tiedä vastausta, sano se suoraan äläkä keksi tietoja.
- Älä toista kysymystä äläkä aloita vastausta täytesanoilla.
- Et tee diagnooseja etkä anna lääkeneuvoja (annokset, lääkkeen lopetus tai aloitus).
  Saat antaa vain tietopohjan kotihoito-ohjeita. Muuhun neuvoa kysyttäessä sano:
  "Välitän tämän hoitajalle."
- Jos henkilö mainitsee rintakivun, hengenahdistuksen tai halvausoireita, kehota
  soittamaan heti 112.
"""


# ---------------------------------------------------------------- PUHEEN TALLENNUS
def calibrate_noise(seconds=1.0):
    """Mittaa taustamelu ja laske siitä puheen kynnysarvo."""
    print("Kalibroidaan taustamelua, ole hiljaa hetki...")
    audio = sd.rec(int(RATE * seconds), samplerate=RATE, channels=1, dtype="float32")
    sd.wait()
    noise = float(np.sqrt(np.mean(audio ** 2)))
    threshold = max(noise * 3.0, 0.008)
    print(f"Kynnys: {threshold:.4f}")
    return threshold


def record_utterance(threshold, mute_event):
    """Tallentaa yhden puheenvuoron. Palauttaa float32-numpy-taulukon."""
    block = int(RATE * BLOCK_SEC)
    pre_roll = []              # pieni puskuri, ettei sanan alku leikkaudu
    frames, silent, started = [], 0.0, False
    with sd.InputStream(samplerate=RATE, channels=1, blocksize=block, dtype="float32") as s:
        while True:
            data, _ = s.read(block)
            if mute_event.is_set():          # AI puhuu: ei kuunnella omaa ääntä
                pre_roll.clear()
                continue
            loud = np.sqrt(np.mean(data ** 2)) > threshold
            if not started:
                pre_roll.append(data.copy())
                pre_roll = pre_roll[-10:]
                if loud:
                    started = True
                    frames.extend(pre_roll)
                continue
            frames.append(data.copy())
            silent = 0.0 if loud else silent + BLOCK_SEC
            if silent > SILENCE_LIMIT or len(frames) * BLOCK_SEC > MAX_UTTERANCE:
                return np.concatenate(frames).flatten()


# ---------------------------------------------------------------- PUHEENTUNNISTUS
def valitse_whisper(pyynto):
    """Palauttaa (malli, laite, laskentatarkkuus). auto: GPU -> large-v3-turbo, CPU -> medium."""
    try:
        import ctranslate2
        gpu = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        gpu = False
    malli = pyynto if pyynto != "auto" else ("large-v3-turbo" if gpu else "medium")
    if gpu:
        return malli, "cuda", "float16"
    return malli, "cpu", "int8"


def litteroi(stt, audio, history, sanasto):
    """Suomenkielinen tunnistus. Avustajan viimeisin kysymys annetaan Whisperille
    kontekstiksi, ja oire- ja lääkesanasto vihjesanoiksi: vastauksen sanat tunnistuvat
    paremmin, kun malli tietää mistä puhutaan."""
    kysymys = tietopohja.viimeisin_kysymys(history)
    segments, _ = stt.transcribe(
        audio,
        language=WHISPER_LANGUAGE,
        beam_size=WHISPER_BEAM,
        vad_filter=True,
        initial_prompt=kysymys or None,
        hotwords=sanasto or None,
        condition_on_previous_text=False,   # vähentää Whisperin "keksimiä" lauseita
    )
    segments = list(segments)
    text = " ".join(s.text for s in segments).strip()
    if not segments:
        return text, None
    laatu = tietopohja.Litterointilaatu(
        min(s.avg_logprob for s in segments),
        max(s.no_speech_prob for s in segments),
    )
    return text, laatu


# ---------------------------------------------------------------- OLLAMA
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def clean_for_speech(text):
    """Poistaa markdown-jäänteet, jos malli niitä silti tuottaa."""
    text = re.sub(UUDELLEENMERKKI + r"[:,.]?\s*", "", text)
    text = text.replace(LOPETUSMERKKI, "")
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.MULTILINE)
    return text.strip()


def ask_ollama(history, speaker=None):
    """Lähettää keskustelun Ollamalle, striimaa vastauksen ja puhuu sen lause kerrallaan."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": history,
        "stream": True,
        "think": False,              # ei thinking-tilaa -> nopea vastaus
        "keep_alive": "30m",         # pidä malli muistissa
        "options": {
            "temperature": 0.6,
            "num_ctx": 4096,
            "num_predict": 200,      # lyhyet vastaukset
        },
    }
    full, buffer = "", ""
    with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=120) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            piece = chunk.get("message", {}).get("content", "")
            if piece:
                print(piece, end="", flush=True)
                full += piece
                buffer += piece
                # Lähetä valmiit lauseet puheeksi heti
                parts = SENTENCE_END.split(buffer)
                for sentence in parts[:-1]:
                    s = clean_for_speech(sentence)
                    if LOPETUSMERKKI in s:
                        continue
                    if s and speaker:
                        speaker.say(s)
                buffer = parts[-1]
            if chunk.get("done"):
                break
    rest = clean_for_speech(buffer)
    if rest and LOPETUSMERKKI not in rest and speaker:
        speaker.say(rest)
    print()
    return full.strip()


def warm_up():
    """Lataa mallin muistiin etukäteen, jotta ensimmäinen vastaus on nopea."""
    try:
        requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "messages": [], "keep_alive": "30m"},
            timeout=120,
        )
    except requests.RequestException as e:
        sys.exit(f"Ollamaan ei saada yhteyttä ({OLLAMA_URL}): {e}")


def trim_history(history):
    """Säilyttää järjestelmäohjeen ja MAX_TURNS viimeisintä vuoroparia."""
    system, rest = history[:1], history[1:]
    return system + rest[-MAX_TURNS * 2:]


# ---------------------------------------------------------------- PUHELUN ANALYYSI
ANALYYSI_OHJE = """Analysoi alla oleva seurantapuhelu hoitajalle.
Käytä VAIN sitä, mitä henkilö itse sanoi. Älä arvaa äläkä diagnosoi.
Palauta pelkkä JSON tässä muodossa:
{"yleisvointi": "lyhyt kuvaus henkilön omin sanoin kertomasta voinnista",
 "oireet": [{"oire": "...", "voimakkuus": "lievä|kohtalainen|voimakas|ei tiedossa",
   "alkoi_laakkeen_jalkeen": "kyllä|ei|ei tiedossa", "tilanne": "milloin tai missä tilanteissa",
   "varmuus": "selvä|epäselvä"}],
 "annetut_ohjeet": ["avustajan antamat kotihoito-ohjeet lyhyesti"],
 "laakkeen_kaytto": {"otettu_ohjeen_mukaan": "kyllä|ei|ei tiedossa", "huomiot": "..."},
 "lopettamisen_syy": "jos henkilö halusi lopettaa, miksi; muuten tyhjä",
 "huolet": ["asiat, jotka hoitajan kannattaa huomioida"],
 "muuta_hoitajalle": "lyhyt lause tai tyhjä",
 "ehdotettu_taso": "vihreä|keltainen|punainen",
 "perustelu": "yksi lause, miksi tämä taso"}
Taso: vihreä = ei huolta, keltainen = hoitajan hyvä soittaa tänään, punainen = heti."""


def tee_analyysi(history):
    """LLM analysoi puhelun; palauttaa dictin tai virhetiedon. Litterointia ei tallenneta."""
    keskustelu = "\n".join(
        f"{'Avustaja' if m['role'] == 'assistant' else 'Henkilö'}: {m['content']}"
        for m in history if m["role"] != "system"
    )
    keskustelu = re.sub(r"\n?\((Ohje|Tietopohja):[^)]*\)", "", keskustelu)
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": ANALYYSI_OHJE},
                    {"role": "user", "content": keskustelu},
                ],
                "stream": False,
                "think": False,
                "format": "json",
                "options": {"temperature": 0.1, "num_predict": 600},
            },
            timeout=120,
        )
        r.raise_for_status()
        return json.loads(r.json()["message"]["content"])
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[Analyysi epäonnistui: {e}]")
        return {"virhe": f"analyysi epäonnistui: {e}"}


def tallenna_analyysi(profiili, havainnot, analyysi, tila):
    """Tallentaa puhelun JSON:na: tila (mitä tapahtui), saanto (sääntöjen havainnot),
    analyysi (mallin tulkinta). Datan minimointi: ei litterointia eikä nimeä."""
    epaselvat = tila["selkeys"]["sisalto_epaselva"]
    saantotaso = tietopohja.kiireellisyys(havainnot, epaselvat)
    ehdotus = analyysi.get("ehdotettu_taso")
    llm_taso = ehdotus if ehdotus in TASOT else "vihreä"
    # Sääntö on alaraja: lopullinen taso = korkeampi kahdesta (sääntö voi vain nostaa).
    lopullinen = max(saantotaso, llm_taso, key=TASOT.index)
    laake = profiili["uudet_laakkeet"][0]

    def kerää(kentta):
        tulos = []
        for h in havainnot:
            for x in getattr(h, kentta):
                if x not in tulos:
                    tulos.append(x)
        return tulos

    tulos = {
        "tila": {
            "aika": datetime.now().isoformat(timespec="seconds"),
            "tunnus": profiili["tunnus"],
            "laake": laake["avain"],
            "lopputapa": tila["lopputapa"],
            "vuoroja": len(havainnot),
            "lopetuspyyntoja": tila["lopetuspyyntoja"],
            "kiireellisyys": lopullinen,
        },
        "saanto": {
            "taso": saantotaso,
            "hatamerkit": kerää("hata"),
            "hatamerkit_kieltomuodossa": kerää("hata_kielletty"),
            "huomiosanat": kerää("keltainen"),
            "tunnistetut_oireet": kerää("oireet"),
            "huolestuttavat_oireet": kerää("huolestuttavat"),
            "uudelleenkysymyksia": tila["selkeys"]["uudelleenkysymyksia"],
            "sisalloltaan_epaselvia_vastauksia": epaselvat,
            "ilman_vastausta_jaaneet": tila["selkeys"]["ohitetut"],
        },
        "analyysi": {**analyysi, "mallin_taso": llm_taso},
    }
    YHTEENVEDOT.mkdir(exist_ok=True)
    polku = YHTEENVEDOT / f"{datetime.now():%Y%m%d_%H%M%S}_{profiili['tunnus'][:8]}.json"
    with open(polku, "w", encoding="utf-8") as f:
        json.dump(tulos, f, ensure_ascii=False, indent=2)
    print(f"\nKiireellisyys: {lopullinen.upper()}  (sääntö {saantotaso}, malli {llm_taso})")
    print(f"Analyysi tallennettu: {polku}")
    return tulos


# ---------------------------------------------------------------- PÄÄSILMUKKA
def listaa_profiilit():
    for p in tietopohja.lataa_profiilit():
        laakkeet = ", ".join(f"{l['selkonimi']} ({l['aloitettu']})" for l in p["uudet_laakkeet"])
        print(f"{p['tunnus'][:8]}  {p['ikaryhma']:>6}  {laakkeet}")


def puhu(teksti, speaker, history=None):
    """Deterministinen repliikki: ei kulje mallin kautta."""
    print(f"AI: {teksti}")
    if history is not None:
        history.append({"role": "assistant", "content": teksti})
    if speaker:
        speaker.say(teksti)
        speaker.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tts", action="store_true", help="älä puhu vastauksia ääneen")
    ap.add_argument("--teksti", action="store_true", help="kirjoita vastaukset mikrofonin sijaan")
    ap.add_argument("--potilas", help="soittoprofiilin tunnus tai sen alku")
    ap.add_argument("--listaa", action="store_true", help="listaa soittoprofiilit ja lopeta")
    ap.add_argument("--whisper", default=WHISPER_MODEL,
                    help="puheentunnistusmalli: auto, large-v3-turbo, medium, small (oletus auto)")
    ap.add_argument("--aani", default=aani.OLETUSAANI,
                    help="Piper-ääni aanet/-kansiosta tai polku .onnx-tiedostoon")
    ap.add_argument("--puhenopeus", type=float, default=aani.OLETUSNOPEUS,
                    help="1.0 = normaali, pienempi = hitaampi (oletus %(default)s)")
    args = ap.parse_args()

    if args.listaa:
        listaa_profiilit()
        return
    try:
        profiili = tietopohja.hae_profiili(args.potilas)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))
    saannot = tietopohja.SAANNOT
    laake = profiili["uudet_laakkeet"][0]
    print(f"Profiili {profiili['tunnus'][:8]} ({profiili['ikaryhma']} v): "
          f"{laake['selkonimi']}, aloitettu {laake['aloitettu']}")

    if not args.teksti and sd is None:
        sys.exit("sounddevice puuttuu. Asenna se tai käytä --teksti.")

    mute = threading.Event()
    speaker = None
    if not args.no_tts:               # ääni myös tekstitilassa: demo kuuluu jo nyt
        if not aani.aani_loytyy(args.aani):
            print(f"Ääntä '{args.aani}' ei löydy. Lataa: python -c \"import aani; aani.lataa_aani()\" "
                  "tai aja python autorun.py. Vastaukset näytetään vain tekstinä.")
        else:
            try:
                speaker = aani.Puhuja(args.aani, mute, nopeus=args.puhenopeus)
            except (ImportError, OSError) as e:
                print(f"Puhesynteesi ei käytettävissä ({e}). Asenna: pip install piper-tts sounddevice")

    stt = None
    sanasto = None
    if not args.teksti:
        from faster_whisper import WhisperModel
        malli, laite, tarkkuus = valitse_whisper(args.whisper)
        print(f"Ladataan puheentunnistusta: {malli} ({laite}, {tarkkuus}). "
              "Ensimmäisellä kerralla malli ladataan verkosta.")
        stt = WhisperModel(malli, device=laite, compute_type=tarkkuus)
        sanasto = tietopohja.tunnistussanasto(profiili)
    print(f"Ladataan {OLLAMA_MODEL}...")
    warm_up()

    threshold = None if args.teksti else calibrate_noise()
    history = [{"role": "system",
                "content": SYSTEM_PROMPT + "\n" + tietopohja.puhelun_tehtava(profiili)}]
    havainnot = []
    uud = saannot["uudelleenkysymys"]
    lop = saannot["lopetuspyynto"]
    kerrat = 0                  # montako kertaa nykyinen kysymys on kysytty uudelleen
    kasitellyt_oireet = set()   # oireet, joihin tietopohjan ohje on jo annettu mallille
    tila = {
        "lopputapa": "keskeytetty",
        "lopetuspyyntoja": 0,
        "selkeys": {"uudelleenkysymyksia": 0, "sisalto_epaselva": 0, "ohitetut": []},
    }
    print("\nPuhelu alkaa (Ctrl+C lopettaa).\n")

    # AI Act art. 50: kerrotaan heti olevansa tekoäly. Avaus on kiinteä teksti
    # ja päättyy kysymykseen "miten sinulla on mennyt".
    puhu(saannot["avauslause"], speaker, history)

    try:
        for _vuoro in range(MAX_PUHELU_VUOROT):
            laatu = None
            if args.teksti:
                text = input("Sinä: ").strip()
            else:
                print("Kuuntelen...", end="\r", flush=True)
                audio = record_utterance(threshold, mute)
                text, laatu = litteroi(stt, audio, history, sanasto)
                if len(text) < 2:
                    continue
                print(f"Sinä: {text}")

            # 1) Sääntöpohjainen tarkistus ENNEN mallia: hätä, oireet, lopetuspyyntö.
            havainto = tietopohja.tarkista(text)
            havainnot.append(havainto)
            if havainto.hata:
                print(f"[HÄTÄMERKKI: {', '.join(havainto.hata)}]")
                history.append({"role": "user", "content": text})
                viesti = saannot["hataviestit"].get(havainto.hata[0], saannot["hataviesti"])
                puhu(viesti, speaker, history)
                tila["lopputapa"] = "hätä: ohjattu 112"
                break

            # 2) Lopetuspyyntö: 1. kysy syy, 2. lopeta (ellei huolta), 3. lopeta aina.
            if havainto.lopetuspyynto:
                tila["lopetuspyyntoja"] += 1
                n = tila["lopetuspyyntoja"]
                huolet = tietopohja.huolet(havainnot, tila["selkeys"]["sisalto_epaselva"])
                huolet_teksti = ", ".join(huolet)
                history.append({"role": "user", "content": text})
                print(f"[lopetuspyyntö {n}, huolet: {huolet_teksti or 'ei'}]")
                if n == 1:
                    puhu(lop["kysy_syy"], speaker, history)
                    kerrat = 0
                    continue
                if n == 2 and huolet:
                    puhu(lop["turvallisuuskysymys"].format(huolet=huolet_teksti), speaker, history)
                    continue
                viesti = lop["lopetus_huolestuttava"] if huolet else lop["lopetus"]
                puhu(viesti.format(huolet=huolet_teksti), speaker, history)
                tila["lopputapa"] = "henkilö lopetti" + (" (huolia kirjattu)" if huolet else "")
                break

            # 3) Saatiinko vastauksesta selvää? (säännöt, ei mallia)
            arvio = tietopohja.arvioi_vastaus(text, laatu)
            kysymys = tietopohja.viimeisin_kysymys(history)
            ohje = ""
            if arvio != "selvä":
                print(f"[{arvio}]")
                if kerrat < uud["max_per_kysymys"]:
                    kerrat += 1
                    tila["selkeys"]["uudelleenkysymyksia"] += 1
                    alku = uud["toistoviesti"] if arvio == "toistopyyntö" else uud["epaselva_viesti"]
                    puhu(f"{alku} {kysymys}", speaker)
                    continue
                # Raja täynnä: ei jäädä jumiin, malli siirtyy seuraavaan kysymykseen.
                tila["selkeys"]["ohitetut"].append(kysymys)
                puhu(uud["ohitusviesti"], speaker)
                text = "(ei selvää vastausta)"
                ohje = "\n(Ohje: tähän ei saatu selvää vastausta. Siirry seuraavaan kysymykseen.)"
                kerrat = 0
            elif kerrat >= uud["max_per_kysymys"]:
                ohje = "\n(Ohje: tätä on jo kysytty uudelleen. Älä kysy enää, siirry seuraavaan kysymykseen.)"

            # 4) Uusi oire -> tietopohjan jatkokysymykset ja kotihoito-ohjeet mallille.
            uudet = [o for o in havainto.oireet if o not in kasitellyt_oireet]
            kasitellyt_oireet.update(uudet)
            ohje += tietopohja.oireohje_mallille(uudet)

            # 5) Malli jatkaa keskustelua (tai kysyy uudelleen: UUDELLEEN).
            history.append({"role": "user", "content": text + ohje})
            print("AI: ", end="", flush=True)
            try:
                reply = ask_ollama(history, speaker)
            except requests.RequestException as e:
                print(f"\n[Ollama-virhe: {e}]")
                history.pop()
                continue
            if speaker:
                speaker.wait()
            if reply.lstrip().startswith(UUDELLEENMERKKI):
                kerrat += 1
                tila["selkeys"]["uudelleenkysymyksia"] += 1
                tila["selkeys"]["sisalto_epaselva"] += 1
                reply = re.sub(UUDELLEENMERKKI + r"[:,.]?\s*", "", reply, count=1)
            else:
                kerrat = 0
            if LOPETUSMERKKI in reply:
                ilman = reply.replace(LOPETUSMERKKI, "").strip()
                if ilman:
                    history.append({"role": "assistant", "content": ilman})
                puhu(saannot["lopetuslause"], speaker, history)
                tila["lopputapa"] = "keskustelu käyty"
                break
            history.append({"role": "assistant", "content": reply})
            history = trim_history(history)
        else:
            puhu(saannot["lopetuslause"], speaker, history)
            tila["lopputapa"] = "maksimikesto"
    except (KeyboardInterrupt, EOFError):
        pass

    # Analyysi tehdään AINA, myös hätä- ja lopetustilanteissa.
    print("\nAnalysoidaan puhelu hoitajalle...")
    analyysi = tee_analyysi(history) if len(history) > 2 else {"virhe": "ei vastauksia"}
    tallenna_analyysi(profiili, havainnot, analyysi, tila)
    print("Näkemiin!")


if __name__ == "__main__":
    main()
