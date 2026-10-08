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
    python puheavustaja.py --kieli sv          # pakota kieli (oletus auto: fi, vaihtaa sv/en)
Lopetus: Ctrl+C tai sano esim. "lopetetaan nyt" (kahdesti).

Kielet: puhelu alkaa suomeksi ja kertoo, että voi puhua myös ruotsia tai englantia.
Kieli tunnistetaan jokaisesta puheenvuorosta (Whisper + sanatarkistus). Kiinteät repliikit
ovat knowledge/kielet.json:ssa. Kaikki kirjaukset hoitajalle tehdään AINA suomeksi.
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
KIELENVAIHDON_VARMUUS = 0.85   # Whisperin varmuus ennen kuin puhe litteroidaan toisella kielellä.
                               # Itse puhelun kielen vaihtaa vasta tietopohja.Kielenvalitsin.

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
seurantapuhelun. Olet lämmin, kärsivällinen ja aidosti kiinnostunut ihmisestä. Olet tekoäly,
et ihminen, etkä koskaan väitä muuta.

Puhut ikäihmisen kanssa puhelimessa. Puhu niin, että se on helppo kuulla ja ymmärtää:
- Lyhyet lauseet, alle kymmenen sanaa. Yksi asia kerrallaan.
- Tavalliset arkisanat. Ei vierasperäisiä sanoja eikä lääketieteen termejä.
- Kysy vain yksi asia kerrallaan, ja laita kysymys vastauksen loppuun.
- Kun annat ohjeen, sano se rauhallisesti ja selkeästi, enintään kaksi ohjetta kerralla.
- Puhuttele sinuttelemalla.

IHMINEN ENSIN, KYSYMYKSET SITTEN:
- Kuuntele, mitä henkilö oikeasti sanoi, ja vastaa siihen ensin. Kysymyslista ei ole kiireellinen.
- Kun henkilö kertoo kivusta, huolesta, pelosta, surusta, yksinäisyydestä tai muusta
  vaikeasta, PYSÄHDY siihen. Nimeä tunne hänen omin sanoin ja osoita, että ymmärsit.
  Kysy siitä lisää lempeästi. ÄLÄ siirry seuraavaan kysymykseen samassa vastauksessa.
- Siirry eteenpäin vasta, kun hän on saanut kertoa asiansa. Voit kysyä:
  "Haluatko kertoa tästä vielä, vai jatketaanko?"
- Älä vähättele ("ei se mitään", "kyllä se siitä"), älä tuomitse äläkä neuvo liikaa.
- Kiitä kertomisesta, kun asia on henkilölle selvästi vaikea.
- Vaihtele sanojasi. Älä aloita joka vastausta samalla fraasilla.

Esimerkkejä:
Henkilö: "Olen vähän huolissani, kun tämä lääke tuntuu oudolta."
Hyvä: "Ymmärrän, että se huolettaa. Kerrotko, miltä se tuntuu?"
Huono: "Selvä. Onko sinulla ollut pahoinvointia?"
Henkilö: "Vaimo kuoli keväällä, ja on ollut aika yksinäistä."
Hyvä: "Olen todella pahoillani. Se on iso menetys. Onko sinulla ketään, jonka kanssa jutella?"
Huono: "Otan osaa. Oletko ottanut lääkkeen ohjeen mukaan?"
Henkilö: "Ihan hyvin menee."
Hyvä: "Mukava kuulla. Onko uusi lääke sopinut sinulle?"

Säännöt:
- Vastaa puhelun kielellä (oletus suomi; jos alla on PUHELUN KIELI, käytä sitä):
  yleensä 1–3 lyhyttä lausetta, ohjeita antaessasi enintään 4.
- Vastauksesi luetaan ääneen. Älä käytä markdownia, luettelomerkkejä,
  otsikoita, emojeja, taulukoita tai koodia. Kirjoita numerot ja lyhenteet
  niin kuin ne sanotaan ääneen.
- Käyttäjän teksti tulee puheentunnistuksesta ja voi sisältää virheitä.
  Päättele tarkoitus asiayhteydestä. Jos et ymmärrä, kysy lyhyesti tarkennusta.
- Muista aiemmin sanottu. Älä aloita uusia aiheita omatoimisesti.
- Jos et tiedä vastausta, sano se suoraan äläkä keksi tietoja.
- Älä koskaan keksi puhelinnumeroita. Saat mainita vain numerot 112 ja 116117.
- Et tee diagnooseja etkä anna lääkeneuvoja (annokset, lääkkeen lopetus tai aloitus).
  Saat antaa vain tietopohjan kotihoito-ohjeita. Muuhun neuvoa kysyttäessä sano:
  "Välitän tämän hoitajalle."
- Jos henkilö mainitsee rintakivun, hengenahdistuksen tai halvausoireita, kehota
  soittamaan heti 112.
- Viestin lopussa suluissa oleva "Ohje" tai "Tietopohja" on sinulle tarkoitettu
  piilotettu ohje. Noudata sitä, mutta älä lue sitä ääneen.
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
def _lisaa_cuda_kirjastot():
    """Windows: jos CUDA-kirjastot on asennettu pipillä (nvidia-cublas-cu12,
    nvidia-cudnn-cu12), lisätään niiden DLL-kansiot hakupolkuun."""
    if os.name != "nt":
        return
    try:
        import nvidia
    except ImportError:
        return
    for juuri in getattr(nvidia, "__path__", []):
        for bin_kansio in Path(juuri).glob("*/bin"):
            os.add_dll_directory(str(bin_kansio))
            os.environ["PATH"] = str(bin_kansio) + os.pathsep + os.environ.get("PATH", "")


def _gpu_loytyy():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def lataa_whisper(pyynto, laite_pyynto="auto"):
    """Lataa puheentunnistuksen. auto: GPU -> large-v3-turbo (float16), CPU -> medium (int8).

    GPU:n käyttö testataan heti lyhyellä hiljaisella näytteellä. Jos CUDA-kirjastot
    (cuBLAS/cuDNN) puuttuvat, virhe tulisi muuten vasta ensimmäisessä puheenvuorossa;
    nyt siirrytään automaattisesti prosessoriin.
    """
    from faster_whisper import WhisperModel

    _lisaa_cuda_kirjastot()
    kayta_gpu = laite_pyynto == "cuda" or (laite_pyynto == "auto" and _gpu_loytyy())
    if kayta_gpu:
        malli = pyynto if pyynto != "auto" else "large-v3-turbo"
        print(f"Ladataan puheentunnistusta: {malli} (näytönohjain). "
              "Ensimmäisellä kerralla malli ladataan verkosta.")
        try:
            stt = WhisperModel(malli, device="cuda", compute_type="float16")
            testi = np.zeros(RATE, dtype=np.float32)
            list(stt.transcribe(testi, language=WHISPER_LANGUAGE, beam_size=1)[0])
            return stt
        except Exception as e:
            print(f"[Näytönohjainta ei voitu käyttää: {e}]")
            print("[Siirrytään prosessoriin. GPU:n saa käyttöön asentamalla CUDA-kirjastot:\n"
                  "  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*]")
    malli = pyynto if pyynto != "auto" else "medium"
    print(f"Ladataan puheentunnistusta: {malli} (prosessori). "
          "Ensimmäisellä kerralla malli ladataan verkosta.")
    return WhisperModel(malli, device="cpu", compute_type="int8")


def _tunnista_puheen_kieli(stt, audio):
    """Whisperin kielentunnistus. Palauttaa (kieli, todennäköisyys)."""
    try:
        kieli, tod, _ = stt.detect_language(audio)          # faster-whisper >= 1.1
        return kieli, tod
    except (AttributeError, TypeError):
        _, info = stt.transcribe(audio, language=None, beam_size=1)   # vanhemmat versiot
        return info.language, info.language_probability


def litteroi(stt, audio, history, sanasto, kieli="fi", auto=True):
    """Puheentunnistus. auto: Whisper arvaa puheen kielen; jos se on hyvin varma toisesta
    tuetusta kielestä, litteroidaan sillä kielellä (muuten Whisper kääntäisi puheen).
    Puhelun kielen vaihdosta päättää Kielenvalitsin, ei tämä funktio.
    Avustajan viimeisin kysymys annetaan kontekstiksi ja suomeksi oire- ja lääkesanasto
    vihjesanoiksi. Palauttaa (teksti, laatu, whisperin_kieli, varmuus)."""
    puheen_kieli, varmuus = kieli, 0.0
    if auto:
        arvaus, varmuus = _tunnista_puheen_kieli(stt, audio)
        if arvaus in tietopohja.KIELET["tuetut"] and varmuus >= KIELENVAIHDON_VARMUUS:
            puheen_kieli = arvaus
        else:
            arvaus = None
    else:
        arvaus = None
    kysymys = tietopohja.viimeisin_kysymys(history)
    segments, _ = stt.transcribe(
        audio,
        language=puheen_kieli,
        beam_size=WHISPER_BEAM,
        vad_filter=True,
        initial_prompt=(kysymys or None) if puheen_kieli == kieli else None,
        hotwords=(sanasto or None) if puheen_kieli == "fi" else None,
        condition_on_previous_text=False,   # vähentää Whisperin "keksimiä" lauseita
    )
    segments = list(segments)
    text = " ".join(s.text for s in segments).strip()
    if not segments:
        return text, None, arvaus, varmuus
    laatu = tietopohja.Litterointilaatu(
        min(s.avg_logprob for s in segments),
        max(s.no_speech_prob for s in segments),
    )
    return text, laatu, arvaus, varmuus


# ---------------------------------------------------------------- OLLAMA
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


_PUHELINNUMERO = re.compile(r"\d[\d \-]{2,}\d")
SALLITUT_NUMEROT = tietopohja.kaikki_sallitut_numerot()
NUMERON_KORVAUS = "Välitän tämän hoitajalle."


def suodata_numerot(lause):
    """Malli keksi puhelussa numeron "992-200". Vääriin numeroihin ei saa ohjata:
    lause, jossa on muu kuin keskustelusaannot.json:n numero, korvataan."""
    for n in _PUHELINNUMERO.findall(lause):
        if re.sub(r"\D", "", n) not in SALLITUT_NUMEROT:
            print(f"\n[keksitty numero poistettu: {n}]")
            return NUMERON_KORVAUS
    return lause


def suodata_vastaus(teksti):
    """Sama suodatus koko vastaukselle (historiaan tallennettava versio)."""
    lauseet = SENTENCE_END.split(teksti)
    tulos = []
    for l in lauseet:
        l = suodata_numerot(l)
        if l == NUMERON_KORVAUS and NUMERON_KORVAUS in tulos:
            continue
        tulos.append(l)
    return " ".join(tulos)


def clean_for_speech(text):
    """Poistaa markdown-jäänteet, jos malli niitä silti tuottaa."""
    text = re.sub(UUDELLEENMERKKI + r"[:,.]?\s*", "", text)
    text = text.replace(LOPETUSMERKKI, "")
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\((Ohje|Tietopohja):[^)]*\)?", "", text)   # piilo-ohje ei saa kuulua
    return suodata_numerot(text.strip()) if text.strip() else ""


def ask_ollama(history, speaker=None, kieli="fi"):
    # Muilla kielillä pienempi lämpötila: malli pysyy paremmin kielessä ja aiheessa.
    """Lähettää keskustelun Ollamalle, striimaa vastauksen ja puhuu sen lause kerrallaan."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": history,
        "stream": True,
        "think": False,              # ei thinking-tilaa -> nopea vastaus
        "keep_alive": "30m",         # pidä malli muistissa
        "options": {
            "temperature": 0.6 if kieli == "fi" else 0.4,
            "num_ctx": 8192,         # järjestelmäohje + tietopohja + 10 vuoroparia
            "num_predict": 200,      # lyhyet vastaukset
        },
    }
    full, buffer = "", ""
    with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=120) as r:
        tarkista_vastaus(r)
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
                        speaker.say(s, kieli)
                buffer = parts[-1]
            if chunk.get("done"):
                break
    rest = clean_for_speech(buffer)
    if rest and LOPETUSMERKKI not in rest and speaker:
        speaker.say(rest, kieli)
    print()
    return full.strip()


class OllamaVirhe(requests.RequestException):
    """Ollaman palauttama virhe tarkalla syyllä (ei pelkkä '500 Internal Server Error')."""


def tarkista_vastaus(r):
    """Nostaa virheen Ollaman omalla virheviestillä, esim. muistin loppuminen."""
    if r.ok:
        return
    try:
        syy = r.json().get("error", r.text)
    except ValueError:
        syy = r.text
    raise OllamaVirhe(f"Ollama {r.status_code}: {syy.strip()}\n{selita_ollama_virhe(syy)}")


def selita_ollama_virhe(syy):
    """Tunnetuille Ollama-virheille selkokielinen korjausohje."""
    s = syy.lower()
    if "memory" in s or "muisti" in s or "out of memory" in s or "cuda error" in s:
        return ("-> Mallille ei riitä muisti. Kokeile pienempää mallia: muuta OLLAMA_MODEL "
                "= \"gemma4:e2b\" tai sulje muita ohjelmia.")
    if "not found" in s or "pull" in s:
        mallit = ollaman_mallit()
        if mallit is None:
            return f"-> Mallia ei löydy. Aja: ollama pull {OLLAMA_MODEL}"
        if not mallit:
            return ("-> Tämä Ollama ei näe yhtään mallia. Jos latasit mallin, se meni todennäköisesti "
                    "toiseen Ollamaan (esim. Ollama-sovellus vs. 'ollama serve', Docker tai WSL). "
                    f"Aja samassa ympäristössä: ollama pull {OLLAMA_MODEL}")
        lahin = [m for m in mallit if OLLAMA_MODEL.split(":")[0] in m or OLLAMA_MODEL.split(":")[-1] in m]
        vihje = f"\n   Kokeile: python puheavustaja.py --malli {lahin[0]}" if lahin else ""
        return ("-> Tämä Ollama näkee vain nämä mallit (nimen pitää täsmätä täsmälleen):\n   "
                + "\n   ".join(mallit) + vihje)
    if "unknown model architecture" in s or "unsupported" in s or "not supported" in s:
        return "-> Ollama on liian vanha tälle mallille. Päivitä Ollama: https://ollama.com/download"
    if "terminated" in s or "exit status" in s:
        return ("-> Mallin käynnistys kaatui (usein muisti tai vanha Ollama). "
                f"Kokeile terminaalissa: ollama run {OLLAMA_MODEL} \"hei\"")
    return f"-> Kokeile terminaalissa: ollama run {OLLAMA_MODEL} \"hei\" nähdäksesi tarkemman virheen."


def ollaman_mallit():
    """Mallit, jotka juuri tämä Ollama-palvelin (OLLAMA_URL) näkee. None jos ei vastaa."""
    try:
        r = requests.get(OLLAMA_URL.replace("/api/chat", "/api/tags"), timeout=5)
        return [m["name"] for m in r.json().get("models", [])]
    except (requests.RequestException, ValueError):
        return None


def warm_up():
    """Lataa mallin muistiin etukäteen, jotta ensimmäinen vastaus on nopea.
    Pysäyttää ohjelman selkeällä viestillä, jos malli ei lataudu."""
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "messages": [], "keep_alive": "30m"},
            timeout=300,          # ensimmäinen lataus muistiin voi kestää
        )
        tarkista_vastaus(r)
    except OllamaVirhe as e:
        sys.exit(f"Mallin {OLLAMA_MODEL} lataus epäonnistui.\n{e}")
    except requests.RequestException as e:
        sys.exit(f"Ollamaan ei saada yhteyttä ({OLLAMA_URL}): {e}\n"
                 "-> Onko Ollama käynnissä? Käynnistä: ollama serve (tai aja python autorun.py)")


def trim_history(history):
    """Säilyttää järjestelmäohjeen ja MAX_TURNS viimeisintä vuoroparia."""
    system, rest = history[:1], history[1:]
    return system + rest[-MAX_TURNS * 2:]


# ---------------------------------------------------------------- PUHELUN ANALYYSI
ANALYYSI_OHJE = """Analysoi alla oleva seurantapuhelu hoitajalle.
Puhelu voi olla suomeksi, ruotsiksi tai englanniksi. Kirjoita analyysi AINA suomeksi:
käännä henkilön sanat ja lainaukset suomeksi. Kenttien arvot (lievä, kyllä, keltainen...)
ovat aina täsmälleen kuten alla.
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
# Ollaman strukturoitu tulos (format = JSON-skeema): malli voi tuottaa vain tämän
# muotoista JSONia. Pelkkä format="json" ei estä katkeamista eikä vääriä kenttiä.
ANALYYSI_SKEEMA = {
    "type": "object",
    "properties": {
        "yleisvointi": _LYHYT,
        "oireet": {
            "type": "array", "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "oire": _LYHYT,
                    "voimakkuus": {"type": "string", "enum": ["lievä", "kohtalainen", "voimakas", "ei tiedossa"]},
                    "alkoi_laakkeen_jalkeen": {"type": "string", "enum": ["kyllä", "ei", "ei tiedossa"]},
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
            "properties": {
                "otettu_ohjeen_mukaan": {"type": "string", "enum": ["kyllä", "ei", "ei tiedossa"]},
                "huomiot": _LYHYT,
            },
            "required": ["otettu_ohjeen_mukaan", "huomiot"],
        },
        "lopettamisen_syy": _LYHYT,
        "huolet": _LISTA,
        "muuta_hoitajalle": _LYHYT,
        "ehdotettu_taso": {"type": "string", "enum": TASOT},
        "perustelu": _LYHYT,
    },
    "required": ["yleisvointi", "oireet", "turvallisuushuolet", "tunnetila", "annetut_ohjeet",
                 "laakkeen_kaytto", "lopettamisen_syy", "huolet", "muuta_hoitajalle",
                 "ehdotettu_taso", "perustelu"],
}


def jasenna_json(teksti):
    """Jäsentää mallin JSONin sietävästi. Korjaa yleisimmät virheet:
    - teksti JSONin ympärillä (```json ... ```), ylimääräiset pilkut ennen sulkua
    - kesken katkennut JSON (num_predict loppui): katkaistaan viimeiseen ehjään
      kohtaan ja suljetaan avoimet merkkijonot ja sulut.
    Palauttaa dictin tai nostaa ValueErrorin."""
    t = teksti.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    alku = t.find("{")
    if alku < 0:
        raise ValueError("vastauksessa ei ole JSON-objektia")
    t = t[alku:]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    t = re.sub(r",\s*([}\]])", r"\1", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Katkennut JSON: etsitään lopusta alkaen viimeinen kohta, jossa edellinen arvo on
    # kokonainen, ja suljetaan avoimet sulut. Ensimmäinen jäsentyvä versio kelpaa.
    for i in range(len(t), 0, -1):
        c = t[i - 1]
        if c not in ',{["}]0123456789elsu':      # arvon tai rakenteen loppumerkit
            continue
        ehdokas = _sulje_rakenteet(t[:i - 1] if c == "," else t[:i])
        if ehdokas is None:
            continue
        try:
            return json.loads(ehdokas)
        except json.JSONDecodeError:
            continue
    raise ValueError("JSONia ei saatu korjattua")


def _sulje_rakenteet(runko):
    """Lisää puuttuvat sulkevat sulut. None, jos runko päättyy kesken merkkijonon."""
    pino, merkkijonossa, ohita = [], False, False
    for c in runko:
        if merkkijonossa:
            if ohita:
                ohita = False
            elif c == "\\":
                ohita = True
            elif c == '"':
                merkkijonossa = False
            continue
        if c == '"':
            merkkijonossa = True
        elif c in "{[":
            pino.append("}" if c == "{" else "]")
        elif c in "}]":
            if not pino:
                return None
            pino.pop()
    if merkkijonossa:
        return None
    return runko.rstrip() + "".join(reversed(pino))


def _tasoita(analyysi):
    """Varmistaa, että kentät ovat oikeaa tyyppiä (tallennus ja hoitajan näkymä eivät kaadu)."""
    if not isinstance(analyysi, dict):
        raise ValueError("analyysi ei ole JSON-objekti")
    for kentta in ("oireet", "turvallisuushuolet", "annetut_ohjeet", "huolet"):
        arvo = analyysi.get(kentta, [])
        if isinstance(arvo, str):
            arvo = [arvo] if arvo.strip() else []
        analyysi[kentta] = arvo if isinstance(arvo, list) else []
    analyysi["oireet"] = [o if isinstance(o, dict) else {"oire": str(o)} for o in analyysi["oireet"]]
    for kentta in ("yleisvointi", "tunnetila", "lopettamisen_syy", "muuta_hoitajalle", "perustelu"):
        if not isinstance(analyysi.get(kentta, ""), str):
            analyysi[kentta] = str(analyysi.get(kentta))
        analyysi.setdefault(kentta, "")
    if not isinstance(analyysi.get("laakkeen_kaytto"), dict):
        analyysi["laakkeen_kaytto"] = {"otettu_ohjeen_mukaan": "ei tiedossa", "huomiot": ""}
    if analyysi.get("ehdotettu_taso") not in TASOT:
        analyysi["ehdotettu_taso"] = "keltainen"     # epävarmassa tilanteessa hoitaja katsoo
    return analyysi


def _saantojen_yhteenveto(havainnot):
    """Sääntöjen löydökset analyysimallille ja varayhteenvetoon."""
    def kerää(kentta):
        tulos = []
        for h in havainnot:
            for x in getattr(h, kentta):
                if x not in tulos:
                    tulos.append(x)
        return tulos
    return {
        "hätämerkit": kerää("hata"),
        "vakavat huolet": [tietopohja.SELKONIMET.get(v, v) for v in kerää("vakavat")],
        "huolen ilmaukset": kerää("huoli"),
        "oireet": kerää("oireet"),
    }


def varayhteenveto(havainnot, syy):
    """Jos malli ei tuota kelvollista JSONia, hoitaja saa silti sääntöjen yhteenvedon."""
    s = _saantojen_yhteenveto(havainnot)
    huolet = [*s["hätämerkit"], *s["vakavat huolet"]]
    taso = tietopohja.kiireellisyys(havainnot)
    return {
        "yleisvointi": "",
        "oireet": [{"oire": o, "voimakkuus": "ei tiedossa", "alkoi_laakkeen_jalkeen": "ei tiedossa",
                    "tilanne": "", "varmuus": "epäselvä"} for o in s["oireet"]],
        "turvallisuushuolet": huolet,
        "tunnetila": "huolestunut" if s["huolen ilmaukset"] else "",
        "annetut_ohjeet": [],
        "laakkeen_kaytto": {"otettu_ohjeen_mukaan": "ei tiedossa", "huomiot": ""},
        "lopettamisen_syy": "",
        "huolet": huolet + (["henkilö ilmaisi huolta"] if s["huolen ilmaukset"] else []),
        "muuta_hoitajalle": "Mallin analyysi epäonnistui; yhteenveto on tehty säännöillä. Soita ja tarkista tilanne.",
        "ehdotettu_taso": taso if taso != "vihreä" else "keltainen",
        "perustelu": "Automaattinen analyysi ei onnistunut, joten hoitajan on hyvä tarkistaa puhelu.",
        "varayhteenveto": True,
        "virhe": f"analyysi epäonnistui: {syy}",
    }


def _kysy_analyysi(viestit, muoto):
    r = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "messages": viestit,
            "stream": False,
            "think": False,
            "format": muoto,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.1,
                "num_ctx": 8192,       # koko puhelu mahtuu; oletus 2048–4096 leikkasi ohjeen pois
                "num_predict": 1800,   # 600 katkaisi JSONin kesken -> "Expecting value"
            },
        },
        timeout=180,
    )
    tarkista_vastaus(r)
    data = r.json()
    sisalto = data["message"]["content"]
    if data.get("done_reason") == "length":
        print("[analyysi katkesi pituusrajaan, korjataan]")
    return _tasoita(jasenna_json(sisalto))


def tee_analyysi(history, havainnot=()):
    """LLM analysoi puhelun; palauttaa dictin. Ei koskaan pelkkää virhettä: jos malli
    epäonnistuu kahdesti, palautetaan sääntöpohjainen varayhteenveto.
    Litterointia ei tallenneta."""
    keskustelu = "\n".join(
        f"{'Avustaja' if m['role'] == 'assistant' else 'Henkilö'}: {m['content']}"
        for m in history if m["role"] != "system"
    )
    keskustelu = re.sub(r"\n?\((Ohje|Tietopohja):[^)]*\)", "", keskustelu)
    saannot = json.dumps(_saantojen_yhteenveto(havainnot), ensure_ascii=False)
    viestit = [
        {"role": "system", "content": ANALYYSI_OHJE},
        {"role": "user", "content": f"PUHELU:\n{keskustelu}\n\nSÄÄNTÖJEN TUNNISTAMAT: {saannot}"},
    ]
    viimeisin = None
    # 1) JSON-skeema (Ollama 0.5+), 2) tavallinen JSON-tila (vanhempi Ollama tai jos skeema ei toimi)
    for muoto in (ANALYYSI_SKEEMA, "json"):
        try:
            return _kysy_analyysi(viestit, muoto)
        except (requests.RequestException, ValueError, KeyError) as e:
            viimeisin = e
            print(f"[Analyysiyritys epäonnistui: {e}]")
    return varayhteenveto(havainnot, viimeisin)


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
            "kielet": [tietopohja.KIELET["nimet"][k] for k in tila.get("kielet", ["fi"])],
            "kiireellisyys": lopullinen,
            "analyysi_varayhteenveto": bool(analyysi.get("varayhteenveto")),
        },
        "saanto": {
            "taso": saantotaso,
            "hatamerkit": kerää("hata"),
            "hatamerkit_kieltomuodossa": kerää("hata_kielletty"),
            "vakavat_huolet": kerää("vakavat"),
            "huolen_ilmaukset": kerää("huoli"),
            "tukitila": tila.get("tukitila"),
            "kaikuharhoja": sum(h.kaikuharha for h in havainnot),
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


def puhu(teksti, speaker, history=None, kieli="fi"):
    """Deterministinen repliikki: ei kulje mallin kautta."""
    print(f"AI: {teksti}")
    if history is not None:
        history.append({"role": "assistant", "content": teksti})
    if speaker:
        speaker.say(teksti, kieli)
        speaker.wait()


def main():
    global OLLAMA_MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tts", action="store_true", help="älä puhu vastauksia ääneen")
    ap.add_argument("--teksti", action="store_true", help="kirjoita vastaukset mikrofonin sijaan")
    ap.add_argument("--potilas", help="soittoprofiilin tunnus tai sen alku")
    ap.add_argument("--listaa", action="store_true", help="listaa soittoprofiilit ja lopeta")
    ap.add_argument("--malli", default=None,
                    help=f"Ollama-mallin nimi täsmälleen kuten 'ollama list' näyttää (oletus {OLLAMA_MODEL})")
    ap.add_argument("--whisper", default=WHISPER_MODEL,
                    help="puheentunnistusmalli: auto, large-v3-turbo, medium, small (oletus auto)")
    ap.add_argument("--whisper-laite", default="auto", choices=["auto", "cpu", "cuda"],
                    help="puheentunnistuksen laite (oletus auto: näytönohjain jos toimii)")
    ap.add_argument("--aani", default=aani.OLETUSAANI,
                    help="Piper-ääni aanet/-kansiosta tai polku .onnx-tiedostoon")
    ap.add_argument("--kieli", default="auto", choices=["auto", "fi", "sv", "en"],
                    help="puhelun kieli; auto = alkaa suomeksi ja vaihtaa ruotsiin/englantiin tarvittaessa")
    ap.add_argument("--puhenopeus", type=float, default=aani.OLETUSNOPEUS,
                    help="1.0 = normaali, pienempi = hitaampi (oletus %(default)s)")
    args = ap.parse_args()
    if args.malli:
        OLLAMA_MODEL = args.malli

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
                kieliaanet = {k: v for k, v in tietopohja.KIELET["aanet"].items() if k != "fi"}
                speaker = aani.Puhuja(args.aani, mute, nopeus=args.puhenopeus, kieliaanet=kieliaanet)
            except (ImportError, OSError) as e:
                print(f"Puhesynteesi ei käytettävissä ({e}). Asenna: pip install piper-tts sounddevice")

    stt = None
    sanasto = None
    if not args.teksti:
        stt = lataa_whisper(args.whisper, args.whisper_laite)
        sanasto = tietopohja.tunnistussanasto(profiili)
    print(f"Ladataan {OLLAMA_MODEL}...")
    warm_up()

    threshold = None if args.teksti else calibrate_noise()
    auto_kieli = args.kieli == "auto"
    kieli = "fi" if auto_kieli else args.kieli
    kielet_puhelussa = [kieli]
    valitsin = tietopohja.Kielenvalitsin(kieli)
    kieli_lipsahti = False      # mallin edellinen vastaus oli väärällä kielellä

    def T(avain):
        """Kiinteä repliikki puhelun nykyisellä kielellä."""
        return tietopohja.teksti(avain, kieli)

    def jarjestelmaohje():
        """Järjestelmäohje: perusohje + kieliohje + tehtävä (tai tukitila)."""
        # Koko kehys samalla kielellä kuin keskustelu: muuten pieni malli lipsuu
        # suomeen tai unohtaa tehtävän, kun ohje on suomeksi ja puhe englanniksi.
        osat = [SYSTEM_PROMPT if kieli == "fi"
                else tietopohja.KIELET["mallin_ohjeet"][kieli]["jarjestelmaohje"]]
        osat.append(tietopohja.tukitilan_tehtava(tila["tukitila"], kieli) if tila["tukitila"]
                    else tietopohja.puhelun_tehtava(profiili, kieli))
        return "\n".join(osat)

    history = [{"role": "system", "content": ""}]
    havainnot = []
    uud = saannot["uudelleenkysymys"]
    huoli_saanto = saannot["huoli"]
    kerrat = 0                  # montako kertaa nykyinen kysymys on kysytty uudelleen
    kasitellyt_oireet = set()   # oireet, joihin tietopohjan ohje on jo annettu mallille
    kasitellyt_vakavat = set()  # vakavat huolet, joihin kiinteä tukirepliikki on jo sanottu
    huoli_jatko = 0             # montako vuoroa huolen käsittelyä vielä jatketaan
    odottaa_varmistusta = False  # avustaja kysyi suoraan itsetuhoisista ajatuksista
    varmistettu = False
    turvallisuus_kysytty = False
    tila = {
        "lopputapa": "keskeytetty",
        "lopetuspyyntoja": 0,
        "tukitila": None,
        "kielet": kielet_puhelussa,
        "selkeys": {"uudelleenkysymyksia": 0, "sisalto_epaselva": 0, "ohitetut": []},
    }
    history[0]["content"] = jarjestelmaohje()

    def aloita_tukitila(syy):
        """Vakavan asian jälkeen lääkekysymykset jäävät pois: malli kuuntelee ja
        varmistaa turvallisuuden. Puhelua ei katkaista ihmisen alta."""
        nonlocal huoli_jatko
        nimi = tietopohja.SELKONIMET.get(syy, syy.replace("_", " "))
        if tila["tukitila"] is None:
            tila["tukitila"] = nimi
        history[0] = {"role": "system", "content": jarjestelmaohje()}
        huoli_jatko = 0
        print(f"[TUKITILA: {nimi}]")

    def lopetuslause():
        return T("lopetuslause_tuki") if tila["tukitila"] else T("lopetuslause")

    def vaihda_kieli(uusi):
        nonlocal kieli, kieli_lipsahti
        if uusi == kieli:
            return
        kieli_lipsahti = False
        print(f"[KIELI: {tietopohja.KIELET['nimet'][kieli]} -> {tietopohja.KIELET['nimet'][uusi]}]")
        kieli = uusi
        if uusi not in kielet_puhelussa:
            kielet_puhelussa.append(uusi)
        history[0] = {"role": "system", "content": jarjestelmaohje()}

    print("\nPuhelu alkaa (Ctrl+C lopettaa).\n")

    # AI Act art. 50: kerrotaan heti olevansa tekoäly. Avaus on kiinteä teksti
    # ja päättyy kysymykseen "miten sinulla on mennyt".
    avaus = T("avauslause")
    if auto_kieli:
        # Kerrotaan heti, että voi puhua myös ruotsia tai englantia (omilla äänillään),
        # ja lopetetaan suomenkieliseen kysymykseen.
        lauseet = SENTENCE_END.split(avaus)
        alku, kysymys_alku = " ".join(lauseet[:-1]), lauseet[-1]
        puhu(alku, speaker)
        for k, vihje in tietopohja.KIELET["avaus_kielivihje"]:
            puhu(vihje, speaker, kieli=k)
        puhu(kysymys_alku, speaker)
        history.append({"role": "assistant", "content": avaus})
    else:
        puhu(avaus, speaker, history, kieli)

    try:
        for _vuoro in range(MAX_PUHELU_VUOROT):
            laatu = None
            if args.teksti:
                text = input("Sinä: ").strip()
                if not text:
                    continue
                if auto_kieli:
                    vaihda_kieli(valitsin.paivita(text))
            else:
                print("Kuuntelen...", end="\r", flush=True)
                audio = record_utterance(threshold, mute)
                text, laatu, whisper_kieli, varmuus = litteroi(stt, audio, history, sanasto, kieli, auto_kieli)
                if len(text) < 2:
                    continue
                if auto_kieli:
                    vaihda_kieli(valitsin.paivita(text, whisper_kieli, varmuus))
                print(f"Sinä: {text}")

            # 1) Sääntöpohjainen tarkistus ENNEN mallia: hätä, vakavat huolet, oireet, lopetus.
            kysymys = tietopohja.viimeisin_kysymys(history)
            kaiku = (not args.teksti) and tietopohja.on_kaikuharha(
                text, kysymys, (sanasto or "") if kieli == "fi" else "")
            havainto = tietopohja.tarkista(text, kaikuharha=kaiku)
            havainnot.append(havainto)

            # Vastaus suoraan varmistuskysymykseen ("onko ollut ajatuksia vahingoittaa itseäsi?").
            if odottaa_varmistusta:
                odottaa_varmistusta = False
                if not havainto.hata and tietopohja.on_myontava(text):
                    havainto.hata.append("itsetuhoisuus")

            # Tukitilassa jo käsitelty itsetuhoisuus ei toista samaa pitkää viestiä joka
            # vuorolla: tukitilan malli jatkaa kuuntelemista.
            jo_tuettu = tila["tukitila"] and all(m in tietopohja.HATA_TUKITILAAN for m in havainto.hata)
            if havainto.hata and not jo_tuettu:
                syy = havainto.hata[0]
                print(f"[HÄTÄMERKKI: {', '.join(havainto.hata)}]")
                history.append({"role": "user", "content": text})
                try:
                    viesti = T(f"hataviestit.{syy}")
                except KeyError:
                    viesti = T("hataviesti")
                puhu(viesti, speaker, history, kieli)
                if syy in tietopohja.HATA_TUKITILAAN:
                    aloita_tukitila(syy)       # ei katkaista: jäädään tueksi
                    tila["lopputapa"] = f"tukitila: {tila['tukitila']}"
                    continue
                tila["lopputapa"] = f"hätä: ohjattu 112 ({syy})"
                break

            # 2) Lopetuspyyntö. Ilman huolia: 1. kysy kuulumiset, 2. lopeta.
            #    Huolien kanssa: 1. yksi turvallisuuskysymys, 2. lopeta. Ei pidätellä enempää.
            if havainto.lopetuspyynto:
                tila["lopetuspyyntoja"] += 1
                n = tila["lopetuspyyntoja"]
                huolet = tietopohja.huolet(havainnot, tila["selkeys"]["sisalto_epaselva"])
                huolet_teksti = ", ".join(huolet)
                history.append({"role": "user", "content": text})
                print(f"[lopetuspyyntö {n}, huolet: {huolet_teksti or 'ei'}]")
                if huolet and not turvallisuus_kysytty and n <= 2:
                    turvallisuus_kysytty = True
                    puhu(T("lopetuspyynto.turvallisuuskysymys").format(huolet=huolet_teksti),
                         speaker, history, kieli)
                    continue
                if n == 1 and not huolet:
                    puhu(T("lopetuspyynto.kysy_syy"), speaker, history, kieli)
                    kerrat = 0
                    continue
                viesti = T("lopetuspyynto.lopetus_huolestuttava" if huolet else "lopetuspyynto.lopetus")
                puhu(viesti.format(huolet=huolet_teksti), speaker, history, kieli)
                tila["lopputapa"] = "henkilö lopetti" + (" (huolia kirjattu)" if huolet else "")
                break

            # 3) Itsetuhoisuus tai yliannostus mainittiin kieltomuodossa -> varmistetaan
            #    suoraan ja lämpimästi (kiinteä kysymys, ei mallin varassa).
            if not varmistettu and any(m in tietopohja.VARMISTETTAVAT for m in havainto.hata_kielletty):
                varmistettu = True
                odottaa_varmistusta = True
                history.append({"role": "user", "content": text})
                puhu(T("varmistuskysymys"), speaker, history, kieli)
                continue

            # 4) Vakava huoli (huono kohtelu, outoja aistimuksia, toivottomuus):
            #    kiinteä empaattinen repliikki, ei hypätä seuraavaan kysymykseen.
            uudet_vakavat = [v for v in havainto.vakavat if v not in kasitellyt_vakavat]
            if uudet_vakavat:
                kasitellyt_vakavat.update(uudet_vakavat)
                print(f"[VAKAVA HUOLI: {', '.join(uudet_vakavat)}]")
                history.append({"role": "user", "content": text})
                puhu(T(f"vakavat_huolet.{uudet_vakavat[0]}.viesti"), speaker, history, kieli)
                aloita_tukitila(uudet_vakavat[0])
                tila["lopputapa"] = f"tukitila: {tila['tukitila']}"
                continue

            # 5) Saatiinko vastauksesta selvää? (säännöt, ei mallia)
            arvio = "epäselvä" if kaiku else tietopohja.arvioi_vastaus(text, laatu)
            ohje = ""
            if arvio != "selvä":
                print(f"[{arvio}{' (kaikuharha)' if kaiku else ''}]")
                if kerrat < uud["max_per_kysymys"]:
                    kerrat += 1
                    tila["selkeys"]["uudelleenkysymyksia"] += 1
                    alku = T("uudelleenkysymys.toistoviesti" if arvio == "toistopyyntö"
                             else "uudelleenkysymys.epaselva_viesti")
                    puhu(f"{alku} {kysymys}", speaker, kieli=kieli)
                    continue
                # Raja täynnä: ei jäädä jumiin, malli siirtyy seuraavaan kysymykseen.
                tila["selkeys"]["ohitetut"].append(kysymys)
                puhu(T("uudelleenkysymys.ohitusviesti"), speaker, kieli=kieli)
                text = "(ei selvää vastausta)"
                ohje = tietopohja.piilo_ohje("siirry", kieli)
                kerrat = 0
            elif kerrat >= uud["max_per_kysymys"]:
                ohje = tietopohja.piilo_ohje("ei_uudelleen", kieli)

            # 6) Uusi oire -> tietopohjan jatkokysymykset ja kotihoito-ohjeet mallille.
            uudet = [o for o in havainto.oireet if o not in kasitellyt_oireet]
            kasitellyt_oireet.update(uudet)
            ohje += tietopohja.oireohje_mallille(uudet)

            # 7) Huoli, pelko tai suru: malli pysähtyy asian äärelle eikä kysy seuraavaa.
            if havainto.huoli:
                print(f"[huoli: {', '.join(havainto.huoli)}]")
                ohje = tietopohja.piilo_ohje("huoli", kieli) + ohje
                huoli_jatko = huoli_saanto["jatkovuoroja"]
            elif huoli_jatko > 0:
                huoli_jatko -= 1
                ohje += tietopohja.piilo_ohje("huoli_jatko", kieli)

            # 8) Muulla kuin suomella: lyhyt muistutus joka vuorolla pitää kielen ja aiheen.
            if kieli_lipsahti:
                ohje += tietopohja.piilo_ohje("kieli_lipsahti", kieli)
            ohje += tietopohja.piilo_ohje("muistutus", kieli)

            # 9) Malli jatkaa keskustelua (tai kysyy uudelleen: UUDELLEEN).
            history.append({"role": "user", "content": text + ohje})
            print("AI: ", end="", flush=True)
            try:
                reply = ask_ollama(history, speaker, kieli)
            except requests.RequestException as e:
                print(f"\n[Ollama-virhe: {e}]")
                history.pop()
                continue
            if speaker:
                speaker.wait()
            reply = suodata_vastaus(reply)
            vastauksen_kieli = tietopohja.tunnista_kieli(reply.replace(LOPETUSMERKKI, ""), kieli)
            kieli_lipsahti = vastauksen_kieli != kieli
            if kieli_lipsahti:
                print(f"[mallin vastaus lipsahti kielelle {vastauksen_kieli}, muistutetaan]")
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
                puhu(lopetuslause(), speaker, history, kieli)
                tila["lopputapa"] = ("tukitila: " + tila["tukitila"]) if tila["tukitila"] else "keskustelu käyty"
                break
            history.append({"role": "assistant", "content": reply})
            history[:] = trim_history(history)
        else:
            puhu(lopetuslause(), speaker, history, kieli)
            tila["lopputapa"] = "maksimikesto"
    except (KeyboardInterrupt, EOFError):
        pass

    # Analyysi tehdään AINA, myös hätä- ja lopetustilanteissa.
    print("\nAnalysoidaan puhelu hoitajalle...")
    analyysi = tee_analyysi(history, havainnot) if len(history) > 2 else {"virhe": "ei vastauksia"}
    tallenna_analyysi(profiili, havainnot, analyysi, tila)
    print("Näkemiin!")


if __name__ == "__main__":
    main()
