"""One verdict per decryption program input: the install, the command line and the setup window word it alike.

The rule (each disc on its own and from one source, each problem on the input that caused it, the program needed
only for a disc with no usable decrypted file) is written out below from the input states, not from the code under
test.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import wozmod.donors as donors
import wozmod.install as installer
from wozmod import __main__ as cli
from wozmod import window
from wozmod.donors import ProgramIdentity
from test_donors_programs import ENCRYPTED, PAYLOAD as DECRYPTED, fake

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake programs are scripts with a #! line")

NEITHER = "Pick a decryption program, or a decrypted file under Advanced."
IMAGE_FIRST = "Pick the right disc image first."
NOT_PRODUCED = ("This program did not produce the file wozmod needs. Check that you picked the right program for this "
                "game. The online guide lists the programs we tested.")
WRONG_FILE = ("This is not the decrypted file wozmod needs. Pick the file your decryption program made, "
              "or give wozmod the program instead.")
FILE_IN_IMAGE = "Not needed: your disc image already has the decrypted file. Clear this box."
FILE_AND_PROGRAM = "You also gave the decryption program. Give one or the other: clear this box or the program."
PROGRAM_NOT_NEEDED = {1: FILE_IN_IMAGE,
                      2: "Not needed: your disc images already have the decrypted files. Clear this box."}
NOT_NEEDED = {"disc": "Not needed: your disc image already has the decrypted file",
              "file": "Not needed: using the decrypted file under Advanced"}
IMAGES = ("missing", "wrong", "encrypted", "decrypted")  # wrong: not this disc (and so no copy of the file)
FILES = ("none", "good", "wrong")
PROGRAMS = ("none", "good", "bad")


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Synthetic pins, images that hold the encrypted or the decrypted file, and fake programs; nothing else runs."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.setattr(installer, "_TOOL_RUNS", {})
    for disc, platform in (("wo1-x360", "x360"), ("wo2-x360", "x360"), ("wo2-psp", "psp")):
        plain, encrypted = (ProgramIdentity(disc, len(content), hashlib.sha256(content).hexdigest(), platform)
                            for content in (DECRYPTED, ENCRYPTED))
        each = donors.DISCS[disc]
        monkeypatch.setitem(donors.DISCS, disc, replace(each, program=replace(each.program, plain=plain, encrypted=encrypted)))

    def check_donor_image(image, donor):
        if Path(image).name.startswith("wrong"):
            raise ValueError("not this disc")

    monkeypatch.setattr(donors, "check_donor_image", check_donor_image)
    monkeypatch.setattr(donors, "read_disc_member", lambda image, donor, member: Path(image).read_bytes())
    release = tmp_path / "release"
    release.write_bytes(b"r")
    monkeypatch.setattr(installer, "LETTERING", (1, hashlib.sha256(b"r").hexdigest()))
    monkeypatch.setattr(installer, "DLL", installer.LETTERING)
    monkeypatch.setattr(installer.resources, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "install", lambda game, inputs, **_: installer.check_inputs(game, inputs))  # no build
    paths = {"release": str(release), "game": str(tmp_path / "no game")}
    for name, content in (("wrong.iso", ENCRYPTED), ("encrypted.iso", ENCRYPTED), ("decrypted.iso", DECRYPTED),
                          ("good.file", DECRYPTED), ("wrong.file", b"x")):
        (tmp_path / name).write_bytes(content)
        paths[name] = str(tmp_path / name)
    for tool, shape in (("x360-tool", "xextool"), ("psp-tool", "psp")):
        paths[f"{tool}.good"] = str(fake(tmp_path / f"{tool}-good", shape))
        paths[f"{tool}.bad"] = str(fake(tmp_path / f"{tool}-bad", shape, produces=b"junk"))
    return paths


def values(world, tool: str, discs, program: str) -> dict[str, str]:
    """The window's values for one case ("" is an empty row). The other program's discs hold the decrypted file."""
    found = {name: "" for name in (*installer.PROGRAMS, *installer.TOOLS, *installer.FILES.values())}
    found.update(game=world["game"], lettering=world["release"], dll=world["release"])
    for disc in installer.PROGRAMS:
        found[disc] = world["decrypted.iso"]
    for disc, (image, file) in zip(installer.TOOLS[tool], discs, strict=True):
        found[disc] = "" if image == "missing" else world[f"{image}.iso"]
        found[installer.FILES[disc]] = "" if file == "none" else world[f"{file}.file"]
    found[tool] = "" if program == "none" else world[f"{tool}.{program}"]
    return found


def rule(tool: str, discs, program: str) -> tuple[dict[str, str], str | None, bool]:
    """(each problem on the input that caused it, what covers every disc or None, whether the program made a file)."""
    problems, covered, made = {}, [], False
    for disc, (image, file) in zip(installer.TOOLS[tool], discs, strict=True):
        if file != "none" and image == "decrypted":  # two sources: the image's copy stands, the file is refused
            problems.setdefault(installer.FILES[disc], FILE_IN_IMAGE)
            covered.append("disc")
        elif file != "none" and program != "none":
            problems.setdefault(installer.FILES[disc], FILE_AND_PROGRAM)
        elif file == "wrong":
            problems.setdefault(installer.FILES[disc], WRONG_FILE)
        elif file == "good":
            covered.append("file")
        elif image == "decrypted":
            covered.append("disc")
        elif program == "none":
            problems.setdefault(tool, NEITHER)
        elif image in ("missing", "wrong"):
            problems.setdefault(disc, IMAGE_FIRST)
        elif program == "bad":
            problems.setdefault(tool, NOT_PRODUCED)
        else:
            made = True
    whole = len(covered) == len(discs)
    if whole and program != "none":
        problems[tool] = PROGRAM_NOT_NEEDED[len(discs)]
    return problems, ("disc" if set(covered) == {"disc"} else "file") if whole else None, made


def lines_for(tool: str, text: str) -> dict[str, str]:
    """The install's error lines for the program and its decrypted files, by input."""
    names = (tool, *(installer.FILES[disc] for disc in installer.TOOLS[tool]))
    return {name: line.split(": ", 1)[1] for line in text.splitlines() for name in names
            if line.removeprefix("wozmod: ").startswith(installer.INPUTS[name].label + ": ")}


def install_says(tool: str, given: dict[str, str]) -> dict[str, str]:
    try:
        installer.check_inputs(Path(given["game"]), {name: Path(path) if path else None for name, path in given.items()})
    except installer.InstallError as error:
        return lines_for(tool, str(error))
    return {}


def cli_says(tool: str, given: dict[str, str], monkeypatch, capsys, typed: str | None = None):
    """Run `wozmod install` with every non-empty value on the command line; with `typed`, in a terminal that answers
    the first question with it. Returns (exit code, what it printed, what it printed as an error)."""
    argv = ["install", *itertools.chain.from_iterable((f"--{name}", path) for name, path in given.items() if path)]
    monkeypatch.setattr(sys, "stdin", Terminal(typed + "\n") if typed else io.StringIO(""))
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def prompt_answer(out: str) -> str:
    """What the terminal said about the first answer it checked."""
    return out.split("  checking...\n", 1)[1].splitlines()[0].strip()


def test_the_install_the_command_line_and_the_window_follow_one_rule_for_each_program(world, monkeypatch, capsys):
    """Bug: one front end re-implements the decision, so the window ticks a program the install refuses, the command
    line asks for a program a decrypted copy makes unnecessary, or a problem is shown on the wrong input."""
    failures = []
    for tool in installer.TOOLS:
        for discs in itertools.product(itertools.product(IMAGES, FILES), repeat=len(installer.TOOLS[tool])):
            for program in PROGRAMS:
                given = values(world, tool, discs, program)
                problems, covered, made = rule(tool, discs, program)
                said = {}
                if program == "none":
                    row = None, NOT_NEEDED.get(covered, "")
                else:
                    own = problems.get(tool) or (IMAGE_FIRST if IMAGE_FIRST in problems.values() else None)
                    row = (own, "") if own else (None, (f"OK. It made the file{'s' * (len(discs) > 1)} wozmod needs."
                                                        if made else ""))
                said[f"window {tool}"] = (window.check_row(tool, given), row)
                for disc, (image, file) in zip(installer.TOOLS[tool], discs, strict=True):
                    if file != "none":
                        name = installer.FILES[disc]
                        said[f"window {name}"] = (window.check_row(name, given), (problems.get(name), "OK"))
                if "missing" not in (image for image, _ in discs):  # both front ends ask for every disc image
                    lines = {name: problem for name, problem in problems.items() if name not in installer.DISCS}
                    said["install"] = (install_says(tool, given), lines)
                    asked = "missing" if lines.get(tool) == NEITHER else lines
                    if program == "none":
                        code, _, err = cli_says(tool, given, monkeypatch, capsys)
                        said["command line"] = (("missing" if code == 2 and f"--{tool} or" in err else lines_for(tool, err)),
                                                asked)
                    elif rule(tool, discs, "none")[0].get(tool) == NEITHER:  # without it, the terminal asks for it
                        _, out, _ = cli_says(tool, {**given, tool: ""}, monkeypatch, capsys, typed=given[tool])
                        said["terminal"] = (prompt_answer(out), row[0] or "✓ OK")  # what the program row says
                failures += [f"{tool} {discs} program={program}: {front} said {got!r}, the rule says {want!r}"
                             for front, (got, want) in said.items() if got != want]
    assert not failures, "\n".join(failures)


def test_a_program_asked_for_while_a_disc_image_is_wrong_is_asked_once(world, monkeypatch, capsys):
    """Bug: the terminal re-checks the program against a disc image the answer cannot change, so it asks forever; or it
    ticks a program it could not run on any disc."""
    given = values(world, "psp-tool", [("wrong", "none")], "none")
    code, out, err = cli_says("psp-tool", given, monkeypatch, capsys, typed=world["psp-tool.good"])
    assert out.count("PSP decryption program you already have") == 1 and prompt_answer(out) == IMAGE_FIRST
    assert code == 1 and "Warriors Orochi 2 (PSP, USA) disc image: Not the Warriors Orochi 2 (PSP, USA) disc image." in err
    assert IMAGE_FIRST not in err and "cancelled" not in err
    assert window.check_row("psp-tool", {**given, "psp-tool": world["psp-tool.good"]}) == (IMAGE_FIRST, "")
