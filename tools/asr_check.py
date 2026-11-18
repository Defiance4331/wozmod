#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["faster-whisper"]
# ///
"""Does a stretch of audio say a given line? Transcribe it with Whisper and compare, word by word.

    uv run tools/asr_check.py AUDIO [--start S] [--end E] [--expect "the line"] [--model medium.en]

AUDIO is a recording (`record` WAV, `video` MKV) or any file ffmpeg reads. Prints one JSON line: the transcript and,
with --expect, the share of expected words heard in order ("match", 0 to 1) and "pass" (match >= --threshold, 0.8).
Exits 1 on a fail. With --clips (a JSON list of {"start", "end", "expect"?} in seconds, end null for the file's end)
it loads the model once and prints a JSON list: per clip the transcript, the language Whisper hears (a multilingual
model such as medium; an .en model hears only English) and its probability, the word count and the match
(voice_checks.py reads it). Runs on the CPU from the models in ~/.cache/huggingface; the first run of a model downloads it.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def match(heard: str, expected: str) -> float:
    """The share of the expected words the transcript has, in order."""
    want = words(expected)
    if not want:
        return 1.0
    blocks = difflib.SequenceMatcher(None, words(heard), want, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks) / len(want)


def transcribe(audio: Path, start: float | None, end: float | None, model: str) -> str:
    return heard(audio, start, end, load(model), "en")[0]


def load(model: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model, device="cpu", compute_type="int8")


def heard(audio: Path, start: float | None, end: float | None, whisper, language: str | None) -> tuple[str, str, float]:
    """(transcript, language, its probability) of a stretch of audio; language None lets Whisper detect it."""
    import numpy

    with tempfile.TemporaryDirectory() as scratch:
        clip = Path(scratch) / "clip.wav"  # 16 kHz mono, cut to the stretch asked for
        subprocess.run(["ffmpeg", "-v", "error", "-y", *(["-ss", str(start)] if start is not None else []), "-i",
                        str(audio), *(["-t", str(end - (start or 0))] if end is not None else []), "-ac", "1", "-ar",
                        "16000", str(clip)], check=True)
        with wave.open(str(clip)) as stream:  # the samples, not the path: faster-whisper's own file reading calls av
            samples = numpy.frombuffer(stream.readframes(stream.getnframes()), numpy.int16)  # with an option av 15 dropped
        segments, info = whisper.transcribe(
            samples.astype(numpy.float32) / 32768, language=language, beam_size=5, temperature=0.0, vad_filter=True,
            condition_on_previous_text=False)
        text = " ".join(segment.text.strip() for segment in segments)
        return text, info.language, round(info.language_probability, 3)


def clips(audio: Path, rows: list[dict], model: str) -> list[dict]:
    """Each stretch's transcript, the language heard in it and its probability, its word count, and its match."""
    whisper, out = load(model), []
    for row in rows:
        text, language, p = heard(audio, row.get("start"), row.get("end"), whisper, None)
        out.append({"heard": text, "language": language, "language_probability": p, "words": len(words(text)),
                    "match": round(match(text, row["expect"]), 3) if row.get("expect") is not None else None})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--start", type=float, help="seconds into the file")
    parser.add_argument("--end", type=float, help="seconds into the file")
    parser.add_argument("--expect", help="the line the audio should say")
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--model", default="medium.en", help="a faster-whisper model (base.en is quicker)")
    parser.add_argument("--clips", type=Path, help="a JSON list of stretches to hear with one model load")
    args = parser.parse_args(argv)
    if args.clips:
        print(json.dumps(clips(args.audio.resolve(strict=True), json.loads(args.clips.read_text(encoding="utf-8")), args.model)))
        return 0
    try:
        heard = transcribe(args.audio.resolve(strict=True), args.start, args.end, args.model)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"asr_check: {exc}", file=sys.stderr)
        return 1
    result = {"heard": heard}
    if args.expect is not None:
        result["match"] = round(match(heard, args.expect), 3)
        result["pass"] = result["match"] >= args.threshold
    print(json.dumps(result))
    return 0 if result.get("pass", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
