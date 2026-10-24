"""Speech-recognise each English voice clip and say whether its subtitle is the line it speaks (developer tool).

    uv run --with faster-whisper==1.2.1 --with nvidia-cublas-cu12==12.9.2.10 --with nvidia-cudnn-cu12==9.27.0.42 \
        python tools/asr_audit.py [--limit N] [--ids 2442,5562]

For every English-voice context of src/wozmod/text/contexts.py with a donor clip, it transcribes the clip and scores
the transcript against the context's shown English and against the donor lines of its neighbours and of every other
context behind the same table key. Verdict per context id: match (the shown English scores best), crossed (another
line scores clearly better), unclear. Writes work/asr/verdicts.json (context ids, voice ids, clip hashes, verdicts,
scores) and work/asr/transcripts/ (game text: it stays in work/). Each transcript is cached by (clip SHA-256,
settings hash), so a rerun only does new or changed clips; tests/test_subtitle_contexts.py recomputes the verdicts
from those transcripts under WOZMOD_ASR=1 with verdicts() below.

Needs faster-whisper and NVIDIA's CUDA 12 cuBLAS and cuDNN 9 wheels (not project dependencies; the command above
supplies them), ffmpeg and an NVIDIA GPU.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from collections.abc import Callable
from pathlib import Path

import checkout

# Pinned so a rerun gives the same words: the library, the model and its Hugging Face revision, decoding and VAD.
# No VAD: its speech filter cut whole lines from WO2 PSP and some Xbox 360 clips (268 of the 585 clips that were not a
# match with it transcribe as their shown line without it; every match under 0.75 stays a match).
SETTINGS = {"faster_whisper": "1.2.1", "model": "Systran/faster-whisper-large-v3",
            "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478", "compute_type": "int8_float16", "beam_size": 5,
            "temperature": 0.0, "language": "en", "vad_filter": False, "condition_on_previous_text": False}
MATCH = 0.6  # the shown English must score this much to match (ratio of matching characters)
CROSSED_MARGIN = 0.15  # another line must beat the shown English by this much
CROSSED_FLOOR = 0.5  # and score this much itself: two near-zero scores name no line the clip says


def _norm(text: str) -> str:
    return " ".join("".join(c for c in text.casefold() if c.isalnum() or c.isspace()).split())


def score(heard: str, text: str) -> float:
    return SequenceMatcher(None, _norm(heard), _norm(text)).ratio()


def verdict(heard: str, shown: str, others: dict[str, str]) -> tuple[str, str | None, dict[str, float]]:
    """(match, crossed or unclear; the best other context; the scores) for one transcript. An other line with no
    words ("...") is no rival: an empty transcript would score 1.0 on it against any shown line."""
    others = {where: text for where, text in others.items() if _norm(text)}
    scores = {"shown": score(heard, shown), **{where: score(heard, text) for where, text in others.items()}}
    best = max(others, key=scores.get, default=None)
    if best and scores[best] >= max(scores["shown"] + CROSSED_MARGIN, CROSSED_FLOOR):
        return "crossed", best, scores
    return ("match" if scores["shown"] >= MATCH else "unclear"), best, scores


def transcripts() -> Path:
    """The transcript cache of these settings: <clip SHA-256>.txt per clip."""
    settings = hashlib.sha256(json.dumps(SETTINGS, sort_keys=True).encode()).hexdigest()[:16]
    return checkout.work() / "asr/transcripts" / settings


def clip_riff(world, clip) -> bytes:
    """The donor RIFF clip (donor, record) of a World's banks."""
    from wozmod.formats.audio import _read_at

    records, bank = world.banks[clip[0]]
    with bank.open("rb") as stream:
        return _read_at(stream, *records[clip[1]])


def verdicts(contexts: list, heard: Callable[[object], str | None]) -> dict[str, tuple[str, str | None, dict]]:
    """Each voiced context's verdict by context id, scored on heard(context), its clip's transcript; a context whose
    transcript is None has none. The rivals are the neighbours and every other context behind the same table key."""
    by_id = {c.id: c for c in contexts}
    by_key: dict[bytes | None, list] = {}
    for context in contexts:
        by_key.setdefault(context.key, []).append(context)
    found = {}
    for context in contexts:
        if context.voice and (words := heard(context)) is not None:
            others = {o.id: o.text for o in by_key.get(context.key, []) if o is not context}
            others |= {n: by_id[n].text for n in context.neighbours if n in by_id}
            found[context.id] = verdict(words, context.text, others)
    return found


def _load_cuda() -> None:
    """Load the cuBLAS and cuDNN libraries of the nvidia wheels, which CTranslate2 finds only on the loader path."""
    import ctypes
    import importlib

    for name in ("nvidia.cublas.lib", "nvidia.cudnn.lib"):
        folder = Path(next(iter(importlib.import_module(name).__path__)))
        for library in sorted(folder.glob("lib*.so.*"), key=lambda f: "Lt" not in f.name):  # cublasLt first
            ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)


def _samples(riff: bytes):
    """The clip as 16 kHz mono float samples, decoded by ffmpeg: faster-whisper's own decoder calls PyAV with an
    argument PyAV 15 removed."""
    import numpy

    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", "16000", "pipe:1"],
                         input=riff, capture_output=True, check=True).stdout
    return numpy.frombuffer(pcm, numpy.float32)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, help="transcribe at most this many new clips (a pilot run)")
    parser.add_argument("--ids", help="only these voice ids, comma separated")
    args = parser.parse_args()
    try:
        _load_cuda()
        from faster_whisper import WhisperModel
        from huggingface_hub import snapshot_download
    except ImportError:
        print("faster-whisper is not installed: run it as the docstring shows", file=sys.stderr)
        return 2
    from wozmod.text.contexts import World, build_text_stage

    found = checkout.inputs()
    game, donors = Path(found["WOZMOD_GAME"]), Path(found["WOZMOD_DONORS"])
    out, cache = checkout.work() / "asr", transcripts()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as stage:
        world = World(game, donors, build_text_stage(game, donors, Path(stage)), checkout.REPO / "data/text")
        found = world.contexts("english")
    wanted = {int(i) for i in args.ids.split(",")} if args.ids else None
    model = WhisperModel(snapshot_download(SETTINGS["model"], revision=SETTINGS["revision"]),
                         compute_type=SETTINGS["compute_type"])
    digests, new = {}, 0
    for clip in dict.fromkeys(c.voice for c in found if c.voice and (not wanted or c.voice_id in wanted)):
        riff = clip_riff(world, clip)
        digest = hashlib.sha256(riff).hexdigest()
        cached = cache / f"{digest}.txt"
        if not cached.exists():
            if args.limit is not None and new >= args.limit:
                continue
            segments, _ = model.transcribe(_samples(riff), **{k: v for k, v in SETTINGS.items()
                                                              if k not in ("faster_whisper", "model", "revision",
                                                                           "compute_type")})
            cached.write_text(" ".join(s.text for s in segments), encoding="utf-8")
            new += 1
        digests[clip] = digest

    def heard(context) -> str | None:
        return (cache / f"{digests[context.voice]}.txt").read_text(encoding="utf-8") if context.voice in digests else None

    by_id = {c.id: c for c in found}
    results = {where: {"voice_id": by_id[where].voice_id, "clip_sha256": digests[by_id[where].voice], "verdict": kind,
                       "best": best, "scores": scores}
               for where, (kind, best, scores) in verdicts(found, heard).items()}
    (out / "verdicts.json").write_text(json.dumps({"settings": SETTINGS, "verdicts": results}, indent=1))
    print(f"{len(results)} verdicts ({new} new transcripts) in {out / 'verdicts.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
