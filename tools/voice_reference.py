"""Every voiced line under the English voice, what plays and why, for checking a player's report (developer tool).

    uv run --extra native python tools/voice_reference.py build
    uv run python tools/voice_reference.py why stage107.31 | 5562 | "words from the subtitle or the Japanese"

build writes reference/voice-lines.tsv in the main checkout: one row per voiced line of src/wozmod/text/contexts.py
(stage lines, battle quotes, cutscene lines, narration) with what plays under the English voice and the stock and
donor text. It holds game text, so it is git-ignored and never published (decisions ledger, text row 45). why prints
the rows whose context id, voice id or text matches, and says when the file was built from another commit.

Verdicts (decisions ledger, text rows 22 and 46):
- english: a donor take plays. Its number column says whether the take is filed under the stock line's own number
  (same) or another (other: MOZ moved or replaced the line, so its words may differ from the Japanese; dream: a
  Dream stage line, which MOZ numbers anew).
- japanese, moz-original: no donor records the line; the stock take plays under our or the PSP's English.
- japanese, REPLACED: the subtitle is a line a donor records, so the line exists there and should play its English:
  a bug, which tests/test_subtitle_contexts.py (C6) reports.

The after column names the line the stage script speaks just before (stage lines): a line MOZ added sits in an
event of its own, after a line both games speak, so a donor recording of that moment has the earlier line only.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import checkout

REFERENCE = checkout.main_checkout() / "reference/voice-lines.tsv"
COLUMNS = ("context", "kind", "voice_id", "verdict", "take", "number", "after", "row", "japanese", "english",
           "take_says")


def recorded_lines(world) -> dict[str, set[str]]:
    """The words of every line each donor records (World.by_clip), by donor."""
    found = defaultdict(set)
    for lines in world.by_clip.values():
        for line in lines:
            found[line.donor].add(line.text)
    return found


def verdict(context, recorded: dict[str, set[str]]) -> str:
    """english, japanese moz-original or japanese REPLACED for an English-voice context with a voice id."""
    if context.voice is not None:
        return "english"
    row = context.row or {}
    donor = row["ref"][0] if row.get("source") == "edited" else row.get("source")  # an authored row may cite a line
    return f"japanese REPLACED: {donor} records this line" if context.text in recorded.get(donor, ()) \
        else "japanese moz-original"


def _number(context) -> str:
    """same or other: whether a stage line's or quote's take is the donor record of the stock line's own suffix;
    dream for a Dream stage line, which MOZ numbers anew whatever donor line it reuses."""
    from wozmod.formats.audio import PSP_CLIP_BASE, WO1_RECORD_BASE, WO2_CLIP_BASE
    from wozmod.text.script_alignment import DREAM_SCRIPTS

    base = {"wo1-x360": WO1_RECORD_BASE, "wo2-x360": WO2_CLIP_BASE, "wo2-psp": PSP_CLIP_BASE}
    suffix = re.search(rb"@([0-9]+)$", context.stock)
    if context.kind not in ("stage", "quote") or not context.banked or not suffix:
        return "-"
    if context.kind == "stage" and int(context.id[5:].split(".")[0]) in {o for o, _ in DREAM_SCRIPTS}:
        return "dream"
    donor, record = context.banked
    return "same" if record == int(suffix[1]) - 1 + base[donor] else "other"


def spoken_after(moz: dict) -> dict[str, str]:
    """Each stock stage line's context id to the line the stage script speaks just before it (its first speak): what
    the player heard last, to set beside a donor recording of the same moment. MOZ adds lines in events of their own,
    after a line both games speak (Dream stage 66 speaks its new line 41 right after line 12)."""
    from wozmod.text.script_alignment import SPEAK, _commands

    found = {}
    for o, script in moz.items():
        last = None
        for (op, *_), args in _commands(script, lambda p: p):
            if op == SPEAK:
                found.setdefault(f"stage{o}.{args[2]}", f"stage{o}.{last}" if last is not None else "-")
                last = args[2]
    return found


def _head() -> str:
    return subprocess.run(["git", "-C", str(checkout.REPO), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def build() -> int:
    from wozmod.text.contexts import World, build_text_stage

    found = checkout.inputs()
    game, donors = Path(found["WOZMOD_GAME"]), Path(found["WOZMOD_DONORS"])
    with tempfile.TemporaryDirectory() as stage:
        world = World(game, donors, build_text_stage(game, donors, Path(stage)), checkout.REPO / "data/text")
        contexts = [c for c in world.contexts("english") if c.voice_id is not None]
    recorded, after = recorded_lines(world), spoken_after(world._dialogue.moz)
    REFERENCE.parent.mkdir(exist_ok=True)
    partial = REFERENCE.with_suffix(".partial")
    with partial.open("w", encoding="utf-8", newline="") as out:
        out.write(f"# built from {_head()} by tools/voice_reference.py: game text, never commit or publish\n")
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        writer.writerow(COLUMNS)
        for c in contexts:
            take = f"{c.voice[0]}:{c.voice[1]}" if c.voice else "stock"
            writer.writerow([c.id, c.kind, c.voice_id, verdict(c, recorded), take, _number(c),
                             after.get(c.id.split("#")[0], "-"), (c.row or {}).get("source", "none"), c.stock.decode("cp932", "replace"), c.text,
                             " / ".join(line.text for line in c.candidates)])
    partial.replace(REFERENCE)
    counts = defaultdict(int)
    for c in contexts:
        counts[verdict(c, recorded).split(":")[0]] += 1
    print(f"{len(contexts)} voiced lines in {REFERENCE}: {dict(counts)}")
    return 0


def why(query: str) -> int:
    if not REFERENCE.is_file():
        print(f"no {REFERENCE}: run tools/voice_reference.py build", file=sys.stderr)
        return 2
    with REFERENCE.open(encoding="utf-8") as stream:
        header = stream.readline()
        rows = list(csv.DictReader(stream, delimiter="\t"))
    built = header.split()[3] if header.startswith("# built from") else "?"
    if built != _head():
        print(f"(built from {built}, the checkout is at {_head()}: rebuild if the voice maps or text changed)")
    needle = query.casefold()
    hits = [r for r in rows if r["context"] == query or r["context"].startswith(query + "#") or r["voice_id"] == query
            or any(needle in r[k].casefold() for k in ("japanese", "english", "take_says"))]
    for r in hits[:40]:
        print("\n".join(f"{k:>9}: {r[k]}" for k in COLUMNS) + "\n")
    print(f"{len(hits)} lines match" + (" (first 40 shown)" if len(hits) > 40 else ""))
    return 0 if hits else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="write reference/voice-lines.tsv from the stock game and donors")
    lookup = commands.add_parser("why", help="the lines whose context id, voice id or text matches")
    lookup.add_argument("query")
    args = parser.parse_args()
    return build() if args.command == "build" else why(args.query)


if __name__ == "__main__":
    sys.exit(main())
