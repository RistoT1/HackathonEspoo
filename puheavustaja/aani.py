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


def lauseiksi(teksti: str) -> list[str]:
    return [l.strip() for l in _LAUSEEN_LOPPU.split(teksti) if l.strip()]


# ---------------------------------------------------------------- PUHUJA
class Puhuja:
    """Puhuu lauseita reaaliajassa. say() palaa heti; wait() odottaa kunnes kaikki on puhuttu."""

    def __init__(self, aani: str, mute_event: threading.Event,
                 nopeus: float = OLETUSNOPEUS, tauko_s: float = LAUSETAUKO_S,
                 voimakkuus: float = AANENVOIMAKKUUS, kieliaanet: dict | None = None):
        """aani = suomenkielinen pääääni. kieliaanet = {"sv": "sv_SE-nst-medium", ...}:
        muiden kielten äänet ladataan vasta, kun kieltä tarvitaan."""
        from piper import PiperVoice, SynthesisConfig   # pip install piper-tts
        import sounddevice as sd

        self._sd = sd
        self._PiperVoice = PiperVoice
        polku = aanen_polku(aani)
        self.voice = PiperVoice.load(str(polku))
        self.config = SynthesisConfig(length_scale=1.0 / nopeus, volume=voimakkuus)
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
                palat = [c.audio_float_array for c in voice.synthesize(puheeksi(lause, kieli), self.config)]
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
