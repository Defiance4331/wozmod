from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

TABLE = Path(__file__).resolve().parents[1] / "data/text/table.json"

# Stock strings that are themselves placeholders or developer markers, so a placeholder translation fits them.
PLACEHOLDER_STOCK = {
    "0ebb59da0ccaa5fe11a50907daca4b979e86cdabdb8c5278661bb35d4b43ef0a",
    "6dbcd5842497303708b440bc858873e49e09fc3081965e5704721f243099de0b",
    "b327104257783da4ad4b3133bf928bd235e1789bbb58ea0fa4a34122248a9835",
    "bd99a376ca4b899232a0492066e1f0fb7852df016bf4d0657b84fd3d423a9eab",
}


@pytest.mark.skipif(not os.environ.get("WOZMOD_DONORS"), reason="set WOZMOD_DONORS to extracted donor folders")
def test_no_pointer_lands_on_a_donor_placeholder_record():
    # Catches a row pointed at a donor's unused placeholder record, which the game then shows as the English.
    from wozmod.text.resolver import DonorResolver

    resolver = DonorResolver(Path(os.environ["WOZMOD_DONORS"]))
    placeholders = {row["key"] for row in json.loads(TABLE.read_text())["rows"] if "ref" in row
                    and re.fullmatch(r"\s*\d*\s*dummy\s*", resolver.resolve_row(row), re.IGNORECASE)}
    assert placeholders - PLACEHOLDER_STOCK == set()


@pytest.mark.skipif(not os.environ.get("WOZMOD_DONORS"), reason="set WOZMOD_DONORS to extracted donor folders")
def test_every_string_pointer_starts_at_a_donor_string():
    # Catches a pointer shifted into the middle of a donor string, or into another language's copy of it, which
    # still resolves to plausible text. A string starts after the NUL that ends the one before it, or in an
    # archive entry after the ffffffff marker that opens its record (an event line's after its id and length).
    from wozmod.text.resolver import DonorResolver

    resolver, mid = DonorResolver(Path(os.environ["WOZMOD_DONORS"])), []
    for row in json.loads(TABLE.read_text())["rows"]:
        for ref in [row.get("ref"), *(edit[2] for edit in row.get("edits", []) if isinstance(edit[2], list))]:
            match ref:
                case [donor, "pe", int(at)]:
                    before = resolver.program(donor)[:at][-1:]
                case [donor, "ens" | "fns", int(entry), "cstr", int(at)]:
                    before = resolver.entry(donor, entry)[:at][-8:]
                case _:
                    continue
            if at and not (before.endswith(b"\0") or b"\xff" * 4 in (before[-4:], before[-8:-4])):
                mid.append(f"{row['key'][:12]} {ref}")
    assert mid == []


# The SHA-256 of the text each row showed before an edit pointer replaced its typed-in words.
EDIT_POINTER_TEXT = {
    "5c409fd1bcfef11d44442680292fe620a0597231c48aea6e10cacccadf4d69f0":
        "b61c47aa06fbfeeb47444ee519ea51e7acffcc685752f948eb5610305b6a8ad1",
}


@pytest.mark.skipif(not os.environ.get("WOZMOD_DONORS"), reason="set WOZMOD_DONORS to extracted donor folders")
def test_rows_that_insert_a_donor_string_read_as_they_did():
    # Catches an edit pointer at the wrong string, or an insert on the wrong side of a space: the screen would show
    # different text from the words the pointer replaced.
    import hashlib

    from wozmod.text.resolver import DonorResolver

    resolver = DonorResolver(Path(os.environ["WOZMOD_DONORS"]))
    rows = {row["key"]: row for row in json.loads(TABLE.read_text())["rows"]
            if any(isinstance(edit[2], list) for edit in row.get("edits", []))}
    assert set(rows) == set(EDIT_POINTER_TEXT)
    built = {key: hashlib.sha256(resolver.resolve_row(row).encode()).hexdigest() for key, row in rows.items()}
    assert built == EDIT_POINTER_TEXT


@pytest.mark.skipif(not (os.environ.get("WOZMOD_DONORS") and os.environ.get("WOZMOD_GAME")),
                    reason="set WOZMOD_DONORS and WOZMOD_GAME")
@pytest.mark.slow_first
def test_the_real_table_builds_from_the_shipped_rows(tmp_path):
    # Catches a row whose donor text the renderer cannot draw or whose stock string is gone, which only the
    # build from real files sees.
    from wozmod.text.build import build_table
    from wozmod.text.resolver import DonorResolver

    built = build_table(Path(os.environ["WOZMOD_GAME"]), DonorResolver(Path(os.environ["WOZMOD_DONORS"])), TABLE,
                        tmp_path / "table.bin")
    assert built["rows"] == len(json.loads(TABLE.read_text())["rows"])


# Western number minus Japanese number. The first Dynasty Warriors was a fighting game, so the Japanese
# musou series counts one behind it; Samurai Warriors kept Japan's numbers.
SERIES_OFFSET = {"Dynasty Warriors": 1, "Samurai Warriors": 0}


@pytest.mark.skipif(not (os.environ.get("WOZMOD_DONORS") and os.environ.get("WOZMOD_GAME")),
                    reason="set WOZMOD_DONORS and WOZMOD_GAME")
def test_series_titles_use_the_western_number():
    # Catches a title that carries the Japanese series number into English, such as an edit that renumbers
    # the official title down by one or a pointer paired to the title with the same digit.
    import unicodedata

    from wozmod.text.psp_exe import EXE_STRING
    from wozmod.text.resolver import DonorResolver
    from wozmod.text.stock import stock_key

    resolver = DonorResolver(Path(os.environ["WOZMOD_DONORS"]))
    # The gallery titles are program strings; scanning only the program keeps this test fast.
    stock = {}
    for match in EXE_STRING.finditer((Path(os.environ["WOZMOD_GAME"]) / "OROCHI_Z.exe").read_bytes()):
        if keyed := stock_key(match.group()):
            stock[keyed[0]] = keyed[1].decode("cp932")
    wrong, checked = [], 0
    for row in json.loads(TABLE.read_text())["rows"]:
        title = re.match(rf"({'|'.join(SERIES_OFFSET)})(?: (\d))?\b", resolver.resolve_row(row))
        if title is None:
            continue
        # The stock title's number ends its first word; a title with none is the first game.
        japanese = unicodedata.digit(stock[row["key"]].split()[0][-1], 1)
        checked += 1
        if int(title[2] or 1) != japanese + SERIES_OFFSET[title[1]]:
            wrong.append((row["key"], title[0]))
    assert checked and wrong == []


@pytest.mark.skipif(not (os.environ.get("WOZMOD_DONORS") and os.environ.get("WOZMOD_GAME")),
                    reason="set WOZMOD_DONORS and WOZMOD_GAME")
@pytest.mark.slow_first
def test_each_shown_notice_and_spoken_line_takes_the_donor_record_its_command_shows():
    # Bugs: a notice or Dream line row paired by its place in its table, so a record MOZ inserts or drops (Dream stage
    # 46's notices) puts every later row one donor record off; a pointer typed one record off; a shared string's row
    # (one that points into another donor file than the stages that show it) whose words none of those stages gives.
    # Oracle: stage_joins and notice_join, which pair records through the commands that show them, never through the
    # tables. Each site (stage, record) a command shows is checked whose row points into a donor stage table. The row's
    # words must be what the join gives that site or another site of the same stock string. Where the join gives a
    # site of the string nothing, the row must not take a donor record the join gives another stock string. Story
    # lines pair as Dream lines do, by the speak command's counterpart (WO1 82.11 shows the line its trigger speaks,
    # decisions ledger text row 22), not by voice suffix.
    from wozmod.donors import DonorsFolder
    from wozmod.formats.linkdata import decoded_member, ps2_members
    from wozmod.formats.scripts import NOTICE, SPEAK, donor_text, parse_script
    from wozmod.text.resolver import DonorResolver
    from wozmod.text.script_alignment import PSP_STAGE_MEMBERS, moz_scripts, stage_joins, wo1_scripts, wo2_x360
    from wozmod.text.stock import stock_key
    from wozmod.voices.build import PSP_TEXT

    folders = DonorsFolder(Path(os.environ["WOZMOD_DONORS"]))
    moz = moz_scripts(Path(os.environ["WOZMOD_GAME"]) / "LINKDATA2")
    members = ps2_members(folders.folder("wo2-psp") / PSP_TEXT, list(PSP_STAGE_MEMBERS.values()))
    psp = {o: parse_script(decoded_member(members[m])) for o, m in PSP_STAGE_MEMBERS.items()}
    joins = stage_joins(moz, wo1_scripts(folders.folder("wo1-x360") / "linkdata_ens"),
                        wo2_x360(folders.folder("wo2-x360") / "linkdata_ens").scripts(), psp)
    rows = {row["key"]: row for row in json.loads(TABLE.read_text())["rows"]}
    resolver = DonorResolver(Path(os.environ["WOZMOD_DONORS"]))

    sites, backs, claims = [], {}, {}
    for o, join in joins.items():
        pairs = join.pairs | join.notices
        shown = {("notices", c[1]) if c[0] == NOTICE else ("dialog", c[2])
                 for event in moz[o].events for c in event if c[0] in (NOTICE, SPEAK)}
        for table in ("notices", "dialog"):
            theirs = getattr(join.donor, table).records
            site = [join.source, "fns" if join.source == "wo2-psp" else "ens", join.entry, table]
            for r, line in enumerate(getattr(moz[o], table).records):
                key = next((k[0] for raw in (line.value[2:], line.text_bytes) if (k := stock_key(raw)) and k[0] in rows),
                           None)
                ref = rows[key].get("ref") if key else None
                if (table, r) not in shown or not ref or len(ref) != 5 or ref[3] not in ("notices", "dialog"):
                    continue
                joined = pairs.get((table, r))
                sites.append((f"{table} {o}.{r} {key[:12]}", key, ref, ref[:4] == site, joined,
                              f"stage{o}.{table[:-1]}{r}"))
                if joined is not None:
                    backs.setdefault(key, set()).add(donor_text(theirs[joined].text_bytes))
                    claims.setdefault((*site, joined), set()).add(key)
    # A reviewed site keeps its row although the join disputes it (data/text/context_exceptions.json, check "notice");
    # an entry whose site no longer fails is stale.
    reviewed = {e["context"] for e in json.loads((TABLE.parent / "context_exceptions.json").read_text())["exceptions"]
                if e["check"] == "notice"}
    wrong, excepted = [], set()
    for name, key, ref, own, joined, context in sites:
        if resolver.resolve(ref) in backs.get(key, ()):
            continue
        failure = None
        if key in backs and (joined is not None or not own):
            failure = f"{name}: row {ref[2]}.{ref[4]}, " + (f"join {joined}" if own else "in another file")
        elif claims.get(tuple(ref), {key}) - {key}:
            failure = f"{name}: row {ref[2]}.{ref[4]}, which the join gives another string"
        if failure and context in reviewed:
            excepted.add(context)
        elif failure:
            wrong.append(failure)
    assert not wrong, f"{len(wrong)} sites: " + "; ".join(wrong)
    assert reviewed <= excepted, f"stale reviewed notices: {sorted(reviewed - excepted)}"


@pytest.mark.skipif(not (os.environ.get("WOZMOD_DONORS") and os.environ.get("WOZMOD_GAME")),
                    reason="set WOZMOD_DONORS and WOZMOD_GAME")
def test_each_briefing_takes_the_donor_string_its_stage_shows():
    # Bugs: a briefing paired by its place among the 40 blocks, so a count one off where the PSP blocks start (block
    # 28, PSP member 61) gives every PSP stage its neighbour's briefing; a briefing pointer typed into the wrong entry.
    # Oracle: the stage each program shows a briefing for, read from the programs: MOZ block s - 0x28, or s - 0x68
    # from 0x84 (0x548C4F to 0x548C5B); WO2 X360 ENS s + 0xF0D - 0xEEA (0x822B05D0); WO2 PSP member s - 11 for its
    # stages from 0x48 (0x2111CC, 0x2111DC), its stage s being the one whose script is member s + 198 (0x1524E4).
    import struct

    from wozmod.formats.linkdata import archive
    from wozmod.formats.scripts import mesc_blocks
    from wozmod.text.script_alignment import PSP_STAGE_MEMBERS
    from wozmod.text.resolver import DonorResolver
    from wozmod.text.stock import stock_key

    donors, game = Path(os.environ["WOZMOD_DONORS"]), Path(os.environ["WOZMOD_GAME"])
    exe = (game / "OROCHI_Z.exe").read_bytes()
    x360, psp = (donors / "wo2-x360/default.pe").read_bytes(), (donors / "wo2-psp/eboot.prx").read_bytes()

    def moz(va: int, size: int) -> int:  # the immediate ending a MOZ instruction of `size` bytes
        from wozmod.formats import pe
        at = pe.file_offset(pe.layout(exe), va)
        return int.from_bytes(exe[at + size - (4 if size == 5 else 1):at + size], "little")

    def ppc(va: int) -> int:  # addi's signed immediate in the flat X360 image
        word = struct.unpack_from(">I", x360, va - 0x82000000)[0]
        assert word >> 26 == 14, hex(va)
        return (word & 0xFFFF) - ((word & 0x8000) << 1)

    def mips(va: int, opcode: int) -> int:  # the signed immediate of a PSP instruction (code segment at file 0x54)
        word = struct.unpack_from("<I", psp, 0x54 + va)[0]
        assert word >> 26 == opcode, hex(va)
        return (word & 0xFFFF) - ((word & 0x8000) << 1)

    split, late, early = moz(0x548C4F, 5), moz(0x548C56, 3), moz(0x548C5B, 3)
    x360_shift = ppc(0x822B05D0) - ppc(0x822B05D4)
    psp_split, psp_late, psp_script = mips(0x2111CC, 10), mips(0x2111DC, 9), mips(0x1524E4, 9)
    rows = {row["key"]: row for row in json.loads(TABLE.read_text())["rows"]}
    resolver = DonorResolver(donors)
    blocks = mesc_blocks(archive(game / "LINKDATA1").read([4190])[4190])
    stages = [*range(early, early + 28), *range(split, split + 12)]
    assert [s - (late if s >= split else early) for s in stages] == list(range(len(blocks)))
    wrong = []
    for e, (block, stage) in enumerate(zip(blocks, stages, strict=True)):
        if stage < split:
            shown = ["wo2-x360", "ens", stage + x360_shift, "mesc", 0, 0]
        else:
            psp_stage = PSP_STAGE_MEMBERS[stage] - psp_script
            assert psp_stage >= psp_split
            shown = ["wo2-psp", "fns", psp_stage + psp_late, "mesc", 0, 0]
        row = rows.get(next((k[0] for k in [stock_key(block[0])] if k), None))
        if row is None or "ref" not in row or resolver.resolve(row["ref"]) != resolver.resolve(shown):
            wrong.append(f"block {e} (stage {stage}): row {row and row.get('ref')}, the donor shows {shown}")
    assert not wrong, "; ".join(wrong)
