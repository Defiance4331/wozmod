"""The subtitle context model (src/wozmod/text/contexts.py) on synthetic bytes, and its runtime model against the C."""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
from itertools import product
from pathlib import Path

import pytest

from wozmod.formats.audio import WO1_EVENT_VOICES
from wozmod.runtime.english_runtime import table_blob
from wozmod.text import contexts

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"


def event_file(lines: list[tuple[float, float, int, bytes]], length: float, order: str = "<",
               captions: tuple[tuple[float, bytes], ...] = ()) -> bytes:
    """One sub-scene: for each (speak tick, clear tick, line index, text) a gate, a speak, a gate and a clear, and for
    each (tick, text) a gate and a place caption, in tick order; then its length and the end record. The line records
    follow the chain, each at an offset 12 past a 16 boundary as the stock files have them."""
    f32, u16, u32 = (lambda v: struct.pack(f"{order}f", v)), (lambda v: struct.pack(f"{order}H", v)), \
        (lambda v: struct.pack(f"{order}I", v))
    events = sorted([(t, 1, i, text) for t, _, i, text in lines] + [(t, 2, 0, b"") for _, t, _, _ in lines]
                    + [(t, 0, 0, text) for t, text in captions])
    base, chain, tail, _texts = 16, [], bytearray(), []
    for tick, what, index, text in events:
        chain.append(("gate", tick))
        chain.append(("speak" if what == 1 else "clear" if what == 2 else "caption", (index, text)))
    chain += [("length", length), ("end", None)]
    sizes = {"gate": 16, "speak": 20, "clear": 8, "caption": 16, "length": 16, "end": 8}
    at = [0]
    for kind, _ in chain:
        at.append(at[-1] + sizes[kind])
    strings = at[-1] + (12 - (base + at[-1]) % 16) % 16  # the first line record, 12 past a 16 boundary
    out = bytearray()
    for n, (kind, value) in enumerate(chain):
        following = at[n + 1] if kind != "end" else 0
        if kind in ("gate", "length"):
            body = bytes([0, 0 if kind == "gate" else 1, 1, 6, 5, 4, 0, 0]) + f32(value)
        elif kind == "clear":
            body = bytes([5, 1, 0, 0])
        elif kind == "end":
            body = bytes([0xFF, 0, 0, 0])
        else:
            index, text = value
            offset = strings + len(tail)
            record = (b"\xff" * 4 + (u16(index) + u16(0) if kind == "speak" else b"") + text + b"\0")
            tail += record + bytes((12 - (base + strings + len(tail) + len(record)) % 16) % 16)
            if kind == "speak":
                body = bytes([5, 5, 2, 4, 8, 5, 4, 0]) + u16(0) + bytes(2) + u32(offset)
            else:
                body = bytes([5, 0, 1, 7, 5, 4, 0, 0]) + u32(offset)
        out += u32(following) + body
        assert len(u32(0) + body) == sizes[kind], kind
    out += bytes(strings - len(out)) + tail
    return u32(1) + u32(base) + bytes(base - 8) + bytes(out)


def test_an_event_window_runs_from_its_speak_to_the_next_text_record() -> None:
    # Bugs: the clock read one tick off (it starts at -1 and clamps to 0), a window ended at the next gate instead of
    # the next text record, a line read through a shifted offset, a big-endian donor file read little-endian. Oracle:
    # the record chain built here, with ticks chosen by hand.
    for order in "<>":
        data = event_file([(30.0, 90.0, 7, b"a"), (120.0, 200.0, 9, b"b")], 300.0, order, captions=((100.0, b"c"),))
        scene = contexts.event_scenes(data, order)
        assert [(w.index, w.start, w.end, w.end_by) for w in scene.windows] == [(7, 30, 90, "clear"), (9, 120, 200, "clear")]
        assert [(c.sub, c.ordinal, c.text) for c in scene.captions] == [(0, 0, b"c")]
        assert scene.lengths == (300.0,)
    last = contexts.event_scenes(event_file([(30.0, 400.0, 7, b"a")], 300.0), "<")
    assert [(w.end, w.end_by) for w in last.windows] == [(301, "end")]  # the scene ends before the clear runs


def test_a_clear_on_the_last_frame_is_the_scene_end() -> None:
    # Bug: a line cleared on its sub-scene's last frame (the retimer's clamp when the English clip outlasts the
    # event, 254.1 line 438) read as cleared early, so C2 reports a cut the scene end makes. Oracle: the record
    # chain built here; a clear one tick before the end is still a clear.
    windows = contexts.event_scenes(event_file([(30.0, 300.0, 7, b"a"), (100.0, 299.0, 9, b"b")], 300.0), "<").windows
    assert [(w.end, w.end_by) for w in windows] == [(100, "speak"), (299, "clear")]
    last = contexts.event_scenes(event_file([(30.0, 300.0, 7, b"a")], 300.0), "<").windows
    assert [(w.end, w.end_by) for w in last] == [(300, "end")]


def test_two_cues_with_one_stock_string_are_two_contexts_with_their_own_donor_lines() -> None:
    # Bug: contexts deduplicated by the stock string (or a cue paired with the wrong donor cue), so movie021 c1,
    # which repeats c0's Japanese but has its own X360 line, would never be compared with its own line.
    table = table_blob({b"\x82\xa0": b"First line"})  # what a string-keyed row gives both cues
    shown = lambda kind, raw: (*contexts.shown_text(table, kind, raw), None)  # noqa: E731
    english = contexts.shown_text(table, "movie", b"\x82\xa0")[0]  # the cue as the schedule serves it either way
    cues = [(10, 50, english), (60, 90, english)]
    donor = (("wo2-x360", "ens", 266), [(12, 48, "First line"), (61, 91, "Second line")], True)
    found = contexts.cue_contexts("movie021", "japanese", cues, donor, shown, "stock")
    assert [(c.id, [line.text for line in c.candidates]) for c in found] == [
        ("movie021.c0", ["First line"]), ("movie021.c1", ["Second line"])]
    assert found[0].text == found[1].text


def test_a_wo1_event_line_finds_its_donor_line_by_voice_record_in_any_ens_entry() -> None:
    # Bug: the donor line of an event voice looked up in one donor file chosen by a fixed entry shift (EVENTS paired
    # stock event 283 with ENS 265 only, while its lines are in ENS 249 and 257), so the line its voice speaks is not
    # found and our text passes. The voice plays record 957 + index whatever file holds the line.
    files = {265: event_file([(10.0, 20.0, 5, b"Other line")], 50.0, ">"),
             249: event_file([(10.0, 20.0, 150, b"Own line")], 50.0, ">"),
             12: b"\x00\x00\x00\x02not an event file at all"}
    lines = dict(contexts.event_records("wo1-x360", files, ">", WO1_EVENT_VOICES[1], WO1_EVENT_VOICES[2]))
    voice = contexts.event_voice(283, 150)
    assert voice == WO1_EVENT_VOICES[0] + 150
    record = WO1_EVENT_VOICES[1] + voice - WO1_EVENT_VOICES[0]  # the WO1 event block maps id to record one to one
    assert (lines[("wo1-x360", record)].pointer, lines[("wo1-x360", record)].text) == (
        ("wo1-x360", "ens", 249, "event", 150), "Own line")


def test_a_movie_cue_takes_the_caption_schedule_of_the_donor_file_it_plays() -> None:
    # Bug: the schedule taken from the slot instead of the file data/movies.json plays there, so a slot pointed at
    # another donor movie keeps the old movie's captions. Expected: WO2 X360 MEVnn's captions are ENS 245 + nn
    # (ENS 246 to 270 hold MEV01 to MEV25); WO1 X360 mev02 to mev05 have French timing in FNS 369 to 372 and no
    # English captions; the other donor movies have none.
    row = lambda slot, donor, name: {"slot": slot, "donor": donor, "source": f"data/movie/{name}.wmv"}  # noqa: E731
    assert contexts.movie_schedule(row("movie003", "wo2-x360", "MEV07")) == ("wo2-x360", "ens", 252)
    assert contexts.movie_schedule(row("movie001", "wo2-x360", "MEV01")) == ("wo2-x360", "ens", 246)
    assert contexts.movie_schedule(row("movie103", "wo1-x360", "mev03")) == ("wo1-x360", "fns", 370)
    assert [contexts.movie_schedule(row("movie100", "wo1-x360", n)) for n in ("mev06", "mev01")] == [None, None]
    assert contexts.movie_schedule(row("movie031", "wo2-x360", "story_title0")) is None


def test_each_movie_slot_reads_the_schedule_entry_the_loader_reads() -> None:
    # Bug: a slot mapped to its neighbour's schedule entry, or a slot that loads none given one. Expected from the
    # loader 0x56A2C0 and its gate 0x56A3B0: slot = movie number (movie1NN at 36 + NN, movie2NN at 46 + NN); slots
    # 0, 26-37, 42-45 and 48 load nothing; entry = 145 + slot, with slots 38-41 at 26-29 and 46 on at slot - 16.
    expected = {"movie000": None, "movie001": 146, "movie025": 170, "movie026": None, "movie035": None,
                "movie100": None, "movie101": None, "movie102": 171, "movie105": 174, "movie106": None,
                "movie200": 175, "movie201": 176, "movie202": None}
    assert {slot: contexts.movie_slot_entry(slot) for slot in expected} == expected


def test_audible_end_is_the_last_frame_above_minus_30_db_of_the_peak() -> None:
    # Bugs: the end measured to the clip's last sample (its silent tail counted as speech), or to the last frame
    # above an absolute level, so a quiet clip reads as silent. Oracle: hand-built 16 kHz clips.
    rate, frame = 16000, 160
    loud, quiet = struct.pack("<h", 20000), struct.pack("<h", 20000 // 40)  # quiet is -32 dB of the peak
    clip = (loud * frame * 50 + quiet * frame * 30 + bytes(2 * frame * 20))
    assert contexts.audible_end(clip, rate) == pytest.approx(0.5)
    soft = struct.pack("<h", 300) * frame * 10 + bytes(2 * frame * 5)
    assert contexts.audible_end(soft, rate) == pytest.approx(0.1)


def _c_function(source: str, name: str) -> str:
    """The text of a top-level C function or declaration whose line starts with name's pattern, to its closing }."""
    start = re.search(rf"^{name}", source, re.M).start()
    end = source.index("\n}\n", start) + 3
    return source[start:end]


def _case(source: str, hook: int) -> str:
    """A hook's case in english_hook, up to the next case."""
    start = source.index(f"    case {hook}:", source.index("static u32 english_hook("))
    body = re.compile(r"(    case \d+:[^\n]*\n)+").match(source, start).end()  # past the labels it shares
    return source[start:re.compile(r"\n    case \d+:|\n    default:").search(source, body).start()]


def test_the_text_hooks_look_up_the_kinds_the_context_model_says() -> None:
    # Bug: a hook gains or loses its table lookup in C (hook 44 stops looking movie cues up once the installer writes
    # each cue's English into its schedule) while contexts.LOOKUP_HOOKS still says it looks them up, so the model
    # shows the player another text than the game does.
    source = (RUNTIME / "english_runtime.c").read_text()
    calls = {hook: bool(re.search(r"\b(text|voiced_line)\(", _case(source, hook)))
             for hooks in contexts.LOOKUP_HOOKS.values() for hook in hooks}
    assert calls == {hook: True for hook in calls}
    assert bool(re.search(r"\btext\(f->ecx", _case(source, 44))) == bool(contexts.LOOKUP_HOOKS["movie"])
    assert "voiced_line(f->arg[2]" in _case(source, 9)


@pytest.mark.skipif(os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1", reason="set WOZMOD_RUNTIME_COMPILE=1")
def test_each_text_hook_looks_up_the_key_the_context_model_uses(tmp_path: Path) -> None:
    # Bugs: the runtime looks a voiced line up by another key than contexts.lookup_keys (its text without the
    # suffix first, or the suffix row never tried), or served() picks another loose folder than contexts.served_entry
    # (the Japanese voice served the English-voice schedule, or the voice folder losing to the other), or a still
    # event's or WO1 page's draw stops trying its line with the voice key (camp talk then shows the shared row's
    # English) while VOICE_KEYED_MESC says it does. The C under test is the runtime's own enrt_translate, text,
    # event_line, voiced_line and served, compiled from english_runtime.c; page_voice, which reads the game's memory,
    # is a stub.
    if not (shutil.which("i686-w64-mingw32-gcc") and shutil.which("wine")):
        pytest.skip("i686-w64-mingw32-gcc or wine absent")
    source = (RUNTIME / "english_runtime.c").read_text()
    rows = {b"line@0012": b"Own wording", b"line": b"Shared wording", b"other": b"Other", b"quote": b"Quote",
            b"line@V0942": b"Voiced wording"}
    stills = [(site, voice, text) for site, voice in (("STILL_DRAW", 942), ("STILL_Z_DRAW", 1480),
                                                      ("NARRATION_DRAW", 942))
              for text in (b"line", b"other")]
    blob = table_blob(rows)
    lines = [b"00line@0012", b"00line@0013", b"00other@0001", b"00none@0002", b"01quote@00042", b"00line"]
    folders = [name.replace("\\\\", "/").rstrip("/") for name in
               re.findall(r'L"([^"]*)"', re.search(r"loose_dirs\[\d+\] = \{([^}]*)\}", source).group(1))]
    assert folders == [f for f, _ in contexts.LOOSE_FOLDERS], "contexts.LOOSE_FOLDERS is not the runtime's loose_dirs"
    cases = [(held, mode) for held in product((0, 1), repeat=len(folders)) for mode in contexts.MODES]
    glue = "\n".join([
        "#include <windows.h>", "#include <stdio.h>", "#include <string.h>", '#include "text_fit.h"',
        '#include "event_line.h"' if (RUNTIME / "event_line.h").exists() else "",
        "typedef unsigned int u32; typedef unsigned short u16; typedef unsigned char u8;",
        "static u32 inner; static const u32 *enrt_table; static u32 voice_saved;",
        re.search(r"^#define VOICE_DIR .*$", source, re.M).group(0) if "#define VOICE_DIR" in source else "",
        re.search(r"^static struct loose \{.*$", source, re.M).group(0), "static u32 loose_count;",
        "static struct loose loose_store[64];",  # the runtime may grow its table at run time: loose is then a pointer
        _c_function(source, r"struct frame \{").replace("\n}\n", "\n}"), ";",
        *(_c_function(source, rf"static \w+ \**{name}\(") for name in ("fnv", "same", "len", "copy")),
        _c_function(source, r"const char \*enrt_translate\("),
        "static u32 voice_mode; static struct frame no_frame;", "#define VOICE_MODE voice_mode",
        *(re.search(rf"^#define {site} .*$", source, re.M).group(0) for site in ("STILL_DRAW", "STILL_Z_DRAW", "NARRATION_DRAW")),
        "static u32 page_voice(u32 ret) { return ret == STILL_Z_DRAW ? 1480 : 942; }",
        *([_c_function(source, r"static const char \*event_line\(const char \*s, u32 ret\)\n\{")]
          if "static const char *event_line(const char *s, u32 ret)\n{" in source else []),
        _c_function(source, r"static u32 text\("), _c_function(source, r"static u32 voiced_line\("),
        _c_function(source, r"static struct loose \*served\("),
        "static const unsigned char blob[] = {" + ",".join(map(str, blob)) + "};",
        "static const char *shown(const char *line) {  /* hook 9, then the window hook 6 on the bare text */",
        "    static char bare[256]; const char *v = (const char *)voiced_line((u32)line, 9, &no_frame), *at = strrchr(v + 2, '@');",
        "    size_t n = at ? (size_t)(at - (v + 2)) : strlen(v + 2);",
        "    memcpy(bare, v + 2, n), bare[n] = 0;", "    return (const char *)text((u32)bare, 6, &no_frame);", "}",
        "int main(void) {", "    enrt_table = (const u32 *)blob;",
        "    loose = loose_store;" if re.search(r"^static struct loose \{.*\} \*loose;", source, re.M) else "",
        *(f'    puts(shown("{line.decode()}"));' for line in lines),
        *(f'    puts((const char *)text((u32)"{line[2:].decode()}", 1, &no_frame));' for line in lines),
        *(f'    {{ static struct frame f = {{.ret = {site}}}; puts((const char *)text((u32)"{text.decode()}", 1, &f)); }}'
          for site, _, text in stills),
    ] + [line for held, mode in cases for line in (
        "    loose_count = 0;",
        *(f"    loose[loose_count++] = (struct loose){{4396, 1, {d}}};" for d, h in enumerate(held) if h),
        f"    voice_saved = {int(mode == 'japanese')};",
        '    { struct loose *e = served(4396); printf("%d\\n", e ? (int)e[0].' + ("dir" if "u32 key, size, dir" in source
                                                                              else "voice") + " : -1); }")]
      + ["    return 0;", "}"])
    (tmp_path / "keys.c").write_text(glue)
    subprocess.run(["i686-w64-mingw32-gcc", "-std=gnu11", "-O1", "-w", "-I", str(RUNTIME), str(tmp_path / "keys.c"),
                    "-o", str(tmp_path / "keys.exe")], check=True)
    env = {**os.environ, "WINEPREFIX": str(tmp_path / "prefix"), "WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml="}
    out = subprocess.run(["wine", str(tmp_path / "keys.exe")], env={**env, "WINEDLLOVERRIDES": "winedbg.exe=d;mscoree,mshtml="},
                         check=True, capture_output=True, timeout=120).stdout
    got = out.decode().replace("\r", "").split("\n")
    voiced, drawn, still = got[:len(lines)], got[len(lines):2 * len(lines)], got[2 * len(lines):2 * len(lines) + len(stills)]
    served = got[2 * len(lines) + len(stills):-1]
    assert voiced == [contexts.shown_text(blob, "stage", line[2:])[0].decode() for line in lines]
    assert drawn == [contexts.shown_text(blob, "event", line[2:])[0].decode() for line in lines]
    assert still == [contexts.shown_text(blob, "mesc", text, voice)[0].decode() for _, voice, text in stills]
    expected = []
    for held, mode in cases:
        mod = tmp_path / f"mod-{''.join(map(str, held))}"
        for d, (folder, _) in enumerate(contexts.LOOSE_FOLDERS):
            if held[d]:
                (mod / "wozmod" / folder).mkdir(parents=True, exist_ok=True)
                (mod / "wozmod" / folder / "4396.bin").write_bytes(str(d).encode())
        found = contexts.served_entry(mod, 4396, mode)
        expected.append(found.decode() if found is not None else "-1")
    assert served == expected
