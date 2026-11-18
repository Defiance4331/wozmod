"""The ffmpeg components the installer uses: record them from a real install, and build and check exactly those.

    python3 packaging/ffmpeg/components.py shim DIR --real FFMPEG --log LOG [--keep CORPUS]   # writes DIR/ffmpeg
    PATH=DIR:$PATH uv run wozmod install ...                                # a checkout runs the ffmpeg on PATH
    python3 packaging/ffmpeg/components.py summarize LOG [--write packaging/ffmpeg/components.json]
    python3 packaging/ffmpeg/components.py configure components.json FFMPEG_SOURCE   # build.sh: the --enable flags
    python3 packaging/ffmpeg/components.py check components.json FFMPEG [--runner wine]   # build.sh: the program
    python3 packaging/ffmpeg/components.py replay LOG CORPUS FFMPEG [--runner wine]   # every run again, compared

The shim runs the real ffmpeg with the same arguments, except the log level, which it raises to verbose: the
outputs are the same, and ffmpeg then names each decoder, encoder and filter it ran. It appends one JSON line per
run to LOG: the arguments, the exit code and ffmpeg's verbose log. `summarize` reads back what every run used, by
the names ffmpeg itself uses (codec s16le, decoder atrac3plus). `configure` turns each name into the component
configure enables (pcm_s16le, atrac3p) from the FFmpeg source, and `check` asks the built program for each name,
so a component configure silently did not enable fails the build.

With --keep, the shim also stores each run's input files in CORPUS under their SHA-256 and records the SHA-256 of
each output. `replay` then runs every recorded command again with another ffmpeg (another build, or the Windows one
under Wine) and names each run whose outputs differ from the recorded ones: a byte-for-byte comparison of two
ffmpeg builds over everything an install converts, without a second install.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KINDS = ("demuxer", "muxer", "decoder", "encoder", "filter", "protocol", "library")
# Filters the CLI puts in every audio filter graph, which the verbose log names only by instance (format_out_#0:0).
# Its source and sink (abuffer, abuffersink) are part of libavfilter, not components.
GRAPH_FILTERS = ("aformat", "anull")
SWITCHES = {"-hide_banner", "-nostdin", "-y", "-n"}  # the ffmpeg options that take no value
FILTER_OPTIONS = {"-af", "-filter:a", "-filter_complex", "-lavfi"}
# Where each kind of component is defined, and how: `const FFCodec ff_atrac3p_decoder = { .p.name = "atrac3plus"`.
SOURCES = {"decoder": "libavcodec", "encoder": "libavcodec", "demuxer": "libavformat", "muxer": "libavformat",
           "filter": "libavfilter", "protocol": "libavformat"}
DEFINITION = re.compile(r"\bff_(\w+?)_(decoder|encoder|demuxer|muxer|protocol)\s*=\s*\{(.*?)\n\};", re.S)
NAME = re.compile(r'\.(?:p\.)?name\s*=\s*"([^"]+)"')


# --- recording


def shim(folder: Path, real: Path, log: Path, keep: Path | None = None) -> Path:
    """Write folder/ffmpeg, which runs `real` and records each run in log (and its files in keep); rerunning
    rewrites it."""
    folder.mkdir(parents=True, exist_ok=True)
    program = folder / "ffmpeg"
    corpus = f" --keep {keep.resolve()}" if keep else ""
    program.write_text(f"#!/bin/sh\nexec {sys.executable} -I {Path(__file__).resolve()} run "
                       f"--real {real.resolve()} --log {log.resolve()}{corpus} -- \"$@\"\n", encoding="utf-8")
    program.chmod(0o755)
    return program


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _keep(path: Path, corpus: Path) -> str:
    digest = _digest(path)
    if not (corpus / digest).is_file():
        temporary = corpus / f".{digest}.{os.getpid()}"
        temporary.write_bytes(path.read_bytes())
        os.replace(temporary, corpus / digest)  # whole or absent: parallel runs keep the same clip
    return digest


def run(real: Path, log: Path, args: list[str], keep: Path | None = None) -> int:
    verbose = list(args)
    for flag in ("-loglevel", "-v"):
        if flag in verbose:
            verbose[verbose.index(flag) + 1] = "verbose"
    inputs, outputs = _paths(args)
    if keep:
        keep.mkdir(parents=True, exist_ok=True)
        inputs = [_keep(Path(p), keep) for p in inputs]
    done = subprocess.run([str(real), *verbose], stdin=subprocess.DEVNULL, capture_output=True)
    record = {"args": args, "code": done.returncode, "log": done.stderr.decode("utf-8", "replace")}
    if keep and not done.returncode:
        record |= {"inputs": inputs, "outputs": [hashlib.sha256(done.stdout).hexdigest() if p == "-" else _digest(Path(p))
                                                 for p in outputs]}  # "-": what the run wrote to its caller
    record = json.dumps(record)
    descriptor = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(descriptor, (record + "\n").encode())  # one write per run: parallel runs never interleave a line
    finally:
        os.close(descriptor)
    sys.stdout.buffer.write(done.stdout)
    sys.stderr.buffer.write(done.stderr)
    return done.returncode


def _scan(args: list[str]) -> list[tuple[str, int, str | None]]:
    """("input" or "output", the index of its path in args, the -f given before it) for each input and output."""
    found, pending, k = [], None, 0
    while k < len(args):
        if args[k] == "-f":
            pending, k = args[k + 1], k + 2
        elif args[k] == "-i":
            found.append(("input", k + 1, pending))
            pending, k = None, k + 2
        elif args[k].startswith("-") and args[k] != "-":
            k += 1 if args[k] in SWITCHES else 2
        else:  # a bare word that is not an option's value is an output path; "-" is the pipe to the caller
            found.append(("output", k, pending))
            pending, k = None, k + 1
    return found


def _formats(args: list[str]) -> tuple[list[str | None], list[str | None]]:
    """The -f given before each -i (the inputs' formats) and before each output path (the outputs')."""
    scanned = _scan(args)
    return [f for side, _, f in scanned if side == "input"], [f for side, _, f in scanned if side == "output"]


def _paths(args: list[str]) -> tuple[list[str], list[str]]:
    scanned = _scan(args)
    return [args[k] for side, k, _ in scanned if side == "input"], [args[k] for side, k, _ in scanned if side == "output"]


def replay(log: Path, corpus: Path, program: list[str], jobs: int | None = None
           ) -> tuple[list[list[str] | None], list[str]]:
    """Run every kept run of log again with program, its inputs from corpus: (the SHA-256 of each run's outputs,
    None for a run that failed; the runs whose outputs are not the recorded ones)."""
    records = [r for r in map(json.loads, log.read_text(encoding="utf-8").splitlines()) if "outputs" in r]

    wine = Path(program[0]).name.startswith("wine")  # a Windows ffmpeg under Wine finds / as drive Z:

    def again(numbered: tuple[int, dict]) -> tuple[list[str] | None, str | None]:
        number, record = numbered
        args = list(record["args"])
        with tempfile.TemporaryDirectory(prefix="ffmpeg-replay-") as folder:
            inputs, outputs = [], []
            for k, (side, at, _) in enumerate(_scan(args)):
                path = Path(folder, f"{k}.{side}")
                args[at] = f"Z:{path}" if wine else str(path)
                (inputs if side == "input" else outputs).append(path)
            for path, digest in zip(inputs, record["inputs"], strict=True):
                path.write_bytes((corpus / digest).read_bytes())
            done = subprocess.run([*program, *args], stdin=subprocess.DEVNULL, capture_output=True)
            if done.returncode:
                return None, f"run {number}: exit {done.returncode}: {done.stderr.decode(errors='replace')[-300:]}"
            got = [_digest(path) for path in outputs]
        if got != record["outputs"]:
            differ = [k for k, (a, b) in enumerate(zip(got, record["outputs"], strict=True)) if a != b]
            return got, f"run {number}: outputs {differ} differ"
        return got, None

    with ThreadPoolExecutor(jobs or os.cpu_count()) as pool:
        results = list(pool.map(again, enumerate(records)))
    return [got for got, _ in results], [wrong for _, wrong in results if wrong]


def used(records: list[dict]) -> dict[str, list[str]]:
    """What the recorded runs used, by kind, under the names ffmpeg gives them at run time."""
    found = {kind: set() for kind in KINDS}
    found["filter"].update(GRAPH_FILTERS)
    for record in records:
        if record["code"]:
            raise SystemExit(f"a recorded run failed ({record['code']}): {' '.join(record['args'])}")
        args, log = record["args"], record["log"]
        # The format of each input and output, as ffmpeg names it in "Input #0, asf, from" (it may have found it by
        # itself) or, when the log does not say, as -f gave it.
        logged = {side: re.findall(rf"^{side} #\d+, ([\w,]+), (?:from|to) ", log, re.M) for side in ("Input", "Output")}
        demuxers, muxers = _formats(args)
        for kind, given, seen in (("demuxer", demuxers, logged["Input"]), ("muxer", muxers, logged["Output"])):
            names = seen if len(seen) == len(given) else given
            if None in names:
                raise SystemExit(f"a run's {kind} is neither in its log nor given with -f: {args}")
            found[kind].update(name.split(",")[0] for name in names)
        inputs, outputs = _paths(args)
        found["protocol"].update("pipe" if p == "-" or p.startswith("pipe:") else "file" for p in [*inputs, *outputs])
        # "Stream #0:0 -> #0:0 (xma2 (native) -> vorbis (libvorbis))": each side is a codec and the coder that ran
        # it, which is ffmpeg's own coder of that name when it says native.
        for mapping in re.findall(r"-> #\d+:\d+ \((\S+ \(\S+\)) -> (\S+ \(\S+\))\)", log):
            for kind, side in zip(("decoder", "encoder"), mapping, strict=True):
                codec, coder = re.fullmatch(r"(\S+) \((\S+)\)", side).groups()
                found[kind].add(codec if coder == "native" else coder)
                if coder.startswith("lib"):  # an outside library's coder (libvorbis) needs that library
                    found["library"].add(coder)
        found["filter"].update(re.findall(r"\[(?:Parsed|auto)_([a-z0-9]+)_\d+ @", log))
        for k, arg in enumerate(args[:-1]):  # a filter that logs nothing at verbose (asetpts) is still in its chain
            if arg in FILTER_OPTIONS:
                found["filter"].update(re.findall(r"(?:^|[,;\]])\s*([a-z0-9_]+)(?=[=,;@\[]|$)", args[k + 1]))
                if re.search(r"resampler=soxr", args[k + 1]):  # aresample's SoX resampler is libsoxr
                    found["library"].add("libsoxr")
    return {kind: sorted(names) for kind, names in found.items()}


def summarize(log: Path) -> dict:
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
    unparsed = sorted({m for r in records for m in re.findall(r"parser not found for codec (\w+)", r["log"])})
    return {"runs": len(records), "used": used(records), "codecs without a parser": unparsed}


# --- building


def _listed(source: Path, kind: str) -> set[str]:
    """The components of a kind that this FFmpeg source's configure knows."""
    listing = subprocess.run(["./configure", f"--list-{kind}s"], cwd=source, capture_output=True, text=True,
                             check=True, encoding="utf-8").stdout
    return set(listing.split())


def components(names: dict[str, list[str]], source: Path) -> dict[str, list[str]]:
    """The configure component of each run-time name: the definition that names it, else the component of that
    name or (a raw PCM format, s16le) pcm_<name>; exactly one must exist."""
    kinds = [kind for kind in KINDS if kind != "library"]  # a library is enabled by its own name (configure_flags)
    defined = {kind: {} for kind in kinds}
    for kind in kinds:
        for path in (source / SOURCES[kind]).glob("*.c"):
            for component, found_kind, body in DEFINITION.findall(path.read_text(errors="replace", encoding="utf-8")):
                if found_kind == kind and (name := NAME.search(body)):
                    defined[kind].setdefault(name.group(1), set()).add(component)
    result = {}
    for kind in kinds:
        known = _listed(source, kind)
        result[kind] = []
        for name in names.get(kind, []):
            candidates = defined[kind].get(name) or {name, f"pcm_{name}"}
            matches = sorted(candidates & known)
            if len(matches) != 1:
                raise SystemExit(f"{kind} {name}: configure knows {matches or 'none'} of {sorted(candidates)}")
            result[kind] += matches
    return result


def configure_flags(names: dict[str, list[str]], source: Path) -> list[str]:
    flags = [f"--enable-{kind}={c}" for kind, found in components(names, source).items() for c in found]
    return [*flags, *(f"--enable-{library}" for library in names.get("library", []))]


def missing(names: dict[str, list[str]], program: list[str]) -> list[str]:
    """The names the built program (a command: [path], or [wine, path]) does not list (ffmpeg -decoders and so on)."""
    absent = []
    for kind, wanted in names.items():
        if kind == "library":  # the configure line the program keeps names each library it was built with
            built = subprocess.run([*program, "-hide_banner", "-buildconf"], capture_output=True, text=True,
                                   check=True, encoding="utf-8").stdout
            absent += [f"library {name}" for name in wanted if f"--enable-{name}" not in built]
            continue
        listing = subprocess.run([*program, "-hide_banner", f"-{kind}s"], capture_output=True, text=True,
                                 check=True, encoding="utf-8").stdout
        words = set(re.findall(r"[\w.]+", listing))
        absent += [f"{kind} {name}" for name in wanted if name not in words]
    return absent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    make, run_parser = commands.add_parser("shim"), commands.add_parser("run")
    make.add_argument("folder", type=Path)
    for each in (make, run_parser):
        each.add_argument("--real", type=Path, required=True)
        each.add_argument("--log", type=Path, required=True)
        each.add_argument("--keep", type=Path, help="also keep each run's input files here, by SHA-256")
    run_parser.add_argument("args", nargs=argparse.REMAINDER)
    read = commands.add_parser("summarize")
    read.add_argument("log", type=Path)
    read.add_argument("--write", type=Path, help="also write what the runs used to this file")
    for name in ("configure", "check"):
        each = commands.add_parser(name)
        each.add_argument("names", type=Path, help="components.json")
        each.add_argument("path", type=Path, help="the FFmpeg source (configure) or the built program (check)")
        if name == "check":
            each.add_argument("--runner", help="the program that runs it, such as wine for the Windows build")
    again = commands.add_parser("replay")
    again.add_argument("log", type=Path)
    again.add_argument("corpus", type=Path)
    again.add_argument("program", type=Path)
    again.add_argument("--runner", help="the program that runs it, such as wine for the Windows build")
    again.add_argument("--jobs", type=int)
    again.add_argument("--write", type=Path, help="also write each run's output SHA-256s here, to compare two builds")
    args = parser.parse_args()
    if args.command == "shim":
        print(shim(args.folder, args.real, args.log, args.keep))
    elif args.command == "run":
        return run(args.real, args.log, args.args[1:] if args.args[:1] == ["--"] else args.args, args.keep)
    elif args.command == "replay":
        hashes, wrong = replay(args.log, args.corpus, [*filter(None, [args.runner]), str(args.program)], args.jobs)
        if args.write:
            args.write.write_text(json.dumps(hashes) + "\n", encoding="utf-8")
        print(f"{len(hashes)} runs replayed, {len(wrong)} with other outputs than recorded")
        print("\n".join(wrong[:30]))
        return 1 if wrong else 0
    elif args.command == "summarize":
        summary = summarize(args.log)
        if args.write:
            args.write.write_text(json.dumps(summary["used"], indent=1) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=1))
    elif args.command == "configure":
        print("\n".join(configure_flags(json.loads(args.names.read_text(encoding="utf-8")), args.path)))
    else:
        if absent := missing(json.loads(args.names.read_text(encoding="utf-8")), [*filter(None, [args.runner]), str(args.path)]):
            raise SystemExit(f"{args.path} lacks: {', '.join(absent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
