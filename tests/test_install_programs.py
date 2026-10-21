"""The program files: the player's decryption programs run on the disc image's encrypted files, and the output is pinned."""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import replace
from pathlib import Path

import pytest

import wozmod.donors as donors
import wozmod.install as installer
from wozmod.donors import ProgramIdentity
from wozmod.install import InstallError, decrypt_with_tool
from test_donors_programs import ENCRYPTED, PAYLOAD as DECRYPTED, fake



def _xbox_image(tmp_path: Path, name: bytes, content: bytes) -> Path:
    """A one-file Xbox 360 disc image."""
    image = bytearray(34 * 2048 + len(content) + 2048)
    descriptor = memoryview(image)[32 * 2048:33 * 2048]
    descriptor[:20] = descriptor[-20:] = b"MICROSOFT*XBOX*MEDIA"
    struct.pack_into("<II", descriptor, 20, 33, 2048)
    record = struct.pack("<HHIIBB", 0, 0, 34, len(content), 0x20, len(name)) + name
    image[33 * 2048:33 * 2048 + len(record)] = record
    image[34 * 2048:34 * 2048 + len(content)] = content
    path = tmp_path / "x360.iso"
    path.write_bytes(image)
    return path


def pin_synthetic_programs(monkeypatch) -> None:
    """Pin the synthetic programs in place of both Xbox 360 discs' real ones, and forget earlier runs."""
    installer._TOOL_RUNS.clear()
    for disc in ("x360", "wo1-x360"):
        plain, encrypted = (ProgramIdentity(disc, len(content), hashlib.sha256(content).hexdigest(), "x360")
                            for content in (DECRYPTED, ENCRYPTED))
        each = donors.DISCS[disc]
        monkeypatch.setitem(donors.DISCS, disc, replace(each, program=replace(each.program, plain=plain, encrypted=encrypted)))


@pytest.fixture(autouse=True)
def pins(monkeypatch):
    pin_synthetic_programs(monkeypatch)
    check = installer.check_input  # only the decrypted-file check is under test: every other input passes
    monkeypatch.setattr(installer, "check_input", lambda name, path: check(name, path) if name in installer.FILE_PROGRAMS else None)


pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake programs are scripts with a #! line")
COUNT = "Path(sys.argv[0] + '.runs').open('a').write('x')"


def _runs(tool: Path) -> int:
    runs = Path(str(tool) + ".runs")
    return len(runs.read_text()) if runs.exists() else 0


def test_the_programs_output_is_what_the_text_build_reads_and_the_program_runs_once(tmp_path):
    # Bug: the file lands under the wrong name, one Xbox 360 disc's program is never made, the program runs
    # again for the check, the window and the build (minutes lost on each), or its padding is installed with it.
    image = _xbox_image(tmp_path, b"default.xex", ENCRYPTED)
    tool = fake(tmp_path / "xextool", "xextool", zeros=32_768, body=COUNT)
    for folder in ("wo2-x360", "wo1-x360", "wo2-psp"):
        (tmp_path / "donors" / folder).mkdir(parents=True)

    assert decrypt_with_tool("x360", tool, image) == DECRYPTED  # the window's check
    assert decrypt_with_tool("x360", tool, image) == DECRYPTED  # the install's check
    with pytest.raises(InstallError):  # no PSP program given: nothing is placed
        installer._place_programs({"x360-tool": tool, "x360": image, "wo1-x360": image, "psp-tool": None, "psp": None},
                                  tmp_path / "donors")
    assert (tmp_path / "donors/wo2-x360/default.pe").read_bytes() == DECRYPTED
    assert (tmp_path / "donors/wo1-x360/default.pe").read_bytes() == DECRYPTED
    assert _runs(tool) == 2  # once for each Xbox 360 disc


@pytest.mark.parametrize("name, content, expected", [
    (b"default.xex", ENCRYPTED, None),
    (b"default.xex", DECRYPTED, "default.xex on the disc image is not the supported file."),
    (b"other.xex", ENCRYPTED, "default.xex on the disc image is not the supported file."),
])
def test_only_the_pinned_encrypted_file_from_the_image_is_given_to_the_program(tmp_path, name, content, expected):
    # Bug: the program is run on a same-size or already decrypted file, or on the wrong member.
    tool = fake(tmp_path / "tool", "xex2ool")
    found = decrypt_with_tool("x360", tool, _xbox_image(tmp_path, name, content))
    assert found == DECRYPTED if expected is None else found == expected


def test_wrong_output_gets_the_plain_message_and_the_install_stops_before_building(tmp_path, monkeypatch):
    # Bug: a program with the wrong output is accepted, the build starts and fails minutes later, or the
    # message does not say which program failed.
    game = tmp_path / "game"
    game.mkdir()
    image = _xbox_image(tmp_path, b"default.xex", ENCRYPTED)
    tool = fake(tmp_path / "tool", "xex2ool", produces=b"X" * len(DECRYPTED))
    # the build runs while the checks finish: it must not go on past them
    monkeypatch.setattr(installer, "_build", lambda *args: (args[-1](), pytest.fail("the build must not go on past the checks"))[1])
    monkeypatch.setattr(installer.resources, "ffmpeg", lambda: "ffmpeg")
    inputs = {"wo1": tmp_path, "wo1-x360": image, "x360": image, "psp": tmp_path, "x360-tool": tool, "psp-tool": tmp_path,
              "lettering": tmp_path, "dll": tmp_path}
    monkeypatch.setattr(installer, "check_input", lambda name, path: None if name != "psp" else "Not the PSP disc.")
    with pytest.raises(InstallError) as caught:
        installer.install(game, inputs, log=lambda _: None)
    assert "Xbox 360 decryption program: This program did not produce the file wozmod needs." in str(caught.value)


def test_the_install_refuses_to_start_without_both_programs(tmp_path, monkeypatch):
    # Bug: a missing program is skipped (the build then reads a file that is not there) or only one is named.
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr(installer, "_build", lambda *args: (args[-1](), pytest.fail("the build must not go on past the checks"))[1])
    monkeypatch.setattr(installer.resources, "ffmpeg", lambda: "ffmpeg")
    inputs = {"wo1": tmp_path, "wo1-x360": tmp_path, "x360": tmp_path, "psp": tmp_path, "x360-tool": None, "psp-tool": None,
              "lettering": tmp_path, "dll": tmp_path}
    with pytest.raises(InstallError) as caught:
        installer.install(game, inputs, log=lambda _: None)
    assert "Xbox 360 decryption program: Pick a decryption program, or a decrypted file under Advanced." in str(caught.value)
    assert "PSP decryption program: Pick a decryption program, or a decrypted file under Advanced." in str(caught.value)


@pytest.mark.parametrize("extra, accepted", [(65_536, True), (65_537, False)])
def test_the_tail_limit_after_the_program_is_exactly_64_kib(extra, accepted):
    # Bug: the limit is off by one, or any file of the right length passes without the prefix hash.
    identity = donors.DISCS["x360"].program.plain
    assert (donors.trim_program(DECRYPTED + bytes(extra), identity) == DECRYPTED) is accepted
    assert donors.trim_program(b"E" + DECRYPTED[1:] + bytes(10), identity) is None


@pytest.mark.parametrize("disc, image, pin", [("x360", "WOZMOD_X360_ISO", "X360_PROGRAM"),
                                              ("wo1-x360", "WOZMOD_WO1_X360_ISO", "WO1_X360_PROGRAM")])
def test_a_real_program_makes_the_pinned_xbox_360_file(monkeypatch, disc, image, pin):
    # Bug: a profile that works on the fakes has the wrong arguments for the real program, or a disc's pin
    # is not what the real program makes from it (every install of that disc would fail).
    if not (os.environ.get(image) and os.environ.get("WOZMOD_X360_TOOL")):
        pytest.skip(f"set {image} and WOZMOD_X360_TOOL")
    monkeypatch.undo()
    installer._TOOL_RUNS.clear()
    found = decrypt_with_tool(disc, os.environ["WOZMOD_X360_TOOL"], os.environ[image])
    assert isinstance(found, bytes) and len(found) == getattr(donors, pin).size


def test_the_check_and_the_build_asking_at_once_run_the_program_once(tmp_path):
    # Bug: the input check (on its own thread) and the build both miss the kept result and run the
    # player's program twice at the same time.
    from concurrent.futures import ThreadPoolExecutor

    runs = tmp_path / "runs"
    tool = fake(tmp_path / "tool", "xex2ool", body=f"import time; open({str(runs)!r}, 'a').write('x'); time.sleep(0.3)")
    image = _xbox_image(tmp_path, b"default.xex", ENCRYPTED)
    with ThreadPoolExecutor(2) as pool:
        found = list(pool.map(lambda _: decrypt_with_tool("x360", tool, image), range(2)))
    assert found == [DECRYPTED, DECRYPTED]
    assert runs.read_text() == "x"


def _marker_tool(tmp_path: Path) -> Path:
    """A program that leaves a mark when run and would then fail."""
    return fake(tmp_path / "marker-tool", "xex2ool", body=COUNT + "; sys.exit(9)")


def _decrypted_file(tmp_path: Path, tail: bytes = b"") -> Path:
    path = tmp_path / "plain.pe"
    path.write_bytes(DECRYPTED + tail)
    return path


def test_a_decrypted_file_is_accepted_with_its_tail_and_never_run(tmp_path):
    # Bug: the "file" is handed to the program route (an executable script runs), or its padding is installed.
    script = tmp_path / "plain.pe"  # executable and wrong to run: it is also the decrypted content
    script.write_bytes(DECRYPTED + bytes(32_768))
    script.chmod(0o755)
    assert installer.resolve_program("x360", {"x360-file": script, "x360": None}) == ("x360-file", DECRYPTED)


@pytest.mark.parametrize("content", [b"E" + DECRYPTED[1:], DECRYPTED[:-1], DECRYPTED + bytes(65_537), ENCRYPTED])
def test_a_wrong_file_is_refused_with_the_plain_message_and_the_install_stops_before_building(tmp_path, monkeypatch, content):
    # Bug: any file of about the right size passes, or the install goes on to build.
    bad = tmp_path / "bad"
    bad.write_bytes(content)
    assert installer.resolve_program("x360", {"x360-file": bad}) == ("x360-file", installer.WRONG_FILE)
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr(installer, "_build", lambda *args: (args[-1](), pytest.fail("the build must not go on past the checks"))[1])
    monkeypatch.setattr(installer.resources, "ffmpeg", lambda: "ffmpeg")
    inputs = {"wo1": tmp_path, "x360": _xbox_image(tmp_path, b"default.xex", ENCRYPTED), "psp": tmp_path,
              "wo1-x360": tmp_path, "wo1-x360-file": _decrypted_file(tmp_path), "x360-file": bad, "x360-tool": None,
              "psp-tool": None, "lettering": tmp_path, "dll": tmp_path}
    with pytest.raises(InstallError) as caught:
        installer.install(game, inputs, log=lambda _: None)
    assert "Already decrypted Warriors Orochi 2 (Xbox 360, USA) program file: This is not the decrypted file wozmod needs." in str(caught.value)


def test_a_decrypted_file_beside_the_program_is_refused_and_nothing_runs(tmp_path):
    # Bug: both are given and one silently wins, or the program runs (minutes lost) before the refusal.
    marker = _marker_tool(tmp_path)
    inputs = {"x360": _xbox_image(tmp_path, b"default.xex", ENCRYPTED), "x360-tool": marker,
              "x360-file": _decrypted_file(tmp_path)}
    assert installer.resolve_program("x360", inputs) == ("x360-file", installer.FILE_AND_PROGRAM)
    assert _runs(marker) == 0


def test_the_disc_images_decrypted_copy_refuses_a_file_beside_it_and_the_encrypted_one_covers_nothing(tmp_path):
    # Bug: an image that already holds the decrypted file still asks for a program, or takes a second decrypted file
    # in silence; or one that holds only the encrypted file is taken as covered.
    marker = _marker_tool(tmp_path)
    other = tmp_path / "other"
    other.write_bytes(b"not it")
    held = _xbox_image(tmp_path, b"default.xex", DECRYPTED + bytes(100))
    assert installer.resolve_program("x360", {"x360": held, "x360-file": other}) == ("x360-file", installer.FILE_IN_IMAGE)
    # the program may still be needed for the other Xbox 360 disc: here it is not run
    assert installer.resolve_program("x360", {"x360": held, "x360-tool": marker}) == ("x360", DECRYPTED)
    assert installer.resolve_program("x360", {"x360": held}) == ("x360", DECRYPTED)  # no program needed
    assert _runs(marker) == 0
    encrypted_only = tmp_path / "enc"
    encrypted_only.mkdir()
    source, found = installer.resolve_program("x360", {"x360": _xbox_image(encrypted_only, b"default.xex", ENCRYPTED)})
    assert (source, found) == ("x360-tool", "Pick a decryption program, or a decrypted file under Advanced.")


def test_neither_a_program_nor_a_file_refuses_before_building_with_the_plain_message(tmp_path):
    # Bug: with no program and no file the pair is skipped and the build reads a missing file.
    (tmp_path / "donors/wo2-x360").mkdir(parents=True)
    with pytest.raises(InstallError, match="Pick a decryption program, or a decrypted file under Advanced."):
        installer._place_programs({"x360": _xbox_image(tmp_path, b"default.xex", ENCRYPTED)}, tmp_path / "donors")
