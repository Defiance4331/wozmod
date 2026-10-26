"""Every subtitle a player sees, checked in its context against the donor game, on the stock game and the donors.

Each test walks every context of src/wozmod/text/contexts.py in both voice modes and names every failing one by its
id (movie021.c1, event283.2.150, stage107.31, caption219.5.0, mesc4105.162), never by its text. A context listed in
data/text/context_exceptions.json for a check passes it; an entry that no longer fails is reported as stale.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXCEPTIONS = REPO / "data/text/context_exceptions.json"
INPUTS = bool(os.environ.get("WOZMOD_GAME") and os.environ.get("WOZMOD_DONORS"))
pytestmark = [
    pytest.mark.skipif(not (os.environ.get("WOZMOD_CONTEXTS") == "1" and INPUTS),
                       reason="set WOZMOD_CONTEXTS=1, WOZMOD_GAME and WOZMOD_DONORS (about two minutes)"),
    pytest.mark.xdist_group("subtitle-contexts"),  # one worker, so the tests share one text stage and one World
]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from wozmod.text.contexts import World, build_text_stage

    game, donors = Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"])
    return World(game, donors, build_text_stage(game, donors, tmp_path_factory.mktemp("text-stage")), REPO / "data/text")


@pytest.fixture(scope="module")
def contexts(world):
    return {mode: world.contexts(mode) for mode in ("english", "japanese")}


def _verdict(check: str, failing: dict[str, str], checked: int) -> None:
    """Fail on every failing context the exceptions file does not name for check, and on every stale entry."""
    excepted = {e["context"] for e in json.loads(EXCEPTIONS.read_text(encoding="utf-8"))["exceptions"]
                if e["check"] == check}
    new = {where: why for where, why in failing.items() if where.split(" ")[0] not in excepted}
    stale = sorted(excepted - {where.split(" ")[0] for where in failing})
    classes = Counter(why.split(";")[0].split(":")[0] for why in new.values())
    message = (f"{check}: {len(new)} of {checked} contexts fail ({', '.join(f'{k} {v}' for k, v in classes.items())})"
               + "".join(f"\n{where}: {why}" for where, why in sorted(new.items()))
               + (f"\n{len(stale)} stale exceptions (they pass now; remove them): {', '.join(stale)}" if stale else ""))
    assert not new and not stale, message


def _pointer(line) -> str:
    return json.dumps(list(line.pointer), separators=(",", ":"))


def _row(context) -> str:
    row = context.row
    if row is None:
        return "no row" if context.key is None and context.kind != "movie" else "served text"
    return f"row {row['key'][:12]} ({row['source']})"


def test_every_voiced_subtitle_shows_the_line_its_voice_speaks(contexts) -> None:
    # Bugs: one string-keyed row shows another context's line (movie021 c1 repeated c0; movie002 c0 showed camp
    # talk); an event joined to the wrong donor file shows our text over an official voice (event 283); two lines
    # play each other's voice (stage 107.31/32); a movie whose audio changed keeps text written for the old audio
    # (movie102-105 at 2d08aa4); a dropped row (Japanese shown). Oracle: what the donor game pairs with what plays
    # there: under the English voice the donor line that owns the clip the bank holds (its script, event or narration
    # line), the donor movie's own caption at that time, or the dub transcript; under the Japanese voice the donor
    # lines of every donor's clip for the line's voice id; for a place caption, the WO2 X360 scenes' caption of the
    # same string. Never official_pairs, EVENTS or the table.
    failing, checked = {}, 0
    for mode, found in contexts.items():
        by_id = {c.id: c for c in found}
        by_key = defaultdict(list)
        for context in found:
            by_key[context.key].append(context)
        for context in found:
            if not context.candidates:
                continue
            checked += 1
            heard = {line.text for line in context.candidates}
            if context.text in heard:
                continue
            if context.key is None and context.kind != "movie":
                why = "japanese: no row"
            elif context.row and context.row["source"] in ("authored", "edited"):
                why = f"unpaired: {context.row['source']}"
            elif any(context.text in {line.text for line in other.candidates} for other in by_key[context.key]
                     if other is not context and context.key is not None):
                why = "collision: another context behind the same key shows this English"
            elif any(n in by_id and context.text in {line.text for line in by_id[n].candidates}
                     for n in context.neighbours):
                why = "crossed: a neighbouring line's English"
            else:
                why = "mismatch"
            voice = f" voice {context.voice[0]} record {context.voice[1]}" if context.voice else ""
            failing[f"{context.id} {mode}"] = (f"{why}; {_row(context)}{voice}; the donor pairs "
                                               + " or ".join(_pointer(line) for line in context.candidates))
    assert checked > 30_000, f"only {checked} contexts have a donor line: a source was dropped"
    _verdict("C1", failing, checked)


def test_no_context_shows_our_text_where_a_donor_line_exists(contexts) -> None:
    # Bugs: a line keeps our wording though a donor holds the line its voice plays (event 283's five authored lines:
    # the pairing map named one donor file, the voices join all of them), and a donor swap drops a row to our text
    # (the He Fei Castle gate caption in 1ca522a). Oracle: the candidate search of contexts.py, through each donor's
    # clip for the voice id, the WO2 X360 scenes' place captions and the donor movie cues, not the pairing tools.
    failing, checked = {}, 0
    for mode, found in contexts.items():
        for context in found:
            if context.row and context.row["source"] == "authored":
                checked += 1
                if context.candidates and (mode == "japanese" or context.kind == "movie"):
                    failing[f"{context.id} {mode}"] = (f"authored row {context.row['key'][:12]}; the donor has "
                                                       + " or ".join(_pointer(line) for line in context.candidates))
    _verdict("C3", failing, checked)


def test_every_subtitle_window_covers_its_voice(world, contexts) -> None:
    # Bugs: under the English voice a movie runs the stock caption schedule (an English line cut short, a cue split
    # in two on the Xbox 360 shown joined), the WO1 dub schedule loses its 25-to-29.97 scale, a schedule is served
    # in the wrong voice mode, an event window ends on the stock frame clock while the English clip still speaks or
    # lingers long after it. Oracle: the donor's own schedules (X360 caption entries, the French FNS timing of the
    # dub), the donor event windows Koei retimed (in the donor sub-scene each stock sub-scene was made from), and
    # each installed English clip's audible end (last 10 ms frame above -30 dB of its peak). Rule (orchestrator, 2026-10-24): a donor-authored schedule is shown exactly; every
    # other English-voice window covers the clip's audible end, unless the next line or the scene end cuts it, and
    # ends no later than 0.5 s and one tick after it.
    from wozmod.formats.linkdata import archive
    from wozmod.formats.scripts import caption_cues
    from wozmod.text.contexts import EVENT_TICKS, audible_ends, movie_schedule, movie_slot_entry

    failing, checked = {}, 0
    voiced = {row["slot"]: row for row in world.movies if row["language"] == "voice"}
    wo2_ens = archive(world.folders.folder("wo2-x360") / "linkdata_ens")
    for mode, found in contexts.items():
        shown = defaultdict(list)
        for context in found:
            if context.kind == "movie":
                shown[context.id.split(".")[0]].append((context.window.start, context.window.end))
        for slot, cues in shown.items():
            source = movie_schedule(voiced[slot]) if mode == "english" and slot in voiced else None
            if source and source[1] == "ens":
                expected = [(start, end) for start, end, _ in caption_cues(wo2_ens[source[2]])]
            elif source:  # French ticks, 25 a second, on the stock 29.97 clock
                french = (world.folders.folder(source[0]) / f"linkdata_fns/{source[2]}.bin").read_bytes()
                expected = [(int(start * 29.97 / 25 + 0.5), int(end * 29.97 / 25 + 0.5))
                            for start, end, _ in caption_cues(french)]
            else:
                expected = [(start, end) for start, end, _ in caption_cues(world.linkdata2[movie_slot_entry(slot)])]
            checked += len(cues)
            what = "/".join(map(str, source)) if source else "the stock schedule"
            if len(cues) != len(expected):
                failing[f"{slot} {mode}"] = f"schedule: {len(cues)} cues where {what} has {len(expected)}"
            for c, (got, want) in enumerate(zip(cues, expected, strict=False)):
                if got != want:
                    failing[f"{slot}.c{c} {mode}"] = f"schedule: shown {got} where {what} has {want}"
    english = [c for c in contexts["english"] if c.kind == "event" and c.window and c.voice]
    ends = audible_ends(world, {c.voice for c in english})
    skipped = Counter()
    for context in contexts["japanese"]:
        if context.kind == "event" and context.window and context.stock_window:
            checked += 1
            got, want = (context.window.start, context.window.end), (context.stock_window.start, context.stock_window.end)
            if got != want:
                failing[f"{context.id} japanese"] = f"stock window: shown {got} where the stock scene has {want}"
    for context in english:
        window, donor, stock = context.window, context.donor_window, context.stock_window
        if window.in_block:
            skipped["in a conditional block"] += 1
            continue
        checked += 1
        got = (window.start, window.end)
        if donor and stock and (donor.start, donor.end) != (stock.start, stock.end):
            if got != (donor.start, donor.end):
                failing[f"{context.id} english"] = (f"donor window: shown {got} where the donor scene, retimed by "
                                                    f"Koei, has {(donor.start, donor.end)}")
            continue
        heard, shown_for = ends[context.voice], (window.end - window.start) / EVENT_TICKS
        if shown_for < heard and window.end_by == "clear":
            failing[f"{context.id} english"] = (f"cut: shown {shown_for:.2f} s, the English clip is heard for "
                                                f"{heard:.2f} s")
        elif shown_for > heard + 0.5 + 1 / EVENT_TICKS:
            failing[f"{context.id} english"] = (f"linger: shown {shown_for:.2f} s, the English clip is heard for "
                                                f"{heard:.2f} s (ends by {window.end_by})")
    print(f"C2 left out: {dict(skipped)}")
    _verdict("C2", failing, checked)


SUBTITLE_SOURCES = ("captions", "events", "spoken", "officer quotes", "mesc 4071", "mesc 4073", "mesc 4103",
                    "mesc 4105")


def test_contexts_cover_every_subtitle_and_every_english_clip(world, contexts) -> None:
    # Bug: the context builder drops a source (events 384-413, camp talk 4073, a movie slot, unvoiced dialog lines),
    # so every other test here passes without looking at it. Oracle: text.inventory (the stock strings and their
    # draw sites) and the installer's voice choice, two enumerations the contexts do not use.
    from wozmod.formats.audio import (
        PSP_BRIEFING_VOICES, WO1_EVENT_VOICES, WO1_NARRATION_VOICES, WO2_EVENT_VOICES, WO2_NARRATION_VOICES,
    )
    from wozmod.formats.scripts import quote_text
    from wozmod.text.inventory import inventory

    seen = set()
    for found in contexts.values():
        for context in found:
            seen.add(context.stock)
            if context.kind in ("stage", "quote"):
                seen.add(quote_text(b"00" + context.stock))
    stock = inventory(world.stock)
    missing = {f"{source} string {n}" for source in SUBTITLE_SOURCES for n, text in enumerate(stock[source])
               if text not in seen}  # by its place in the inventory: the text is game text
    counts = {source: sum(text in seen for text in stock[source]) for source in SUBTITLE_SOURCES}
    print(f"C5 inventory strings with a context: {counts}")
    assert not missing, f"{len(missing)} subtitle strings have no context: " + ", ".join(sorted(missing)[:50])
    # Every clip in the English bank plays in some context, but for the blocks a whole id range maps by count
    # (select voices, narration, event lines) whose stock data has no line at every id, and the ids of the stage lines
    # no speak command speaks (the installer maps every suffix; the engine never plays these).
    from wozmod.formats.audio import stage_voice

    voiced = {c.voice_id for found in contexts.values() for c in found if c.voice_id is not None}
    unspoken = {stage_voice(int(c.id[5:].split(".")[0]), int(suffix.group(1)), 0) for c in contexts["english"]
                if c.kind == "stage" and c.voice_id is None and (suffix := re.search(rb"@([0-9]+)$", c.stock))}
    blocks = {"select voices": range(0, 96), "unspoken stage lines": unspoken}
    for base, _, count in (*WO1_NARRATION_VOICES, *WO2_NARRATION_VOICES, PSP_BRIEFING_VOICES):
        blocks[f"narration ids {base}+"] = range(base, base + count)
    for name, (base, _, count) in (("WO1 event", WO1_EVENT_VOICES), ("WO2 event", WO2_EVENT_VOICES)):
        blocks[f"{name} ids"] = range(base, base + count)
    unplayed = Counter()
    lost = []
    for voice_id in sorted(set(world.voices) - voiced):
        block = next((name for name, ids in blocks.items() if voice_id in ids), None)
        if block:
            unplayed[block] += 1
        else:
            lost.append(voice_id)
    print(f"C5 English clips no stock line plays, by block: {dict(unplayed)}")
    assert not lost, f"{len(lost)} English clips of script lines or quotes have no context: {lost[:50]}"


@pytest.mark.skipif(os.environ.get("WOZMOD_ASR") != "1", reason="set WOZMOD_ASR=1 (reads tools/asr_audit.py's transcripts)")
def test_no_reviewed_voice_shows_a_neighbours_line(world, contexts) -> None:
    # Bug: the voice map plays a neighbour's clip (stage 107.31/32 before the fix) while the text and the donor pairing
    # agree with each other, which no text-only check can see. Oracle: speech recognition of each English clip, cached
    # by tools/asr_audit.py in work/asr/ (its transcripts are game text and stay there). The verdicts are recomputed
    # here against today's contexts, so a changed subtitle is judged anew; a clip with no transcript is stale.
    import hashlib

    import asr_audit

    cache, digests = asr_audit.transcripts(), {}

    def heard(context) -> str | None:
        if context.voice not in digests:
            digests[context.voice] = hashlib.sha256(asr_audit.clip_riff(world, context.voice)).hexdigest()
        cached = cache / f"{digests[context.voice]}.txt"
        return cached.read_text(encoding="utf-8") if cached.is_file() else None

    found = asr_audit.verdicts(contexts["english"], heard)
    stale = [c.id for c in contexts["english"] if c.voice and c.id not in found]
    assert not stale, f"{len(stale)} voiced contexts play a clip with no transcript: run tools/asr_audit.py ({stale[:20]})"
    failing = {f"{where} english": f"crossed: the clip's words score higher on {best}"
               for where, (kind, best, _) in found.items() if kind == "crossed"}
    _verdict("ASR", failing, len(found))
