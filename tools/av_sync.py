"""Put a harness video on the game's own clock: each frame at the time the game presented it, the sound at the time it
reached the run's sink.

Gamescope's screencast reaches GStreamer about 73 ms after the game's Present (composite and transport; its buffers
carry no time of their own), and `pulsesrc do-timestamp` stamped sound about 6 ms before the sink, so a recording
stamped on arrival showed the picture about 80 ms late against the sound (work/handbacks/lip-av-offset.md). The
`--test` runtime's A/V probe (test op 30) paints each Present's number into the top-left corner and logs the QPC when
each Present returns (`P n before after` rows in the game folder's lipav.log); Wine's QPC is CLOCK_MONOTONIC_RAW in
100 ns units. The sound is read from the sink's monitor with that clock read at each read.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

CELL = 8  # px, english_runtime.c AV_CELL
CELLS = 14  # white, 12 bits low first, black
RATE = 48000
FRAME_BYTES = 8  # float32 stereo
JUMP = 120  # Presents a recording may skip between two frames (2 s at 60 fps)
QUANTUM = 480  # frames a read lags the sink by: one cycle of the game's stream (10 ms at 48 kHz)


def present_log(text: str) -> dict[int, int]:
    """Each Present's number and the CLOCK_MONOTONIC_RAW ns when it returned, from lipav.log's P rows."""
    out = {}
    for line in text.splitlines():
        words = line.split()
        if len(words) == 4 and words[0] == "P":
            out[int(words[1])] = int(words[3], 16) * 100
    return out


def corner_code(row: bytes, width: int) -> int:
    """The 12-bit Present number painted in a frame's top cell row (grey pixels of the row through the cells' middle),
    or -1 when its white and black reference cells do not read."""
    lum = [row[i * CELL + CELL // 2] for i in range(CELLS)] if width >= CELLS * CELL else []
    if not lum or lum[0] < 160 or lum[-1] > 90:
        return -1
    return sum(1 << (i - 1) for i in range(1, CELLS - 1) if lum[i] > 128)


def frame_presents(codes: list[int], presents: dict[int, int]) -> list[int | None]:
    """Each frame's Present number: its 12-bit code taken to the logged Present it names, the one at or after the
    previous frame's (frames come in Present order, some Presents never reach the recording). None where the code
    does not read, names no logged Present, or would skip more than JUMP Presents past the last frame's (a misread
    bit)."""
    first = min(presents, default=0)
    out: list[int | None] = []
    last = None  # the recording starts some seconds into the probe: its first frame may be any logged Present
    for code in codes:
        found = None
        if code >= 0:
            base = first if last is None else last
            n = base + ((code - base) % 4096)  # the first Present at or after `base` with this code
            if n in presents and (last is None or n - last <= JUMP):
                found = last = n
        out.append(found)
    return out


def frame_times(numbers: list[int | None], presents: dict[int, int]) -> tuple[list[int], int]:
    """Each frame's time (ns): its Present's return; a frame whose number does not read takes its neighbours' midpoint
    (or a frame period on from the last). Also the count of frames so filled."""
    period = 16_666_667
    times: list[int | None] = [presents[n] if n is not None else None for n in numbers]
    filled = sum(t is None for t in times)
    known = [i for i, t in enumerate(times) if t is not None]
    if not known:
        raise ValueError("no recorded frame names a logged Present: was the A/V probe on?")
    for i, t in enumerate(times):
        if t is None:
            before = max((k for k in known if k < i), default=None)
            after = min((k for k in known if k > i), default=None)
            if before is not None and after is not None:
                times[i] = times[before] + (times[after] - times[before]) * (i - before) // (after - before)
            elif before is not None:
                times[i] = times[before] + period * (i - before)
            else:
                times[i] = times[after] - period * (after - i)
    return [int(t) for t in times], filled


def sink_start(reads: list[tuple[int, int]]) -> int:
    """CLOCK_MONOTONIC_RAW ns when the first recorded frame reached the sink, from (clock at each read's return, bytes
    so far): the read with the least time to spare bounds it, one cycle late (work/handbacks/lip-premise.md)."""
    if not reads:
        raise ValueError("the sink recording read nothing")
    return min(t - total * 1_000_000_000 // (FRAME_BYTES * RATE) for t, total in reads) + QUANTUM * 1_000_000_000 // RATE


def strip_rows(video: Path) -> tuple[list[bytes], int]:
    """Each frame's grey cell row (the middle row of the corner cells), in recording order."""
    width = CELLS * CELL
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:v:0", "-fps_mode", "passthrough", "-vf",
                          f"format=gray,crop={width}:1:0:{CELL // 2}", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return [raw[i:i + width] for i in range(0, len(raw), width)], width


def retime(video: Path, sink: Path, reads: list[tuple[int, int]], log: Path, out: Path) -> dict:
    """Write `out`: `video`'s frames at their Presents' times, the corner code blanked, and the sink recording (float32
    stereo, kept lossless: FLAC has no encoder delay to shift it) at its sink time, both on one origin. Returns the timing summary also written beside `out`."""
    presents = present_log(log.read_text(errors="replace", encoding="utf-8"))
    rows, width = strip_rows(video)
    numbers = frame_presents([corner_code(r, width) for r in rows], presents)
    times, filled = frame_times(numbers, presents)
    start = sink_start(reads)
    origin = min(times[0], start)
    seen = [n for n in numbers if n is not None]
    summary = {"frames": len(times), "unread_codes": filled, "first_present": seen[0], "last_present": seen[-1],
               "presents_missing": seen[-1] - seen[0] + 1 - len(set(seen)),
               "audio_delay_ms": round((start - origin) / 1e6, 3), "origin_monotonic_raw_ns": origin,
               "frame_presents": numbers}
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        work = Path(tmp)
        stamps = work / "stamps.txt"
        stamps.write_text("# timestamp format v4\n" + "".join(f"{(t - origin) / 1e6:.3f}\n" for t in times), encoding="utf-8")
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:v:0", "-fps_mode", "passthrough", "-vf",
                        f"drawbox=x=0:y=0:w={width}:h={CELL}:color=black:t=fill", "-c:v", "h264_nvenc", "-b:v", "12M",
                        "-bf", "0", str(work / "v.mkv")], check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", str(sink),
                        "-c:a", "flac", "-sample_fmt", "s32", str(work / "a.mka")], check=True, capture_output=True)
        subprocess.run(["mkvmerge", "-q", "-o", str(work / "out.mkv"), "--timestamps", f"0:{stamps}", str(work / "v.mkv"),
                        "--sync", f"0:{round((start - origin) / 1e6)}", str(work / "a.mka")], check=True,
                       capture_output=True)
        shutil.move(work / "out.mkv", out)
    out.with_suffix(".timing.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return summary


def retime_files(stem: Path, log: Path) -> dict:
    """retime() from the files a harness `video` leaves when it could not finish: STEM.raw.mkv, STEM.sink.f32 and
    STEM.sink.json (its reads), into STEM.mkv; the three go once it is made."""
    reads = [tuple(r) for r in json.loads(stem.with_suffix(".sink.json").read_text(encoding="utf-8"))]
    summary = retime(stem.with_suffix(".raw.mkv"), stem.with_suffix(".sink.f32"), reads, log, stem.with_suffix(".mkv"))
    for suffix in (".raw.mkv", ".sink.f32", ".sink.json"):
        stem.with_suffix(suffix).unlink()
    return summary


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        sys.exit("usage: av_sync.py RUN/evidence/NAME RUN/game/lipav.log   (retime a video whose finishing failed)")
    print(json.dumps({k: v for k, v in retime_files(Path(sys.argv[1]), Path(sys.argv[2])).items() if k != "frame_presents"}))

