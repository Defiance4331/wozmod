import hashlib
import json
import math
import os
import struct
from collections import Counter
from pathlib import Path

import pytest

from synthetic import write_ens, write_stock_archive
from wozmod.formats.scripts import caption_cues
from wozmod.runtime.english_runtime import encode_english
from wozmod.text import DonorResolver
from wozmod.text.movie_captions import MovieCaptionError, build_dub_schedules, build_movie_schedules

REPO = Path(__file__).resolve().parents[1]


def _schedule(cues: list[tuple[int, int, bytes]]) -> bytes:
    """A caption schedule in the stock layout."""
    return (struct.pack("<I", len(cues)) + b"".join(struct.pack("<II", s, e) for s, e, _ in cues)
            + b"".join(text + b"\0" for _, _, text in cues))


def _french(times: list[tuple[int, int]]) -> bytes:
    """A French schedule in the stock layout, its texts our own placeholders."""
    return _schedule([(s, e, b"cue %d" % i) for i, (s, e) in enumerate(times)])


def _build(tmp_path, cues: list[str]):
    donors = tmp_path / "donors"
    (donors / "wo1-x360/linkdata_fns").mkdir(parents=True)
    # 25 ticks a second: 2.0 to 3.6 s and 10.0 to 12.5 s.
    (donors / "wo1-x360/linkdata_fns/370.bin").write_bytes(_french([(50, 90), (250, 312)]))
    data = tmp_path / "dub_captions.json"
    data.write_text(json.dumps({"movies": [
        {"slot": "movie103", "entry": 172, "timing": ["wo1-x360", "fns", 370], "cues": cues}]}))
    build_dub_schedules(DonorResolver(donors), data, tmp_path / "out")
    return tmp_path / "out/wozmod/captions/voice"


def test_each_caption_takes_its_french_cue_time_on_the_stock_clock(tmp_path):
    # Bug: the 25-a-second French counts are copied unscaled (captions run 20% early), or the file is named by the
    # French entry or the bare LINKDATA2 entry instead of the runtime's key (4251 + 172), so the game never reads it.
    folder = _build(tmp_path, ["Our first line.", "Our second line."])
    assert [p.name for p in folder.iterdir()] == ["4423.bin"]
    # 2.0 s is 59.94 stock ticks, 3.6 s 107.89, 10.0 s 299.7, 12.48 s 374.03.
    assert caption_cues((folder / "4423.bin").read_bytes()) == [
        (60, 108, b"Our first line."), (300, 374, b"Our second line.")]


def test_a_caption_count_unlike_the_french_cues_is_refused(tmp_path):
    # Bug: zip() drops the extra caption, or a missing one shifts every later line onto the wrong speech.
    with pytest.raises(MovieCaptionError, match="movie103"):
        _build(tmp_path, ["Only one line."])


# Our own placeholder bytes stand in for the stock Japanese (CP932) and the donor English.
SHARED, OTHER = (chr(0x3042) * 2).encode("cp932"), chr(0x3044).encode("cp932")
# movie021 (entry 166): two stock cues with the same string, which the Xbox 360 schedule (ENS 266) words apart.
STOCK_166 = [(245, 404, SHARED), (617, 779, SHARED)]
X360_266 = [(245, 404, b"First donor\nline"), (627, 789, b"Second donor line")]
# movie017 (entry 162): the Xbox 360 schedule splits stock cue 0 and has a blank cue where stock cue 1 is ours.
STOCK_162 = [(100, 300, OTHER), (400, 450, SHARED)]
X360_262 = [(100, 190, b"Split one"), (191, 310, b"split two"), (400, 452, b" ")]


def _movies(tmp_path, cues: list[dict], voice_cues: list[dict]):
    stock, donors, out = tmp_path / "stock", tmp_path / "donors", tmp_path / "out"
    stock.mkdir()
    (donors / "wo2-x360").mkdir(parents=True)
    empty = _schedule([])
    write_stock_archive(stock, "LINKDATA2", {**dict.fromkeys(range(146, 177), empty),
                                             166: _schedule(STOCK_166), 162: _schedule(STOCK_162)}, count=177)
    write_ens(donors / "wo2-x360", {**dict.fromkeys(range(246, 271), empty),
                                    266: _schedule(X360_266), 262: _schedule(X360_262)}, count=271)
    data, dub = tmp_path / "movie_captions.json", tmp_path / "dub_captions.json"
    data.write_text(json.dumps({"version": 1, "cues": cues, "voice_cues": voice_cues}))
    dub.write_text(json.dumps({"movies": []}))
    table = {SHARED: b"Table row", OTHER: b"Other row"}
    build_movie_schedules(stock, DonorResolver(donors), table, data, dub, out)
    return out / "wozmod/captions"


def _own(slot: str, entry: int, cue: int, stock: bytes, x360: int, x360_cue: int) -> dict:
    return {"slot": slot, "entry": entry, "cue": cue, "key": hashlib.sha256(stock).hexdigest(), "source": "wo2-x360",
            "ref": ["wo2-x360", "ens", x360, "captions", [x360_cue]]}


def test_each_movie_cue_keeps_its_own_english_where_another_shares_its_japanese(tmp_path):
    # Bug: a cue takes its English by its stock string, so movie021 cue 1 shows cue 0's line (and movie017 cue 1,
    # with the same string, the row's); or a row lands one cue off; or the stock times are not the stock movie's.
    folder = _movies(tmp_path, [_own("movie021", 166, 0, SHARED, 266, 0), _own("movie021", 166, 1, SHARED, 266, 1)], [])
    assert caption_cues((folder / "4417.bin").read_bytes()) == [
        (245, 404, b"First donor line"), (617, 779, b"Second donor line")]
    assert caption_cues((folder / "4413.bin").read_bytes()) == [(100, 300, b"Other row"), (400, 450, b"Table row")]


def test_the_english_voices_get_the_xbox_360_cue_times_and_each_cue_its_own_line(tmp_path):
    # Bug: the English-voice schedule keeps the stock times (the stock end cuts the English line short) or the
    # stock cue count (zip drops the Xbox 360 cue the stock movie joins), a voice cue shows another stock cue's
    # line, or the file is not under the runtime's key for the movie (4251 + entry) in the voice folder.
    folder = _movies(tmp_path, [_own("movie021", 166, 0, SHARED, 266, 0), _own("movie021", 166, 1, SHARED, 266, 1)],
                     [{"slot": "movie017", "entry": 162, "cue": 2, "stock_cue": 1}])
    assert sorted(p.name for p in (folder / "voice").iterdir()) == ["4413.bin", "4417.bin"]
    assert caption_cues((folder / "voice/4413.bin").read_bytes()) == [
        (100, 190, b"Split one"), (191, 310, b"split two"), (400, 452, b"Table row")]
    assert caption_cues((folder / "voice/4417.bin").read_bytes()) == [
        (245, 404, b"First donor line"), (627, 789, b"Second donor line")]


def test_a_cue_row_over_another_stock_string_is_refused(tmp_path):
    # Bug: a row written for one cue sits at another (a shifted cue index, or a stock movie that changed), and
    # replaces a line it was never written for.
    with pytest.raises(MovieCaptionError, match="movie017.0"):
        _movies(tmp_path, [_own("movie017", 162, 0, SHARED, 262, 0)], [])


# The real movies: every built schedule against the stock and donor schedules, reporting every cue that is wrong.
REAL = pytest.mark.skipif(not (os.environ.get("WOZMOD_MOVIE_CAPTIONS") and os.environ.get("WOZMOD_GAME")
                               and os.environ.get("WOZMOD_DONORS")),
                          reason="set WOZMOD_MOVIE_CAPTIONS=1, WOZMOD_GAME and WOZMOD_DONORS (about 30 s)")
# LINKDATA2 schedule entry of each captioned movie file (the stock loader 0x56A2C0).
ENTRIES = {**{f"movie{n:03d}": 145 + n for n in range(1, 26)}, **{f"movie{n}": 69 + n for n in range(102, 106)},
           "movie200": 175, "movie201": 176}
# Approved cues whose English is ours, not the donor's: two official lines with a typo fixed and two lines of ours
# over cues the Xbox 360 schedule leaves blank (text ledger row 1: official first, gaps filled, edits marked).
OURS = {(150, 4): "edited", (162, 4): "authored", (162, 7): "edited", (162, 8): "authored"}
# A WO1 ending cue whose string the game also shows elsewhere, with the English the dub says there.
WO1_SHARED = {(171, 11): ["wo1-x360", "ens", 159, "event", 31]}


@pytest.fixture(scope="module")
def real_movies(tmp_path_factory):
    """The installer's text stage on the real inputs: (stock schedules, built folder, resolver, table rows)."""
    from wozmod.formats.linkdata import archive
    from wozmod.install import _text_table

    game, donors = Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"])
    out = tmp_path_factory.mktemp("movie-captions")
    _text_table(game, donors, out)
    stock = {entry: caption_cues(data) for entry, data in archive(game / "LINKDATA2").read(range(146, 177)).items()}
    rows = {row["key"]: row for row in json.loads((REPO / "data/text/table.json").read_text())["rows"]}
    return stock, out / "wozmod/captions", DonorResolver(donors), rows


def _english(raw: bytes) -> bytes:
    """Donor caption bytes as the runtime gets them: the words, which it lays out again in its own lines."""
    from wozmod.formats.scripts import donor_text

    return encode_english(" ".join(donor_text(raw).split()))


def _times_and_texts(problems: list, name: str, built: list, times: list, texts: list) -> None:
    if [cue[:2] for cue in built] != times:
        problems.append(f"{name}: times {[cue[:2] for cue in built]} are not {times}")
    for cue, (got, want) in enumerate(zip([cue[2] for cue in built], texts, strict=False)):
        if got != want:
            problems.append(f"{name} cue {cue}: {got[:40]!r} is not {want[:40]!r}")
    if len(built) != len(texts):
        problems.append(f"{name}: {len(built)} cues, not {len(texts)}")


@REAL
def test_with_english_voices_each_movie_shows_the_captions_of_the_english_movie(real_movies):
    # Bug: an Xbox 360 movie keeps the stock times (the stock end cuts the English line short), a schedule is
    # missing or under the wrong key so the stock one plays, an Xbox 360 cue is dropped where the stock movie joins
    # two, a cue shows another line (the movie021 cue 1 repeat), or the WO1 dub cues lose the French timing.
    from wozmod.formats.linkdata import archive

    stock, folder, resolver, _ = real_movies
    ens = archive(resolver.donors.folder("wo2-x360") / "linkdata_ens")
    movies = json.loads((REPO / "data/movies.json").read_text())["movies"]
    voiced = {ENTRIES[m["slot"]]: m["donor"] for m in movies if m["language"] == "voice" and m["slot"] in ENTRIES}
    dub = {m["entry"]: m for m in json.loads((REPO / "data/text/dub_captions.json").read_text())["movies"]}
    problems = []
    if (found := sorted(int(p.stem) - 4251 for p in (folder / "voice").iterdir())) != sorted(voiced):
        problems.append(f"English-voice schedules for entries {found}, the English movies are {sorted(voiced)}")
    for entry, donor in sorted(voiced.items()):
        path, name = folder / f"voice/{4251 + entry}.bin", f"movie entry {entry}"
        if not path.is_file():
            continue
        built = caption_cues(path.read_bytes())
        if donor == "wo2-x360":  # its own schedule, counted on the stock clock (the counts match the stock ones)
            theirs = caption_cues(ens[entry + 100])
            ours = caption_cues((folder / f"{4251 + entry}.bin").read_bytes())
            texts = [ours[cue][2] if (entry, cue) in OURS else _english(text) if text.strip() else b""
                     for cue, (_, _, text) in enumerate(theirs)]
            _times_and_texts(problems, name, built, [cue[:2] for cue in theirs], texts)
        else:  # the French schedule of the same dub, 25 ticks a second
            french = caption_cues((resolver.donors.folder("wo1-x360")
                                   / f"linkdata_fns/{dub[entry]['timing'][2]}.bin").read_bytes())
            # the stock counter is floor(seconds * 29.97 + 0.5) (0x5E91F0)
            times = [(math.floor(s / 25 * 29.97 + 0.5), math.floor(e / 25 * 29.97 + 0.5)) for s, e, _ in french]
            texts = [encode_english(resolver.resolve(cue) if isinstance(cue, list) else cue)
                     for cue in dub[entry]["cues"]]
            _times_and_texts(problems, name, built, times, texts)
    assert problems == []


@REAL
def test_with_japanese_voices_each_movie_cue_shows_its_own_english_on_the_stock_times(real_movies):
    # Bug: a cue shows the English of another line with the same Japanese (movie002 cue 0 showed a camp-talk
    # line, movie021 cue 1 repeated cue 0), a cue lands one off, a schedule loses the stock times, or a cue whose
    # string the game shows elsewhere has no English of its own, so a change to that other line would reach it.
    from wozmod.formats.linkdata import archive
    from wozmod.formats.scripts import event_texts
    from wozmod.text.inventory import inventory
    from wozmod.text.psp_exe import EXE_STRING
    from wozmod.text.stock import stock_key

    stock, folder, resolver, rows = real_movies
    game = Path(os.environ["WOZMOD_GAME"])
    ens = archive(resolver.donors.folder("wo2-x360") / "linkdata_ens")
    problems = []
    for entry, cues in sorted(stock.items()):
        built = caption_cues((folder / f"{4251 + entry}.bin").read_bytes())
        if 146 <= entry <= 170:  # the Xbox 360 cues each stock cue overlaps most, their words joined
            joined = {}
            for start, end, text in caption_cues(ens[entry + 100]):
                overlap, at = max((min(end, e) - max(start, s), i) for i, (s, e, _) in enumerate(cues))
                if overlap > 0 and text.strip():
                    joined.setdefault(at, []).append(_english(text))
            texts = [b" ".join(joined.get(cue, [])) for cue in range(len(cues))]
        elif entry in (175, 176):  # the PSP schedule of the same Dream movie, cue for cue
            texts = [_english(text) for _, _, text in resolver.view("wo2-psp", entry + 21, "captions")]
        else:  # the WO1 endings: our translation of each stock cue
            texts = [encode_english(resolver.resolve_row(row)) if (row := rows.get(stock_key(text)[0])) else text
                     for _, _, text in cues]
        for (at, cue), source in OURS.items():
            if at == entry:
                row = rows[stock_key(cues[cue][2])[0]]
                texts[cue] = encode_english(resolver.resolve_row(row)) if row["source"] == source else b"?"
        for (at, cue), ref in WO1_SHARED.items():
            if at == entry:
                texts[cue] = encode_english(resolver.resolve(ref))
        _times_and_texts(problems, f"movie entry {entry}", built, [cue[:2] for cue in cues], texts)

    # Every cue whose string the game shows at another place, and only such a cue, has a row of its own.
    elsewhere = {text for source, found in inventory(game).items() if source != "captions" for text in found}
    elsewhere |= {match.group() for match in EXE_STRING.finditer((game / "OROCHI_Z.exe").read_bytes())}
    for data in archive(game / "LINKDATA2").read(range(177, 414)).values():
        elsewhere |= set(event_texts(data))
    places = [(entry, cue, text) for entry, cues in stock.items() for cue, (_, _, text) in enumerate(cues)]
    counts = Counter(text for _, _, text in places)
    own = {(row["entry"], row["cue"]) for row in json.loads((REPO / "data/text/movie_captions.json").read_text())["cues"]}
    shared = {(entry, cue) for entry, cue, text in places
              if text.strip(b" \n\x81\x40") and (counts[text] > 1 or text in elsewhere)}
    if shared != own:  # a row the cue does not need would hide the next shared cue among the reviewed ones
        problems.append(f"cues shown elsewhere without a row of their own: {sorted(shared - own)}, "
                        f"rows for cues shown nowhere else: {sorted(own - shared)}")
    if (wo1 := {place for place in shared if 171 <= place[0] <= 174}) != set(WO1_SHARED):
        problems.append(f"WO1 ending cues shown elsewhere: {sorted(wo1)}, the test expects {sorted(WO1_SHARED)}")
    assert problems == []
