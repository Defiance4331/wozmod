"""The game folder check on a small synthetic game: only the pieces the build reads count, found where the player's
own indexes put them."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from synthetic import stock_game
from wozmod.formats import pe
from wozmod.formats.linkdata import LZP2_SIGNATURE, encode_lzp2
from wozmod.install import stock
from wozmod.install.stock import UPDATE_101_FOUND, WRONG_GAME, check_game
from wozmod.runtime import guard_report, guards
from wozmod.runtime.build import build_dll


def _verdict(tmp_path, **changes):
    record = stock_game(tmp_path / "stock")
    stock_game(tmp_path / "game", **changes)
    return check_game(tmp_path / "game", record)


def test_entries_another_mod_resized_or_appended_to_do_not_move_the_pieces(tmp_path):
    # Bug: a piece is found at its stock offset instead of through the player's index, so a mod that grew an entry
    # the build never reads (every later entry moves) stops the install; or the bytes past the last entry do.
    record = stock_game(tmp_path / "stock")
    stock_game(tmp_path / "game", linkdata1={1: b"a much larger replacement " * 400},
               linkdata2={1: (b"now in", b"two parts")})
    with (tmp_path / "game/LINKDATA2.BIN").open("ab") as archive:
        archive.write(b"left past the last entry" * 300)
    verdict = check_game(tmp_path / "game", record)
    assert (verdict.problem(), verdict.changed) == (None, ())


def test_a_changed_entry_the_build_reads_is_named_with_its_file_and_the_steps_that_read_it(tmp_path):
    # Bug: the changed entry passes, the message names the wrong entry or file, or the steps that read it are lost,
    # so an update that rebuilds only the text would stop for an entry only the art reads.
    verdict = _verdict(tmp_path, linkdata1={2: b"another mod's art " * 300})
    assert verdict.problem() == ("LINKDATA1.BIN: another mod changed entry 2, which wozmod reads to build its menu art. "
                                 "Put back the original LINKDATA1.BIN, or remove that mod, then install again.")
    assert [p.at for p in verdict.blocking({"text", "voices"})] == []
    assert [p.at for p in verdict.reused({"text", "voices"})] == [2]


def test_a_file_whose_pieces_mostly_differ_is_named_once_as_not_from_version_1_00(tmp_path):
    # Bug: a file from another release lists every piece as changed by another mod (one line per entry), or the
    # half rule counts the wrong file's pieces.
    verdict = _verdict(tmp_path, linkdata1={2: b"other release " * 300, 3: encode_lzp2(b"other release")})
    assert verdict.problem() == ("LINKDATA1.BIN is not from Musou Orochi Z version 1.00. Reinstall the game, then pick "
                                 "its folder.")


def test_a_missing_file_is_named_before_anything_is_hashed(tmp_path, monkeypatch):
    # Bug: a missing Voice.bin is reported as changed pieces, or the check reads the pieces record first.
    stock_game(tmp_path)
    (tmp_path / "data/Sound/Voice/Voice.bin").unlink()
    monkeypatch.setattr(stock, "recorded", lambda: pytest.fail("the record was read"))
    assert check_game(tmp_path).problem() == ("data/Sound/Voice/Voice.bin is missing. Pick the folder that contains "
                                              "the game's .exe (the Japanese PC release).")


def _exe_header(stamp: int, image_size: int) -> bytes:
    """A PE header as the linker writes it: e_lfanew at 0x3C, the file header (i386) and the optional header."""
    header = bytearray(0x400)
    header[:2], header[0x3C:0x40] = b"MZ", (0x80).to_bytes(4, "little")
    header[0x80:0x86] = b"PE\0\0" + (0x14C).to_bytes(2, "little")
    header[0x88:0x8C] = stamp.to_bytes(4, "little")
    header[0x80 + 24 + 56:0x80 + 24 + 60] = image_size.to_bytes(4, "little")
    return bytes(header)


def test_only_the_1_01_update_exe_is_named_as_the_update(tmp_path):
    # Bug: a game with the 1.01 update is only told its exe is not version 1.00 (so the player reinstalls and
    # patches again), or another exe, or the update's LINKDATA4 beside a 1.00 exe, is called the update.
    record = stock_game(tmp_path)
    exe = (tmp_path / "OROCHI_Z.exe").read_bytes()
    rows = {
        "1.01 exe": (_exe_header(0x4B5E99A7, 0x1194000), None, UPDATE_101_FOUND),
        "1.01 stamp, other image size": (_exe_header(0x4B5E99A7, 0x1196000), None, WRONG_GAME),
        "another build": (_exe_header(0x4AF69DF8, 0x101A000), None, WRONG_GAME),
        "not an exe": (b"MZ", None, WRONG_GAME),
        "1.00 exe and the update's LINKDATA4": (exe, b"update archive", None),
    }
    found = {}
    for name, (image, linkdata4, _) in rows.items():
        (tmp_path / "OROCHI_Z.exe").write_bytes(image)
        (tmp_path / "LINKDATA4.BIN").unlink(missing_ok=True)
        if linkdata4:
            (tmp_path / "LINKDATA4.BIN").write_bytes(linkdata4)
        found[name] = check_game(tmp_path, record).problem()
    assert found == {name: expected for name, (*_, expected) in rows.items()}


def _literal_lzp2(data: bytes) -> bytes:
    """LZP2 as a packer that never back-references writes it: runs of 63 literals, the end token, 16-byte padding."""
    stream = b"".join(bytes([len(data[at:at + 63])]) + data[at:at + 63] for at in range(0, len(data), 63)) + b"\0"
    stream += bytes(-len(stream) % 16)
    return struct.pack("<4s4sII", b"LZP2", LZP2_SIGNATURE, len(data), len(stream)) + stream


@pytest.mark.parametrize("changes, passes", [
    ({3: b"text entry " * 40}, True),  # read unpacked: stored unpacked now, the same bytes
    ({3: _literal_lzp2(b"text entry " * 40)}, True),  # packed again, by another packer
    ({3: encode_lzp2(b"text entry " * 39)}, False),
], ids=["unpacked", "repacked", "other text"])
def test_an_entry_read_unpacked_passes_when_repacked_to_the_same_bytes(tmp_path, changes, passes):
    # Bug: the unpacked hash is never tried, so a mod tool that repacks the archive stops the install, or it is
    # tried on bytes that do not unpack to the same text.
    assert (_verdict(tmp_path, linkdata1=changes).problem() is None) == passes


def test_voice_clips_are_found_through_the_players_voice_index(tmp_path):
    # Bug: clips are found at their stock offsets, so a Voice.bin another mod laid out again fails, or a changed
    # clip the voices read passes.
    assert _verdict(tmp_path, clip_order=(2, 4, 3, 1, 0), clips={2: b"a longer clip " * 99}).problem() is None
    assert _verdict(tmp_path, clips={0: b"another take " * 20}).problem() == (
        "data/Sound/Voice/Voice.bin: another mod changed voice clip 0, which wozmod reads to build its voices. Put back "
        "the original Voice.bin, or remove that mod, then install again.")


def test_only_exe_bytes_inside_the_spans_count(tmp_path):
    # Bug: a span is hashed at the wrong offset or length, so an edit beside it stops the install or one inside it
    # passes.
    assert _verdict(tmp_path, exe={0x220: b"\xcc" * 0xE0, 0x310: b"\xcc"}).problem() is None
    assert _verdict(tmp_path, exe={0x30F: b"\xcc"}).problem() == (
        "OROCHI_Z.exe: another mod changed the bytes at 0x300, which wozmod reads to build its text and menu art. Put "
        "back the original OROCHI_Z.exe, or remove that mod, then install again.")


def test_what_wozmod_replaces_without_reading_it_is_only_noted(tmp_path):
    # Bug: another mod's clips or entry that wozmod serves its own in place of stop the install, go unnoted, or count
    # toward calling Voice.bin a file from another release (a voice mod that replaced most clips).
    verdict = _verdict(tmp_path, clips={1: b"another mod's take " * 9, 2: b"and another", 4: b"and a third"},
                       linkdata2={2: b"another schedule"})
    assert verdict.problem() is None
    assert verdict.notes() == ["Note: another mod also replaces 4 things wozmod replaces (LINKDATA2.BIN: 1 entry; "
                               "Voice.bin: 3 voice clips). wozmod's are used while it is installed."]


STOP = ("OROCHI_Z.exe: another mod changed the game's code {}. wozmod works only with all of its changes, so it was "
        "not installed. Put back the original OROCHI_Z.exe, or remove that mod, then install again.")


@pytest.mark.parametrize("names, expected", [
    ([], None),
    (["frame rate"], STOP.format("where wozmod patches frame rate")),
    (["field of view", "frame rate"], STOP.format("where wozmod patches field of view and frame rate")),
    (["core"], STOP.format("that wozmod hooks into")),
    (["core", "frame rate", "display sizes"], STOP.format(
        "where wozmod patches English text, Game Settings rows, controllers, frame rate, sharp menu art and display sizes")),
    (["served entries", "English voices"], STOP.format(
        "where wozmod patches English menu art and captions and English voices")),
], ids=["none", "one", "two", "core alone", "core with others", "served entries"])
def test_exe_code_another_mod_changed_stops_the_install_naming_what_players_know(tmp_path, monkeypatch, names,
                                                                                  expected):
    # Bugs: changed exe code wozmod patches lets the install through, so the DLL turns all of wozmod off in game; a
    # second feature is dropped from the line; the line says "core" or "served entries", which mean nothing to a
    # player, or names frame rate twice beside core; the guard report is handed the wrong files.
    record = stock_game(tmp_path / "stock")
    stock_game(tmp_path / "game")
    (tmp_path / "d3d9.dll").write_bytes(b"the dll")
    exe = (tmp_path / "game/OROCHI_Z.exe").read_bytes()

    def refused_features(exe_bytes, dll_bytes):
        assert (exe_bytes, dll_bytes) == (exe, b"the dll")
        return names

    monkeypatch.setattr(guard_report, "refused_features", refused_features)
    assert check_game(tmp_path / "game", record, tmp_path / "d3d9.dll").problem() == expected


def test_exe_code_another_mod_changed_stops_an_update_that_rebuilds_no_step(tmp_path, monkeypatch):
    # Bug: an update that reuses every build step (row 31: it stops only for pieces its rebuilt steps read) lets
    # changed exe code through, though the DLL it installs would turn all of wozmod off.
    record = stock_game(tmp_path / "stock")
    stock_game(tmp_path / "game")
    (tmp_path / "d3d9.dll").write_bytes(b"the dll")
    monkeypatch.setattr(guard_report, "refused_features", lambda exe, dll: ["frame rate"])
    verdict = check_game(tmp_path / "game", record, tmp_path / "d3d9.dll")
    assert verdict.problem(rebuilt=()) == STOP.format("where wozmod patches frame rate")


@pytest.fixture(scope="module")
def stock_and_dll(tmp_path_factory):
    """The stock game folder and the release DLL built from this checkout (the one wozmod.install.DLL pins)."""
    if os.environ.get("WOZMOD_RUNTIME_COMPILE") != "1" or not os.environ.get("WOZMOD_GAME"):
        pytest.skip("set WOZMOD_RUNTIME_COMPILE=1 and WOZMOD_GAME")
    game = Path(os.environ["WOZMOD_GAME"])
    return game, build_dll(game / "OROCHI_Z.exe", tmp_path_factory.mktemp("dll") / "d3d9.dll")


def test_another_mods_edit_of_the_code_wozmod_patches_stops_the_install(tmp_path, stock_and_dll):
    # Bug: with the DLL the release ships, another mod's edit of the frame rate limiter (0x5CA36D) lets the install
    # through (the DLL then turns all of wozmod off in game), or names other features than frame rate.
    game, dll = stock_and_dll
    copy = tmp_path / "game"
    copy.mkdir()
    for name in stock.NEEDED:
        (copy / name).parent.mkdir(parents=True, exist_ok=True)
        (copy / name).symlink_to(game / name)
    exe = bytearray((game / "OROCHI_Z.exe").read_bytes())
    exe[pe.file_offset(pe.layout(bytes(exe)), 0x5CA36D)] ^= 0xFF  # push 60 in the frame limiter, a frame rate guard
    (copy / "OROCHI_Z.exe").unlink()
    (copy / "OROCHI_Z.exe").write_bytes(exe)
    fps = next(f.name for f in guards.FEATURES if f.key == "fps")
    assert check_game(copy, dll=dll).problem() == STOP.format(f"where wozmod patches {fps}")
