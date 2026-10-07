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
}

# Ikäihmisille sopivat oletukset. ALKUARVOJA: kuuntele ja säädä.
OLETUSNOPEUS = 0.85        # 1.0 = Piperin normaali, pienempi = hitaampi
LAUSETAUKO_S = 0.35        # hiljaisuus lauseiden välissä
AANENVOIMAKKUUS = 1.0      # kerroin normalisoidun äänen päälle

_LAUSEEN_LOPPU = re.compile(r"(?<=[.!?…])\s+")
_NUMEROT = ["nolla", "yksi", "kaksi", "kolme", "neljä", "viisi", "kuusi", "seitsemän", "kahdeksan", "yhdeksän"]


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
def _numero_kerrallaan(numero: str) -> str:
    return ", ".join(_NUMEROT[int(n)] for n in numero if n.isdigit())


def puheeksi(teksti: str) -> str:
    """Muokkaa tekstiä niin, että se kuulostaa selkeältä ääneen luettuna."""
    # Puhelinnumerot (esim. 09 2525 0111) numero kerrallaan.
    teksti = re.sub(r"\b0\d(?:[ -]?\d){5,}\b", lambda m: _numero_kerrallaan(m.group(0)), teksti)
    teksti = re.sub(r"\b112\b", "yksi yksi kaksi", teksti)
    return teksti.strip()


def lauseiksi(teksti: str) -> list[str]:
    return [l.strip() for l in _LAUSEEN_LOPPU.split(teksti) if l.strip()]


# ---------------------------------------------------------------- PUHUJA
class Puhuja:
    """Puhuu lauseita reaaliajassa. say() palaa heti; wait() odottaa kunnes kaikki on puhuttu."""

    def __init__(self, aani: str, mute_event: threading.Event,
                 nopeus: float = OLETUSNOPEUS, tauko_s: float = LAUSETAUKO_S,
                 voimakkuus: float = AANENVOIMAKKUUS):
        from piper import PiperVoice, SynthesisConfig   # pip install piper-tts
        import sounddevice as sd

        self._sd = sd
        polku = aanen_polku(aani)
        self.voice = PiperVoice.load(str(polku))
        self.config = SynthesisConfig(length_scale=1.0 / nopeus, volume=voimakkuus)
        self.sample_rate = self.voice.config.sample_rate
        self.tauko = np.zeros(int(self.sample_rate * tauko_s), dtype=np.float32)
        self.mute_event = mute_event
        self.tekstit: queue.Queue = queue.Queue()
        self.aanet: queue.Queue = queue.Queue()
        threading.Thread(target=self._syntetisoi, daemon=True).start()
        threading.Thread(target=self._soita, daemon=True).start()
        print(f"Ääni: {polku.stem}, nopeus {nopeus}")

    def say(self, teksti: str) -> None:
        self.mute_event.set()            # ei kuunnella mikrofonia, kun avustaja puhuu
        for lause in lauseiksi(teksti):
            self.tekstit.put(lause)

    def wait(self) -> None:
        self.tekstit.join()
        self.aanet.join()
        self.mute_event.clear()

    def _syntetisoi(self) -> None:
        while True:
            lause = self.tekstit.get()
            try:
                palat = [c.audio_float_array for c in self.voice.synthesize(puheeksi(lause), self.config)]
                if palat:
                    self.aanet.put(np.concatenate([*palat, self.tauko]).astype(np.float32))
            except Exception as e:
                print(f"\n[Puhesynteesin virhe: {e}]")
            finally:
                self.tekstit.task_done()

    def _soita(self) -> None:
        while True:
            aani = self.aanet.get()
            try:
                self._sd.play(aani, self.sample_rate)
                self._sd.wait()
            except Exception as e:
                print(f"\n[Äänentoiston virhe: {e}]")
            finally:
                self.aanet.task_done()
