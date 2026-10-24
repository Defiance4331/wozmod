"""Speech-recognise each English voice clip and say whether its subtitle is the line it speaks (developer tool).

    uv run --with faster-whisper==1.1.1 --with requests python tools/asr_audit.py [--limit N] [--ids 2442,5562]

For every English-voice context of src/wozmod/text/contexts.py with a donor clip, it transcribes the clip and scores
the transcript against the context's shown English and against the donor lines of its neighbours and of every other
context behind the same table key. Verdict per voice id: match (the shown English scores best), crossed (another
line scores clearly better), unclear. Writes work/asr/verdicts.json (voice ids, clip hashes, verdicts, scores) and
work/asr/transcripts/ (game text: it stays in work/). Each transcript is cached by (clip SHA-256, settings hash), so
a rerun only does new or changed clips; tests/test_subtitle_contexts.py reads the verdicts under WOZMOD_ASR=1.

Needs faster-whisper 1.1.1 and requests, which are not project dependencies; the command above supplies both.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

import checkout

# Pinned so a rerun gives the same words: the model and its Hugging Face revision, decoding and VAD settings. The
# revision is still to be pinned to the hash the first full run downloads (None takes the newest).
SETTINGS = {"model": "large-v3", "revision": None, "compute_type": "int8_float16",
            "beam_size": 5, "temperature": 0.0, "language": "en", "vad_filter": True, "condition_on_previous_text": False}
CROSSED_MARGIN = 0.15  # another line must beat the shown English by this much (ratio of matching characters)


def _norm(text: str) -> str:
    return " ".join("".join(c for c in text.casefold() if c.isalnum() or c.isspace()).split())


def score(heard: str, text: str) -> float:
    return SequenceMatcher(None, _norm(heard), _norm(text)).ratio()


def verdict(heard: str, shown: str, others: dict[str, str]) -> tuple[str, str | None, dict[str, float]]:
    """(match, crossed or unclear; the best other context; the scores) for one transcript."""
    scores = {"shown": score(heard, shown), **{where: score(heard, text) for where, text in others.items()}}
    best = max(others, key=scores.get, default=None)
    if best and scores[best] >= scores["shown"] + CROSSED_MARGIN:
        return "crossed", best, scores
    return ("match" if scores["shown"] >= 0.6 else "unclear"), best, scores


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, help="transcribe at most this many new clips (a pilot run)")
    parser.add_argument("--ids", help="only these voice ids, comma separated")
    args = parser.parse_args()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper is not installed: uv pip install faster-whisper==1.1.1", file=sys.stderr)
        return 2
    from wozmod.formats.audio import _read_at
    from wozmod.text.contexts import World, build_text_stage

    game, donors = Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"])
    out = checkout.work() / "asr"
    cache = out / "transcripts" / hashlib.sha256(json.dumps(SETTINGS, sort_keys=True).encode()).hexdigest()[:16]
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as stage:
        world = World(game, donors, build_text_stage(game, donors, Path(stage)), checkout.REPO / "data/text")
        found = world.contexts("english")
    by_id = {c.id: c for c in found}
    by_key: dict[bytes | None, list] = {}
    for context in found:
        by_key.setdefault(context.key, []).append(context)
    wanted = {int(i) for i in args.ids.split(",")} if args.ids else None
    model = WhisperModel(SETTINGS["model"], compute_type=SETTINGS["compute_type"], revision=SETTINGS["revision"])
    verdicts, new = {}, 0
    for context in found:
        if not context.voice or (wanted and context.voice_id not in wanted) or str(context.voice_id) in verdicts:
            continue
        records, bank = world.banks[context.voice[0]]
        with bank.open("rb") as stream:
            riff = _read_at(stream, *records[context.voice[1]])
        digest = hashlib.sha256(riff).hexdigest()
        cached = cache / f"{digest}.txt"
        if not cached.exists():
            if args.limit is not None and new >= args.limit:
                continue
            with tempfile.NamedTemporaryFile(suffix=".wav") as clip:
                clip.write(riff)
                clip.flush()
                segments, _ = model.transcribe(clip.name, **{k: v for k, v in SETTINGS.items()
                                                             if k not in ("model", "revision", "compute_type")})
                cached.write_text(" ".join(s.text for s in segments), encoding="utf-8")
            new += 1
        others = {o.id: o.text for o in by_key.get(context.key, []) if o is not context}
        others |= {n: by_id[n].text for n in context.neighbours if n in by_id}
        kind, best, scores = verdict(cached.read_text(encoding="utf-8"), context.text, others)
        verdicts[str(context.voice_id)] = {"clip_sha256": digest, "verdict": kind, "best": best, "scores": scores,
                                           "contexts": [c.id for c in found if c.voice_id == context.voice_id]}
    (out / "verdicts.json").write_text(json.dumps({"settings": SETTINGS, "verdicts": verdicts}, indent=1))
    print(f"{len(verdicts)} verdicts ({new} new transcripts) in {out / 'verdicts.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
