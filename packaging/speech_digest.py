"""Print one SHA-256 of the phones PocketSphinx (wozmod_native) gives for a few synthetic voices, aligned to words and
heard with none. packaging/build_windows.sh runs it with the Linux module and with the Windows one under Wine: the
generated mouths come from these phones, so the two must agree to the bit.

    python packaging/speech_digest.py SPEECH_MODEL_FOLDER
"""
import hashlib
import math
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wozmod.voices.phones import PHONE_RATE, Listener  # noqa: E402


def voice(seed: int, seconds: float = 1.5) -> bytes:
    """A pitch pulse train through two formants that move every 0.1 s, with a pause every 0.75 s, and a little noise."""
    rng, out, phase = random.Random(seed), [], 0.0
    f1 = f2 = 0.0
    for i in range(int(PHONE_RATE * seconds)):
        if i % 1600 == 0:
            f1, f2 = rng.uniform(300, 900), rng.uniform(900, 2500)
        phase += 2 * math.pi * 120 / PHONE_RATE
        pulse = 0.6 if (i // 4000) % 3 != 2 and math.sin(phase) > 0.95 else 0.0
        t = i / PHONE_RATE
        out.append(pulse * (math.sin(2 * math.pi * f1 * t) + 0.5 * math.sin(2 * math.pi * f2 * t)) + rng.gauss(0, 0.01))
    return b"".join(struct.pack("<h", round(max(-1.0, min(1.0, x)) * 32000)) for x in out)


if __name__ == "__main__":
    listener = Listener(Path(sys.argv[1]), 4)
    speech = {seed: voice(seed) for seed in range(6)}
    texts = ("hello there", "one two three", "a good day to you")
    found = (listener.aligned({v: (s, texts[v % 3]) for v, s in speech.items()}), listener.heard(speech))
    print(hashlib.sha256(repr(found).encode()).hexdigest(), sum(x is not None for x in found[0].values()), "aligned")
