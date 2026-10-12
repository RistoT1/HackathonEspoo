"""
Suomenkielinen puhesynteesi (Piper) reaaliajassa.

- Vastaus luetaan lause kerrallaan heti kun malli on kirjoittanut lauseen valmiiksi.
- Kaksi säiettä: toinen syntetisoi seuraavaa lausetta sillä aikaa kun toinen soittaa
  edellistä, joten lauseiden väliin ei jää odotusta.
- Ikäihmisille: oletuksena hieman hitaampi puhe, tauko lauseiden välissä ja numerot
  (112, puhelinnumerot) luetaan numero kerrallaan.

Äänet ovat kansiossa aanet/. Uuden äänen voi lisätä kopioimalla sinne Piper-mallin
(.onnx + .onnx.json) ja valitsemalla sen: python puheavustaja.py --aani <nimi>
Tunnetut suomenkieliset äänet ladataan automaattisesti (lataa_aani).

Asennus: pip install piper-tts sounddevice
"""

import queue
import re
import threading
import urllib.request
from pathlib import Path

import numpy as np

AANIKANSIO = Path(__file__).resolve().parent / "aanet"
OLETUSAANI = "fi_FI-harri-medium"

# Piperin viralliset suomenkieliset äänet (Hugging Face: rhasspy/piper-voices).
_HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/fi/fi_FI"
TUNNETUT_AANET = {
    "fi_FI-harri-medium": f"{_HF}/harri/medium/fi_FI-harri-medium.onnx",
    "fi_FI-harri-low": f"{_HF}/harri/low/fi_FI-harri-low.onnx",
    # Ruotsi ja englanti: käytetään, kun henkilö puhuu niitä (knowledge/kielet.json).
    "sv_SE-nst-medium": "https://huggingface.co/rhasspy/piper-voices/resolve/main/sv/sv_SE/nst/medium/sv_SE-nst-medium.onnx",
    "en_GB-alan-medium": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx",
}

# Ikäihmisille sopivat oletukset. ALKUARVOJA: kuuntele ja säädä.
OLETUSNOPEUS = 0.85        # 1.0 = Piperin normaali, pienempi = hitaampi
LAUSETAUKO_S = 0.35        # hiljaisuus lauseiden välissä
AANENVOIMAKKUUS = 1.0      # kerroin normalisoidun äänen päälle

# Huudahdukset ("Oho!", "Voi ei!", "Whoa!") lausutaan tunteella: nopeammin ja kovempaa
# kuin muu puhe. Tunnistetaan lauseen alusta. Lista on muokattavissa:
# knowledge/keskustelusaannot.json -> tunteet.huudahdukset (puheavustaja välittää sen tänne).
HUUDAHDUS_NOPEUS = 1.35    # kerroin puhenopeuteen (1.0 = sama kuin muu puhe)
HUUDAHDUS_VOIMAKKUUS = 1.7  # kerroin äänenvoimakkuuteen (rajataan säröytymättömäksi)
HUUDAHDUS_TAUKO_S = 0.12   # lyhyt hengähdys huudahduksen jälkeen
OLETUSHUUDAHDUKSET = [
    "oho", "ohhoh", "voi ei", "voi että", "voi voi", "voi hyvänen aika", "huh huh", "ai kamala", "hui",
    "oj", "oj då", "åh nej", "herregud", "usch",
    "whoa", "wow", "oh no", "oh dear", "oh my", "goodness",
]

_LAUSEEN_LOPPU = re.compile(r"(?<=[.!?…])\s+")
_NUMEROT = {
    "fi": ["nolla", "yksi", "kaksi", "kolme", "neljä", "viisi", "kuusi", "seitsemän", "kahdeksan", "yhdeksän"],
    "sv": ["noll", "ett", "två", "tre", "fyra", "fem", "sex", "sju", "åtta", "nio"],
    "en": ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
}


# ---------------------------------------------------------------- ÄÄNIMALLIT
def aanen_polku(nimi_tai_polku: str) -> Path:
    """Hyväksyy äänen nimen (aanet/-kansiosta) tai suoran polun .onnx-tiedostoon."""
    polku = Path(nimi_tai_polku)
    if polku.suffix == ".onnx":
        return polku
    return AANIKANSIO / f"{nimi_tai_polku}.onnx"


def aani_loytyy(nimi_tai_polku: str) -> bool:
    polku = aanen_polku(nimi_tai_polku)
    return polku.exists() and Path(str(polku) + ".json").exists()


def lataa_aani(nimi: str = OLETUSAANI) -> Path:
    """Lataa tunnetun suomenkielisen äänen aanet/-kansioon (.onnx + .onnx.json)."""
    if nimi not in TUNNETUT_AANET:
        raise ValueError(f"Tuntematon ääni '{nimi}'. Tunnetut: {', '.join(TUNNETUT_AANET)}")
    AANIKANSIO.mkdir(exist_ok=True)
    url = TUNNETUT_AANET[nimi]
    kohde = AANIKANSIO / f"{nimi}.onnx"
    for lahde, tiedosto in ((url + ".json", Path(str(kohde) + ".json")), (url, kohde)):
        if tiedosto.exists():
            continue
        print(f"  Ladataan {tiedosto.name}...")
        valiaikainen = tiedosto.with_suffix(tiedosto.suffix + ".osittain")
        urllib.request.urlretrieve(lahde, valiaikainen)
        valiaikainen.replace(tiedosto)
    return kohde


def saatavilla_olevat_aanet() -> list[str]:
    if not AANIKANSIO.exists():
        return []
    return sorted(p.stem for p in AANIKANSIO.glob("*.onnx") if Path(str(p) + ".json").exists())


# ---------------------------------------------------------------- TEKSTI PUHEEKSI
def _numero_kerrallaan(numero: str, kieli: str = "fi") -> str:
    sanat = _NUMEROT.get(kieli, _NUMEROT["fi"])
    return ", ".join(sanat[int(n)] for n in numero if n.isdigit())


def puheeksi(teksti: str, kieli: str = "fi") -> str:
    """Muokkaa tekstiä niin, että se kuulostaa selkeältä ääneen luettuna."""
    # Puhelinnumerot (esim. 09 2525 0111, 116117, 112) numero kerrallaan.
    teksti = re.sub(r"\b(0\d(?:[ -]?\d){5,}|116 ?117|112)\b",
                    lambda m: _numero_kerrallaan(m.group(0), kieli), teksti)
    return teksti.strip()


def huudahdus_kuvio(huudahdukset: list[str] | None = None) -> re.Pattern:
    """Lauseen alussa oleva huudahdus välimerkkeineen, esim. 'Oho!', 'Voi ei,', 'Whoa...'.
    Pidemmät ensin, jotta 'voi ei' osuu ennen 'voi'. Venytykset (ohoo, whoaa) sallitaan."""
    sanat = sorted({h.strip().lower() for h in (huudahdukset or OLETUSHUUDAHDUKSET) if h.strip()},
                   key=len, reverse=True)
    vaihtoehdot = "|".join(re.escape(h) + "+" for h in sanat)
    return re.compile(rf"^\s*((?:{vaihtoehdot})(?:[ ,]+(?:{vaihtoehdot}))*[!.,…]*)(?=\s|$)", re.IGNORECASE)


def jaa_huudahdus(lause: str, kuvio: re.Pattern) -> list[tuple[str, bool]]:
    """[(teksti, korostetaanko)]. 'Oho, tuo kuulostaa pahalta.' ->
    [('Oho!', True), ('tuo kuulostaa pahalta.', False)]"""
    osuma = kuvio.match(lause)
    if not osuma:
        return [(lause, False)]
    huuto = osuma.group(1).rstrip(" ,")
    if not huuto.endswith(("!", ".", "…")):
        huuto += "!"
    loput = lause[osuma.end():].strip()
    return [(huuto, True)] + ([(loput, False)] if loput else [])


def lauseiksi(teksti: str) -> list[str]:
    return [l.strip() for l in _LAUSEEN_LOPPU.split(teksti) if l.strip()]


def _vahvista(aani_: np.ndarray, kerroin: float) -> np.ndarray:
    """Kovempi ääni ilman säröä: vahvistus, mutta huippu enintään 0.98."""
    huippu = float(np.max(np.abs(aani_))) or 1.0
    kerroin = min(kerroin, 0.98 / huippu)
    return (aani_ * kerroin).astype(np.float32)


# ---------------------------------------------------------------- PUHUJA
class Puhuja:
    """Puhuu lauseita reaaliajassa. say() palaa heti; wait() odottaa kunnes kaikki on puhuttu."""

    def __init__(self, aani: str, mute_event: threading.Event,
                 nopeus: float = OLETUSNOPEUS, tauko_s: float = LAUSETAUKO_S,
                 voimakkuus: float = AANENVOIMAKKUUS, kieliaanet: dict | None = None,
                 huudahdukset: list[str] | None = None,
                 huudahdus_nopeus: float = HUUDAHDUS_NOPEUS,
                 huudahdus_voimakkuus: float = HUUDAHDUS_VOIMAKKUUS):
        """aani = suomenkielinen pääääni. kieliaanet = {"sv": "sv_SE-nst-medium", ...}:
        muiden kielten äänet ladataan vasta, kun kieltä tarvitaan."""
        from piper import PiperVoice, SynthesisConfig   # pip install piper-tts
        import sounddevice as sd

        self._sd = sd
        self._PiperVoice = PiperVoice
        polku = aanen_polku(aani)
        self.voice = PiperVoice.load(str(polku))
        self.config = SynthesisConfig(length_scale=1.0 / nopeus, volume=voimakkuus)
        # Huudahdukset: nopeampi tempo, ja voimakkuus nostetaan jälkikäteen (rajattuna).
        self.config_huuto = SynthesisConfig(length_scale=1.0 / (nopeus * huudahdus_nopeus), volume=voimakkuus)
        self.huuto_vahvistus = huudahdus_voimakkuus
        self.huuto_kuvio = huudahdus_kuvio(huudahdukset)
        self.sample_rate = self.voice.config.sample_rate
        self.tauko_s = tauko_s
        self.kieliaanet = dict(kieliaanet or {})
        self._aanet = {"fi": self.voice}
        self.mute_event = mute_event
        self.tekstit: queue.Queue = queue.Queue()
        self.aanet: queue.Queue = queue.Queue()
        threading.Thread(target=self._syntetisoi, daemon=True).start()
        threading.Thread(target=self._soita, daemon=True).start()
        print(f"Ääni: {polku.stem}, nopeus {nopeus}")

    def _aani(self, kieli: str):
        """Kielen ääni. Puuttuva ääni ladataan verkosta kerran; jos ei onnistu, suomi."""
        if kieli in self._aanet:
            return self._aanet[kieli]
        nimi = self.kieliaanet.get(kieli)
        voice = self.voice
        if nimi:
            try:
                if not aani_loytyy(nimi) and nimi in TUNNETUT_AANET:
                    print(f"\n[Ladataan ääni {nimi}...]")
                    lataa_aani(nimi)
                voice = self._PiperVoice.load(str(aanen_polku(nimi)))
                print(f"\n[Ääni vaihdettu: {nimi}]")
            except Exception as e:
                print(f"\n[Äänen {nimi} lataus ei onnistunut ({e}); käytetään suomenkielistä ääntä]")
        self._aanet[kieli] = voice
        return voice

    def say(self, teksti: str, kieli: str = "fi") -> None:
        self.mute_event.set()            # ei kuunnella mikrofonia, kun avustaja puhuu
        for lause in lauseiksi(teksti):
            self.tekstit.put((lause, kieli))

    def wait(self) -> None:
        self.tekstit.join()
        self.aanet.join()
        self.mute_event.clear()

    def _syntetisoi(self) -> None:
        while True:
            lause, kieli = self.tekstit.get()
            try:
                voice = self._aani(kieli)
                sr = voice.config.sample_rate
                tauko = np.zeros(int(sr * self.tauko_s), dtype=np.float32)
                palat = []
                for osa, huuto in jaa_huudahdus(lause, self.huuto_kuvio):
                    config = self.config_huuto if huuto else self.config
                    aanet = [c.audio_float_array for c in voice.synthesize(puheeksi(osa, kieli), config)]
                    if not aanet:
                        continue
                    aani_ = np.concatenate(aanet).astype(np.float32)
                    if huuto:
                        aani_ = _vahvista(aani_, self.huuto_vahvistus)
                        aani_ = np.concatenate([aani_, np.zeros(int(sr * HUUDAHDUS_TAUKO_S), dtype=np.float32)])
                    palat.append(aani_)
                if palat:
                    self.aanet.put((np.concatenate([*palat, tauko]).astype(np.float32), sr))
            except Exception as e:
                print(f"\n[Puhesynteesin virhe: {e}]")
            finally:
                self.tekstit.task_done()

    def _soita(self) -> None:
        while True:
            aani, sr = self.aanet.get()
            try:
                self._sd.play(aani, sr)
                self._sd.wait()
            except Exception as e:
                print(f"\n[Äänentoiston virhe: {e}]")
            finally:
                self.aanet.task_done()
