"""Heard assertions: what a check screen's recording (CheckDriver.listen) says about the voices the game played.

A listen gives a `record` WAV, its growth journal (game_harness.watch_growth) and the screen polls made while it
ran, each with its wall-clock time and the event captions then drawn. The assertions put both on one clock:

- `lines_in_english`: each event caption is spoken in English (Whisper's language over the stretch from that caption
  to the next) by the installed English take of its line (found in the recording by normalised correlation), which
  the game starts within `max_lag` seconds of where it starts the line's stock clip, measured the same way in the
  scene replayed with stock voices (listen's `stock` pass). Measured so, every take and stock clip lands 0.08-0.22 s
  after its caption, and each take -0.06 to +0.06 s from its own stock clip (runs 2026-11-16_0109-rf-dream and
  _1054-rf2-dream, 41 lines each): the lag behind the caption is the harness's audio path (the game's DirectSound
  queue through Wine and PipeWire, 67.6 ms in the lip clock's measure, then the recorder) and the caption poll, not
  the takes.
- `movie_voice`: the movie the game opened is the file the voice setting picks (wozmod/movie/voice/ for English, the
  stock ./movie/ for Japanese) and its speech is in that language.

No game text is stored: a caption's words come from the run's own screen, a take from the run's own Voice.bin.
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy
from screen_checks import Check

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

CAPTION_CALLERS = ("0x49dace", "0x49db4c")  # an event caption's draws (runtime/english_runtime.c event_line_key)
RATE = 48_000  # the `record` format: float32 stereo
SCAN_RATE = 8_000  # the rate takes are found at: 0.125 ms steps, far inside the 0.1 s asked
DREAM_VOICES = range(0xAF3 + 241, 0xAF3 + 336)  # the Z Dream scenes' voice ids (text.contexts.event_voice)
FOUND = 0.3  # least normalised correlation of a played take: 0.57-0.74 over the music (run 2026-11-15_2151-levels-408)
NEAR = 1.0  # seconds from a caption's start within which its take is looked for
ASR = Path(__file__).resolve().parent / "asr_check.py"


# ---- the recording's clock --------------------------------------------------

def wav_data_offset(head: bytes) -> int:
    """Where a RIFF WAV's sample data starts (the `data` chunk's payload)."""
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise ValueError("not a RIFF WAVE file")
    at = 12
    while at + 8 <= len(head):
        tag, size = head[at:at + 4], struct.unpack_from("<I", head, at + 4)[0]
        if tag == b"data":
            return at + 8
        at += 8 + size + (size & 1)
    raise ValueError("no data chunk in the WAV's head")


def recording_start(growth: list[list[float]], data_offset: int, frame_bytes: int = 8, rate: int = RATE) -> float:
    """The wall-clock time of a recording's first sample: each journalled (time, size) row bounds it from above by
    time - samples written / rate, as a block is written only after it was captured; the tightest bound is kept."""
    bounds = [t - (size - data_offset) / frame_bytes / rate for t, size in growth if size > data_offset]
    if not bounds:
        raise ValueError("the recording never grew past its header")
    return min(bounds)


# ---- captions from the screen polls -----------------------------------------

def caption_spans(polls: list[list], end: float) -> list[dict]:
    """Each caption shown during the polls, in order: its text (draws top to bottom), `start` the midpoint between
    the last poll without it and the first with it, `gap` that interval (the start's uncertainty), and `end` where
    the next caption or a poll without one began. A poll is [time, [[y, x, text], ...]]."""
    spans, current, before = [], None, None
    for t, draws in polls:
        text = " ".join(d[2] for d in sorted(draws)).strip()
        if text != (current["text"] if current else ""):
            if current:
                current["end"] = t
                spans.append(current)
            current = None
            if text:
                gap = t - before if before is not None else 0.0
                current = {"text": text, "start": t - gap / 2, "gap": round(gap, 3)}
        before = t
    if current:
        current["end"] = end
        spans.append(current)
    return spans


# ---- takes ------------------------------------------------------------------

def decode(data: bytes, suffix: str, rate: int = SCAN_RATE) -> numpy.ndarray:
    """Mono float samples of an audio file's bytes at rate, through ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(data)
        f.flush()
        out = subprocess.run(["ffmpeg", "-v", "error", "-i", f.name, "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
    return numpy.frombuffer(out, numpy.float32).astype(numpy.float64)


def stock_takes(game: Path, ids) -> dict[int, bytes]:
    """The Ogg stream of each voice id in the game folder's stock Voice.bin, found through its voice index."""
    from wozmod.formats.audio import kovs_ogg, stock_record
    from wozmod.formats.linkdata import archive, decoded_member
    from wozmod.install.stock import VOICE_INDEX

    index = decoded_member(archive(game / "LINKDATA1")[VOICE_INDEX])
    with (game / "data/Sound/Voice/Voice.bin").open("rb") as bank:
        return {vid: kovs_ogg(record) for vid in ids if (record := stock_record(bank, index, vid))}


def bank_takes(bank: bytes, ids) -> dict[int, bytes]:
    """The Ogg stream of each voice id in an English Voice.bin (formats.audio.english_voice_bank) that has one."""
    from wozmod.formats.audio import kovs_ogg

    count = struct.unpack_from("<I", bank)[0]
    takes = {}
    for vid in ids:
        if vid < count:
            at, size = struct.unpack_from("<II", bank, 4 + 8 * vid)
            if size:
                takes[vid] = kovs_ogg(bank[at:at + size])
    return takes


def find(recording: numpy.ndarray, take: numpy.ndarray) -> tuple[float, float]:
    """(seconds into the recording where take plays, normalised correlation there), both at SCAN_RATE."""
    n = len(recording) + len(take)
    size = 1 << (n - 1).bit_length()
    corr = numpy.fft.irfft(numpy.fft.rfft(recording, size) * numpy.conj(numpy.fft.rfft(take, size)), size)
    lags = len(recording) - len(take) + 1
    if lags <= 0:
        return 0.0, 0.0
    energy = numpy.concatenate([[0.0], numpy.cumsum(recording ** 2)])
    window = numpy.sqrt(numpy.maximum(energy[len(take):len(take) + lags] - energy[:lags], 1e-12))
    score = corr[:lags] / (window * numpy.sqrt(numpy.sum(take ** 2)) + 1e-12)
    best = int(numpy.argmax(score))
    return best / SCAN_RATE, float(score[best])


# ---- Whisper ----------------------------------------------------------------

def hear(wav: Path, clips: list[dict], model: str = "medium") -> list[dict]:
    """asr_check.py over many stretches of one recording, one model load: each clip's transcript and language."""
    with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
        json.dump(clips, f)
        f.flush()
        out = subprocess.run(["nice", "uv", "run", "-q", str(ASR), str(wav), "--clips", f.name, "--model", model],
                             capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"asr_check failed: {out.stderr.strip()[-400:]}")
    return json.loads(out.stdout)


# ---- assertions -------------------------------------------------------------

def _voice_mode(settings: str) -> str:
    found = re.search(r"^\[language\][^\[]*?^voice=(\w+)", settings, re.M | re.S)
    return found.group(1).lower() if found else "english"  # the runtime's default


def placed(heard: dict, takes: dict[int, bytes]) -> tuple[list[dict], float, list[dict]]:
    """(caption spans, the recording's start on their clock, per caption: `at` seconds into the recording, `gap`,
    and the voice id, correlation and `lag` behind the caption of the nearest take played, when one is)."""
    wav = Path(heard["wav"])
    start = recording_start(json.loads(Path(heard["growth"]).read_text()), wav_data_offset(wav.read_bytes()[:4096]))
    spans = caption_spans(heard["polls"], heard["end"])
    recording = decode(wav.read_bytes(), ".wav")
    found = [(vid, *find(recording, decode(ogg, ".ogg"))) for vid, ogg in takes.items()]
    played = sorted((at, vid, corr) for vid, at, corr in found if corr >= FOUND)
    lines, used = [], set()
    for span in spans:
        at = span["start"] - start
        line = {"at": round(at, 2), "gap": span["gap"]}
        near = [(abs(t - at), vid, t, corr) for t, vid, corr in played if vid not in used and abs(t - at) <= NEAR]
        if near:
            _, vid, t, corr = min(near)
            used.add(vid)
            line.update({"voice": f"0x{vid:x}", "corr": round(corr, 2), "lag": round(t - at, 3)})
        lines.append(line)
    return spans, start, lines


def lines_in_english(heard: dict, settings: str, *, max_lag: float = 0.1, require: list[str] = (),
                     listen=hear) -> Check:
    """Every caption of the event scene is spoken in English by its own take, which the game starts within max_lag
    seconds of where it starts the same line's stock clip (heard["stock"], the scene replayed with stock voices);
    `require` names voice ids (hex) that must be among the lines found."""
    if "stock" not in heard:
        return Check(False, {"error": "no stock pass to measure the stock clips' start against: listen with stock"})
    for h in (heard, heard["stock"]):
        if any(draws for _, draws in h["polls"][-1:]):  # its last line would be cut, or never recorded
            return Check(False, {"error": f"{Path(h['wav']).name} ended while a caption still showed: listen longer"})
    bank = Path(heard["bank"]).read_bytes()
    spans, start, lines = placed(heard, bank_takes(bank, DREAM_VOICES))
    if not spans:
        return Check(False, {"error": "no event caption drawn while the recording ran"})
    stock = {line["voice"]: line["lag"] for line in placed(heard["stock"], stock_takes(Path(heard["game"]),
                                                                                       DREAM_VOICES))[2]
             if "voice" in line}
    said = listen(Path(heard["wav"]), [{"start": max(s["start"] - start, 0.0), "end": s["end"] - start,
                                        "expect": s["text"]} for s in spans])
    for line, asr in zip(lines, said, strict=True):
        line.update({"language": asr["language"], "language_p": asr["language_probability"],
                     "subtitle_match": asr["match"]})
        if "voice" in line:
            line["stock_lag"] = stock.get(line["voice"])
        line["ok"] = (line.get("stock_lag") is not None and asr["language"] == "en"
                      and abs(line["lag"] - line["stock_lag"]) <= max_lag)
    found = {line.get("voice") for line in lines if line["ok"]}
    missing = [v for v in require if f"0x{int(v, 16):x}" not in found]
    later = [line["lag"] - line["stock_lag"] for line in lines if line.get("stock_lag") is not None]
    return Check(all(line["ok"] for line in lines) and not missing,
                 {"lines": len(lines), "english": sum(line["language"] == "en" for line in lines),
                  "stock_lines": len(stock), "worst_later": round(max(later, key=abs), 3) if later else None,
                  "missing": missing, "per_line": lines})


def movie_voice(heard: dict, settings: str, *, movie: int, listen=hear) -> Check:
    """The movie opened while the recording ran is the one the voice setting picks, and its speech is in that
    language: wozmod/movie/voice/movieNNN.wmv and English, or the stock ./movie/movieNNN.wmv and Japanese."""


    mode = _voice_mode(settings)
    want_file = f"./wozmod/movie/voice/movie{movie:03d}.wmv" if mode == "english" else f"./movie/movie{movie:03d}.wmv"
    opened = re.findall(rf"^(\S*movie{movie:03d}\.wmv)\r?$", heard["log"], re.M)
    said = listen(Path(heard["wav"]), [{"start": 0.0, "end": None}])[0]
    want_language = "en" if mode == "english" else "ja"
    return Check(opened == [want_file] and said["language"] == want_language,
                 {"voice_setting": mode, "opened": opened, "want": want_file, "language": said["language"],
                  "language_p": said["language_probability"], "words": said["words"]})
