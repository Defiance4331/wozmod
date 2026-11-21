"""Record which pieces of the stock game files the installer's build reads, into data/stock_pieces.json.

    uv run tools/stock_pieces.py            # inputs from [tests] in work/harness.toml: WOZMOD_GAME, WOZMOD_DONORS,
                                            # WOZMOD_LETTERING, WOZMOD_SPEECH; writes data/stock_pieces.json
    uv run tools/stock_pieces.py --check    # exits 1 when a fresh record differs from the committed one

The three build steps (text, voices, art) run as an install runs them (install/steps.run: side by side, each in a
worker of its own), with the read gate (install/gate.py) in record mode. The steps read the game only through
wozmod.game_files, which announces each piece it serves: a span of the exe or of another file (the Dream movies, read
whole), a LINKDATA entry unpacked or as stored, a Voice.bin clip. The gate writes each to its trace, and any other
read of a game file as a "game" record, which stops this tool. The indexes (the .IDX files, the voice index LINKDATA1
4058) only locate pieces, so they are not pieces. Of the text step's scan of every NUL-ended exe string, only the
strings a translation row is looked up by are pieces (game_files.Strings).

The reads become pieces here: the spans of each file merged where they touch, each entry and clip with the steps that
read it. An entry every step reads only unpacked also passes the installer's check when its unpacked bytes match. The
served pieces are read off the build's output: wozmod/entries, wozmod/captions and the clips of wozmod/voice/Voice.bin.

The read gate holds every build from source to this list (a read of a piece it leaves out stops the build), and
tests/test_stock_pieces.py (WOZMOD_STOCK_PIECES=1) checks it against what the reference builds read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import tempfile
import time
from pathlib import Path

import checkout

REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / "data/stock_pieces.json"
ABOUT = ("Written by tools/stock_pieces.py: hashes and locations only. The pieces of the stock game files the "
         "installer's build steps read (and the ones wozmod serves its own in place of), which install/stock.py checks.")


def run_steps(inputs, scratch: Path, mode: str | None = None, trace: Path | None = None) -> Path:
    """Run the installer's three build steps on inputs (steps.Inputs) as an install runs them (steps.run) into
    scratch/out, laid out as the game folder, with the read gate in mode (None: the default, enforce from source) and
    its trace; return scratch/out. What the steps print goes to scratch/build.log."""
    from wozmod.install import steps
    from wozmod.progress import Progress

    out = scratch / "out"
    out.mkdir(parents=True, exist_ok=True)
    work = {step: inputs.arguments(scratch / step, out) for step in steps.STEPS}
    started = time.monotonic()
    steps.run(work, Progress(), scratch / "build.log", mode=mode, trace=trace)
    print(f"steps: {time.monotonic() - started:.0f} s", file=sys.stderr, flush=True)
    return out


# ---------------------------------------------------------------- from reads to pieces

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_pin(path: Path) -> list:
    with path.open("rb") as stream:
        return [path.stat().st_size, hashlib.file_digest(stream, "sha256").hexdigest()]


def stock_layout(game: Path):
    """The stock indexes, and each Voice.bin clip's (offset, size) by clip number."""
    from wozmod.formats.linkdata import archive, decoded_member, parse_index
    from wozmod.game_files import ARCHIVES, VOICE_INDEX, clip_span

    indexes = {name: parse_index((game / f"{name}.IDX").read_bytes(), (game / f"{name}.BIN").stat().st_size)
               for name in ARCHIVES}
    table = decoded_member(archive(game / "LINKDATA1")[VOICE_INDEX])
    clips = {clip: clip_span(table, clip) for clip in range((len(table) - 8) // 8)}
    return indexes, clips


def _merged(spans) -> list[tuple[int, int, set[str]]]:
    """(start, end, step) spans joined where they touch or overlap, each with the steps that read it."""
    merged: list[tuple[int, int, set[str]]] = []
    for start, end, step in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), merged[-1][2] | {step})
        else:
            merged.append((start, end, {step}))
    return merged


def served_pieces(outs: list[Path]) -> tuple[dict[str, set[int]], set[int]]:
    """The LINKDATA entries and Voice.bin clips the build's output (in one folder or in a folder per step) serves
    wozmod's own in place of."""
    from wozmod.formats.audio import MOZ_VOICE_IDS
    from wozmod.formats.linkdata import LINKDATA2_FIRST

    entries: dict[str, set[int]] = {"LINKDATA1": set(), "LINKDATA2": set()}
    for out, folder in ((out, folder) for out in outs for folder in ("entries", "captions", "captions/voice")):
        for path in (out / "wozmod" / folder).glob("*.bin"):
            key = int(path.stem)
            if key < LINKDATA2_FIRST:
                entries["LINKDATA1"].add(key)
            else:
                entries["LINKDATA2"].add(key - LINKDATA2_FIRST)
    bank, = (path.read_bytes() for out in outs if (path := out / "wozmod/voice/Voice.bin").is_file())
    # Ids past the stock range are a stage line's own slot (runtime/voice_slots.h): they stand in for no stock clip.
    count = min(struct.unpack_from("<I", bank)[0], MOZ_VOICE_IDS)
    clips = {clip for clip in range(count) if struct.unpack_from("<II", bank, 4 + 8 * clip)[1]}
    return entries, clips


def pieces_document(game: Path, records: list[dict], outs: list[Path]) -> dict:
    """data/stock_pieces.json from the read gate's records of the steps' builds (gate.records) and their output."""
    from wozmod.formats import pe
    from wozmod.install.stock import ARCHIVES, EXE, NEEDED, STEPS, VOICE, _decoded

    spans: dict[str, list[tuple[int, int, str]]] = {}  # file -> (start, end, step)
    entries: dict[str, dict[int, tuple[set[str], set[str]]]] = {name: {} for name in ARCHIVES}  # -> (steps, forms)
    voices: dict[int, set[str]] = {}
    for record in records:
        if record["kind"] != "stock":
            continue
        name, at, end, form = record["piece"]
        if form == "span":
            spans.setdefault(name, []).append((at, end, record["step"]))
        elif form == "clip":
            voices.setdefault(at, set()).add(record["step"])
        else:
            readers, forms = entries[name].setdefault(at, (set(), set()))
            readers.add(record["step"])
            forms.add(form)
    indexes, clips = stock_layout(game)
    exe = (game / EXE).read_bytes()
    document = {"format": 1, "about": ABOUT, "stamp": list(pe.build_stamp(exe[:0x1000])),
                "recorded_from": {name: _file_pin(game / name) for name in NEEDED}, "pieces": {}}
    order = lambda steps: " ".join(s for s in STEPS if s in steps)  # noqa: E731
    document["pieces"][EXE] = [[start, end - start, _sha(exe[start:end]), order(steps)]
                               for start, end, steps in _merged(spans.get(EXE, ()))]
    served, served_clips = served_pieces(outs)
    for name in ARCHIVES:
        index, rows = indexes[name], []
        with (game / f"{name}.BIN").open("rb") as stream:
            for entry in sorted(set(entries[name]) | served[name]):
                geometry, parts, offset = index.entries[entry], [], index.entries[entry].offset
                for size in geometry.stored_sizes:
                    stream.seek(offset)
                    parts.append(stream.read(size))
                    offset += -(-size // 2048) * 2048
                readers, forms = entries[name].get(entry, (set(), set()))
                decoded = _sha(_decoded(parts)) if forms == {"decoded"} and _decoded(parts) is not None else None
                rows.append([entry, _sha(b"".join(parts)), decoded, order(readers), int(entry in served[name])])
        document["pieces"][name] = rows
    with (game / VOICE).open("rb") as stream:
        rows = []
        for clip in sorted(set(voices) | served_clips):
            offset, size = clips[clip]
            stream.seek(offset)
            rows.append([clip, _sha(stream.read(size)), order(voices.get(clip, ())), int(clip in served_clips)])
    document["pieces"][VOICE] = rows
    for name in sorted(set(spans) - {EXE}):
        data = (game / name).read_bytes()
        document["pieces"][name] = [[start, end - start, _sha(data[start:end]), order(steps)]
                                    for start, end, steps in _merged(spans[name])]
    return document


def dumps(document: dict) -> str:
    """The document with one piece per line, so a change in what the build reads shows as a short diff."""
    head = {k: v for k, v in document.items() if k != "pieces"}
    lines = ["{"] + [f" {json.dumps(k)}: {json.dumps(v)}," for k, v in head.items()] + [' "pieces": {']
    files = list(document["pieces"].items())
    for n, (name, rows) in enumerate(files):
        lines.append(f"  {json.dumps(name)}: [")
        lines += [f"   {json.dumps(row)}{',' if k < len(rows) - 1 else ''}" for k, row in enumerate(rows)]
        lines.append("  ]" + ("," if n < len(files) - 1 else ""))
    return "\n".join(lines + [" }", "}"]) + "\n"


def summary(document: dict) -> str:
    from wozmod.install.stock import STEPS

    lines = []
    for name, rows in document["pieces"].items():
        spans = name not in ("LINKDATA1", "LINKDATA2", "data/Sound/Voice/Voice.bin")
        steps = [row[-1] if spans else row[-2] for row in rows]
        per_step = {step: sum(step in s.split() for s in steps) for step in STEPS}
        unread = sum(not s for s in steps)
        extra = f", {sum(row[1] for row in rows)} bytes" if spans else ""
        lines.append(f"{name}: {len(rows)} pieces{extra}; read by "
                     + ", ".join(f"{step} {n}" for step, n in per_step.items()) + f"; served only {unread}")
    return "\n".join(lines)


def record(inputs, scratch: Path) -> tuple[dict, Path]:
    """Run the steps on inputs (steps.Inputs) with the read gate in record mode; return the pieces document and the
    build's output folder."""
    from wozmod.install import gate

    trace = scratch / "trace.jsonl"
    out = run_steps(inputs, scratch, gate.RECORD, trace)
    records = gate.records(trace)
    if stray := sorted({f"{r['step']}: {r['item']}" for r in records if r["kind"] == "game"}):
        raise RuntimeError("game files read other than through wozmod.game_files:\n" + "\n".join(stray))
    return pieces_document(inputs.game, records, [out]), out


def inputs(args):
    """The steps.Inputs the command line names, each path not given from [tests] in work/harness.toml."""
    from wozmod.install import steps, worker_count

    found = checkout.inputs()

    def given(name: str) -> Path:
        if value := getattr(args, name) or found.get(f"WOZMOD_{name.upper()}"):
            return Path(value)
        raise SystemExit(f"stock_pieces: give --{name} or set WOZMOD_{name.upper()}")

    return steps.Inputs(game=given("game"), donors=given("donors"), speech=given("speech"),
                        separator=given("separator"), lettering=given("lettering"), jobs=worker_count())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--game", type=Path)
    parser.add_argument("--donors", type=Path, help="a donors folder as the installer lays it out")
    parser.add_argument("--lettering", type=Path)
    parser.add_argument("--speech", type=Path, help="the speech model folder")
    parser.add_argument("--separator", type=Path, help="the Dream voice separator's model folder")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="compare with --output instead of writing it")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="stock-pieces-", dir=checkout.work()) as scratch:
        document, _ = record(inputs(args), Path(scratch))
    text = dumps(document)
    print(summary(document))
    if args.check:
        same = args.output.is_file() and args.output.read_text(encoding="utf-8") == text
        print("the record matches" if same else f"{args.output} differs from a fresh record")
        return 0 if same else 1
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
