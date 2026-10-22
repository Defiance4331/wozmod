"""The setup window: it shows the installer's own verdicts, remembers paths, and reports how a run ended."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter
from pathlib import Path

import pytest

import wozmod.install as installer
from wozmod import __main__ as cli, remembered, window
from wozmod.install import InstallError


@pytest.fixture(autouse=True)
def config_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))


def test_the_window_and_the_command_give_the_same_verdict_on_each_input(tmp_path, monkeypatch):
    # Bug: the window re-implements a check, so a file it ticks is refused by the install (or the reverse).
    monkeypatch.setattr(installer, "DLL", (3, "0" * 64))
    monkeypatch.setattr(installer.resources, "ffmpeg", lambda: "ffmpeg")
    wrong = tmp_path / "wrong"
    wrong.write_bytes(b"xyz")
    values = {"game": str(tmp_path), "wo1-x360": str(wrong), "wo2-x360": str(tmp_path / "none"), "wo2-psp": str(wrong),
              "lettering": str(wrong), "dll": str(wrong)}

    def same_verdicts(names):
        with pytest.raises(InstallError) as caught:
            installer.check_inputs(tmp_path, {name: Path(path) for name, path in values.items()})
        lines = str(caught.value).splitlines()
        for name in names:
            problem, _ = window.check_row(name, values)
            assert problem, name
            assert f"{installer.INPUTS[name].label}: {problem}" in lines, name

    same_verdicts(("lettering", "dll"))  # the release files are checked first, on their own
    monkeypatch.setattr(installer, "LETTERING", (3, hashlib.sha256(b"xyz").hexdigest()))
    monkeypatch.setattr(installer, "DLL", installer.LETTERING)
    same_verdicts(("game", "wo1-x360", "wo2-x360", "wo2-psp"))


def test_settings_survive_a_restart_and_a_broken_file_is_ignored():
    # Bug: the chosen paths are lost, or a half-written settings file stops the window from opening.
    remembered.save_settings({"game": "/some/folder"})
    assert remembered.load_settings() == {"game": "/some/folder"}
    remembered.settings_path().write_text("{not json")
    assert remembered.load_settings() == {}


def test_paths_v0_1_0_remembered_come_back_under_the_disc_names_used_now():
    # Bug: v0.1.0 remembered the Warriors Orochi 2 discs and their decrypted files as x360, psp, x360-file and
    # psp-file; after the rename a returning player finds those four rows empty and has to pick each again.
    remembered.save_settings({"game": "/g", "x360": "/a.iso", "psp": "/b.iso", "x360-file": "/c", "psp-file": "/d"})
    assert remembered.load_settings() == {"game": "/g", "wo2-x360": "/a.iso", "wo2-psp": "/b.iso",
                                          "wo2-x360-file": "/c", "wo2-psp-file": "/d"}


@pytest.fixture(scope="session")
def screen():
    """The display the window tests draw on. On Linux it is a private Xvfb, so the scroll and keyboard tests, which
    must show the window, never open it on the developer's desktop; elsewhere it is the usual one."""
    if sys.platform != "linux":
        yield None
        return
    if not shutil.which("Xvfb"):
        pytest.skip("install Xvfb (Fedora: xorg-x11-server-Xvfb) to run the window tests off the desktop")
    read, write = os.pipe()
    xvfb = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                            pass_fds=(write,), stderr=subprocess.DEVNULL)
    os.close(write)
    with os.fdopen(read) as ready:
        number = ready.readline().strip()  # Xvfb writes its display number once it accepts clients
    try:
        if not number:
            pytest.skip("Xvfb did not start")
        yield f":{number}"
    finally:
        xvfb.terminate()
        xvfb.wait()


@pytest.fixture
def shown(monkeypatch, screen):
    try:
        root = tkinter.Tk(screenName=screen)
    except tkinter.TclError:
        pytest.skip("no display for Tk")
    root.withdraw()
    win = window.SetupWindow(root)
    yield win, root
    root.after_cancel(win.pump_id)
    root.destroy()


def _wait(root, until):
    deadline = time.monotonic() + 10
    while not until() and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    assert until()


def _row_label(win, name: str) -> str:
    """The name the window shows on the left of an input's path box."""
    box = win.frame.grid_slaves(row=int(win.entries[name].grid_info()["row"]), column=0)[0]
    return box.winfo_children()[0].cget("text")


def test_every_input_has_a_command_line_option_a_window_row_and_an_install_key(shown, tmp_path, monkeypatch):
    """Bug: an input added to the window but not to the command line (or the reverse), or a front end that never
    hands a path the player gave to the install, so the install refuses an input the player did give."""
    win, root = shown
    paths = {name: tmp_path / name for name in installer.INPUTS}
    given = {}

    def install(game, inputs, **kwargs):
        given[front] = {"game": Path(game), **inputs}
        return {}

    monkeypatch.setattr(cli, "install", install)
    monkeypatch.setattr(window, "install", install)
    front = "command line"
    assert cli.main(["install", *(f"--{name}={path}" for name, path in paths.items())]) == 0
    front = "window"
    rows = {name for name in paths if name in win.entries}
    win.work("install", {name: str(paths[name]) for name in rows}, threading.Event())
    failures = [f"{name}: the command line hands the install {given['command line'].get(name)}"
                for name, path in paths.items() if given["command line"].get(name) != path]
    for name, path in paths.items():
        if name in ("lettering", "dll"):  # the window takes the release files from beside itself
            if name not in given["window"]:
                failures.append(f"{name}: the window never hands it to the install")
        elif name not in rows:
            failures.append(f"{name}: no window row")
        elif given["window"].get(name) != path:
            failures.append(f"{name}: the window hands the install {given['window'].get(name)}")
    assert not failures, "\n".join(failures)


def test_every_place_names_each_disc_the_same_way(shown, tmp_path, monkeypatch, capsys):
    """Bug: one place keeps an old or mistyped disc name, or names the other disc, so the error line, the progress,
    --help and the window call one disc by different names."""
    import wozmod.donors
    from wozmod.progress import Progress

    win, root = shown
    titles = {"wo1-x360": "Warriors Orochi (Xbox 360, USA)", "wo2-x360": "Warriors Orochi 2 (Xbox 360, USA)",
              "wo2-psp": "Warriors Orochi 2 (PSP, USA)"}
    files = {"wo1-x360": ("wo1-x360-file", "x360-tool", "program file", "program file"),
             "wo2-x360": ("wo2-x360-file", "x360-tool", "program file", "program file"),
             "wo2-psp": ("wo2-psp-file", "psp-tool", "EBOOT", "EBOOT.BIN")}
    monkeypatch.setenv("COLUMNS", "200")
    with pytest.raises(SystemExit):
        cli.main(["install", "--help"])
    help_text = capsys.readouterr().out

    wrong = tmp_path / "wrong"
    wrong.write_bytes(b"xyz")
    monkeypatch.setattr(installer, "LETTERING", (3, hashlib.sha256(b"xyz").hexdigest()))
    monkeypatch.setattr(installer, "DLL", installer.LETTERING)
    with pytest.raises(InstallError) as caught:
        installer.check_inputs(tmp_path, {name: wrong for name in installer.INPUTS})
    error_lines = str(caught.value).splitlines()

    shown_lines = set()
    monkeypatch.setattr(installer, "check_inputs", lambda *args: None)

    def extract_donor(*args, on_bytes, **kwargs):
        time.sleep(0.11)  # the progress takes at most ten reports a second
        on_bytes(1, 2)

    monkeypatch.setattr(wozmod.donors, "extract_donor", extract_donor)

    def stop(*args):
        raise InstallError("stop")

    monkeypatch.setattr(installer, "_place_programs", stop)
    (tmp_path / "game").mkdir()
    with pytest.raises(InstallError, match="^stop$"):
        installer.install(tmp_path / "game", {name: wrong for name in installer.INPUTS}, log=lambda _: None,
                          progress=Progress(lambda p: shown_lines.update(text for *_, text in p.lines())))

    win.set_advanced(True)
    failures = []
    for disc, title in titles.items():
        file, tool, what, help_what = files[disc]
        rows = [
            ("error line", f"{title} disc image: Not the {title} disc image. Pick the .iso made from that disc.",
             error_lines),
            ("progress", f"reading the {title} disc", shown_lines),
            ("--help", f"{title} disc image", _help(help_text, disc)),
            ("--help", f"advanced: in place of --{tool}, the {title} {help_what} already decrypted", _help(help_text, file)),
            ("window row", title, [_row_label(win, disc)]),
            ("Advanced row", f"Already decrypted {title} {what}", [_row_label(win, file)]),
        ]
        failures += [f"{disc} {place}: {expected!r} not in {list(found)}" for place, expected, found in rows
                     if expected not in found]
    assert not failures, "\n".join(failures)


def _help(text: str, name: str) -> list[str]:
    """The --help text of one option, on one line."""
    found = re.search(rf"^  --{name} \S+\s+(.+)$", text, re.MULTILINE)
    return [found[1]] if found else []


def test_a_fresh_window_cannot_install_and_says_where_the_files_come_from(shown):
    # Bug: Install is clickable with nothing picked, or the own-copies notice is missing.
    win, root = shown
    assert "disabled" in win.install_button.state() and "disabled" in win.uninstall_button.state()
    assert [w.cget("text") for w in win.frame.winfo_children() if w.winfo_class() == "TLabel"
            ].count(window.NOTICE) == 1



def test_the_window_shows_the_released_version(shown):
    # Bug: the version label is missing, or shows a version other than the release's.
    import tomllib
    win, root = shown
    version = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["version"]
    labels = [w.cget("text") for w in win.frame.winfo_children() if w.winfo_class() == "TLabel"]
    assert f"wozmod {version}" in labels

@pytest.mark.parametrize("outcome, expected", [
    (None, "Done. Start the game as usual. Switch the text and voice languages in the game's Options."),
    (InstallError("Game folder: Not a folder."), "Game folder: Not a folder."),
])
def test_a_finished_run_shows_its_log_and_its_result_in_the_window(shown, monkeypatch, outcome, expected):
    # Bug: worker output never reaches the widgets (nothing posted to the Tk loop), or the failure text is lost.
    win, root = shown
    calls = {}

    def fake_install(game, inputs, force, log, progress):
        calls.update(game=game, force=force)
        log("built the thing")
        if outcome:
            raise outcome
        return {}

    monkeypatch.setattr(window, "install", fake_install)
    win.vars["game"].set("/g")
    win.run("install")
    _wait(root, lambda: not win.busy)
    assert expected in win.result.cget("text")
    assert "built the thing" in win.details.get("1.0", "end")
    assert calls["game"] == Path("/g") and calls["force"] is True


@pytest.mark.parametrize("answers, expected", [
    ([False], None),  # the player declines the confirmation
    ([True, True], True),
    ([True, False], False),
])
def test_uninstall_confirms_first_and_asks_whether_to_keep_the_settings(shown, monkeypatch, answers, expected):
    # Bug: Uninstall runs without asking, or deletes the settings whatever the player answered.
    win, root = shown
    seen, replies = {}, iter(answers)
    monkeypatch.setattr(win, "ask_yes", lambda *a, **k: next(replies))
    monkeypatch.setattr(window, "uninstall", lambda game, log, keep_settings: seen.update(keep=keep_settings) or {})
    win.vars["game"].set("/g")
    win.run("uninstall")
    if expected is None:
        assert not win.busy and not seen
        return
    _wait(root, lambda: not win.busy)
    assert seen == {"keep": expected} and "Done. wozmod is removed." in win.result.cget("text")


@pytest.mark.parametrize("record, label, asks", [
    (None, "Install", False),
    ({"version": "0.0.1"}, "Update from 0.0.1 to 0.1.0", False),
    ({}, "Update from an earlier version to 0.1.0", False),
    ({"version": "0.1.0"}, "Reinstall", True),
    ({"version": "9.0.0"}, "Install", True),
])
def test_the_main_button_names_what_will_happen_and_reinstall_or_going_back_asks(
        shown, monkeypatch, tmp_path, record, label, asks):
    # Bug: the button says "Install" over an older install (the player cannot tell it is an update), or a
    # reinstall or a step back to an older version starts without asking.
    import json
    win, root = shown
    (tmp_path / "wozmod").mkdir()
    if record is not None:
        (tmp_path / "wozmod/install.json").write_text(json.dumps({"format": 1, "files": {}, **record}))
    asked = []
    monkeypatch.setattr(win, "ask_yes", lambda *a, **k: asked.append(a) or False)
    installed = []
    monkeypatch.setattr(window, "install", lambda *a, **k: installed.append(a) or {})
    win.vars["game"].set(str(tmp_path))
    win.refresh_buttons()
    assert win.install_button.cget("text") == label
    win.run("install")
    assert bool(asked) is asks
    if not asks:
        _wait(root, lambda: not win.busy)
    assert bool(installed) is (not asks)  # an unanswered question (declined) installs nothing


def test_the_finished_view_keeps_the_stage_lines_and_shows_the_real_time(shown, monkeypatch):
    # Bug: the details end on a stale "Overall 100%, less than a minute left" and a developer log line, or the
    # percent is missing next to the time left while the install runs.
    win, root = shown
    gate = __import__("threading").Event()

    def fake_install(game, inputs, force, log, progress):
        progress.begin("build")
        for key in ("text", "voices", "art"):
            progress.begin(key)
            progress.apply((key, "working", 0.5, (1, 2)))
        gate.wait(10)  # the test looks at the window while the install is "running"
        for key in ("text", "voices", "art"):
            progress.finish(key)
        progress.finish("build")
        log("Placed 2 files in the game folder.")
        return {}

    monkeypatch.setattr(window, "install", fake_install)
    win.vars["game"].set("/g")
    win.run("install")
    _wait(root, lambda: "%" in win.left.cget("text"))
    label = win.left.cget("text")
    gate.set()
    _wait(root, lambda: not win.busy)
    details = win.details.get("1.0", "end")
    assert "left" in label
    assert "Installed in" in details and "Overall" not in details
    assert [line.split()[0] for line in details.splitlines()[:3]] == ["Text", "Voices", "Art"]
    assert details.splitlines()[-2] == "Placed 2 files in the game folder."


def test_a_typed_program_path_runs_only_when_finished(shown, monkeypatch):
    # Bug: the window runs whatever executable a half-typed path names, on every keystroke.
    win, root = shown
    ran = []
    monkeypatch.setattr(window, "check_row", lambda name, values: (ran.append(values[name]), (None, "OK"))[1])
    name = next(iter(installer.TOOLS))
    for path in ("/usr/bin/p", "/usr/bin/py"):
        win.vars[name].set(path)
    for _ in range(40):
        root.update()
        time.sleep(0.01)
    assert [path for path in ran if path] == []  # (an empty row is looked at: a disc image may cover it)
    win.check_program(name)
    _wait(root, lambda: [path for path in ran if path] == ["/usr/bin/py"])


@pytest.fixture
def program_pins(monkeypatch):
    """Pin synthetic Xbox 360 programs, and let every row except the Xbox 360 program and its files pass."""
    from test_install_programs import DECRYPTED, ENCRYPTED, _xbox_image, pin_synthetic_programs

    pin_synthetic_programs(monkeypatch)
    real = window.check_row
    monkeypatch.setattr(window, "check_row", lambda name, values: real(name, values) if name in (
        "x360-tool", "wo2-x360-file", "wo1-x360-file") else (None, "OK"))
    check = installer.check_input  # the disc image check is not under test; the decrypted file check is
    monkeypatch.setattr(installer, "check_input", lambda name, path: None if name in installer.DISCS else check(name, path))
    return DECRYPTED, ENCRYPTED, _xbox_image


def test_advanced_is_collapsed_by_default_and_opens_when_a_value_was_saved(shown):
    # Bug: the section shows at first start (clutter), or stays shut over a saved file the install would use.
    win, root = shown
    assert not win.advanced_open and not win.advanced_widgets[0].winfo_manager()
    win.toggle_advanced()
    assert win.advanced_open and win.advanced_widgets[0].winfo_manager()
    remembered.save_settings({"wo2-psp-file": "/some/EBOOT.BIN"})
    again = tkinter.Tk(screenName=root.winfo_screen())
    again.withdraw()
    try:
        saved = window.SetupWindow(again)
        assert saved.advanced_open and saved.advanced_widgets[0].winfo_manager()
        again.after_cancel(saved.pump_id)
    finally:
        again.destroy()


def test_a_good_advanced_file_makes_its_program_row_not_required_and_a_bad_one_does_not(shown, program_pins, tmp_path):
    # Bug: Install stays disabled although a good decrypted file is given, a file row is itself required
    # (the other platform uses a program), or a wrong file is accepted.
    plain, encrypted, xbox_image = program_pins
    win, root = shown
    good, wrong = tmp_path / "plain.pe", tmp_path / "wrong"
    good.write_bytes(plain)
    wrong.write_bytes(b"x")
    for name in ("game", "wo2-psp", "psp-tool"):
        win.vars[name].set(str(tmp_path))
    for name in ("wo2-x360", "wo1-x360"):
        win.vars[name].set(str(xbox_image(tmp_path, b"default.xex", encrypted)))
    win.vars["wo1-x360-file"].set(str(good))  # the program row stays required until both discs are covered
    win.vars["wo2-x360-file"].set(str(wrong))
    _wait(root, lambda: win.status["wo2-x360-file"].cget("text") == installer.WRONG_FILE)
    win.check_program("psp-tool")
    _wait(root, lambda: win.good["psp-tool"] and win.good["game"])
    assert not win.good["x360-tool"] and "disabled" in win.install_button.state()
    win.vars["wo2-x360-file"].set(str(good))
    _wait(root, lambda: win.status["x360-tool"].cget("text") == "Not needed: using the decrypted file under Advanced")
    _wait(root, lambda: "disabled" not in win.install_button.state())  # psp-file is empty and not needed


def test_a_red_decrypted_file_row_keeps_install_disabled_and_counts_as_missing(shown, program_pins, tmp_path):
    # Bug: the decrypted-file rows never count, so a red one beside a ticked program row enables Install, and the
    # install then refuses the file; or the file row keeps the wording it had before the program was given.
    from test_donors_programs import fake

    plain, encrypted, xbox_image = program_pins
    win, root = shown
    wrong = tmp_path / "wrong"
    wrong.write_bytes(b"x")
    for name in ("game", "wo2-psp", "psp-tool"):
        win.vars[name].set(str(tmp_path))
    for name in ("wo2-x360", "wo1-x360"):
        win.vars[name].set(str(xbox_image(tmp_path, b"default.xex", encrypted)))
    win.vars["wo1-x360-file"].set(str(wrong))  # refused beside the program, which still runs on the other disc
    win.vars["x360-tool"].set(str(fake(tmp_path / "xextool", "xextool")))
    win.check_program("x360-tool")
    win.check_program("psp-tool")
    _wait(root, lambda: win.status["x360-tool"].cget("text") == "✔ OK. It made the files wozmod needs."
          and win.status["wo1-x360-file"].cget("text") == installer.FILE_AND_PROGRAM and win.good["psp-tool"])
    assert "disabled" in win.install_button.state()
    assert win.reason.cget("text") == "Pick the missing item above to enable Install."
    win.vars["wo1-x360-file"].set("")
    _wait(root, lambda: "disabled" not in win.install_button.state() and win.reason.cget("text") == "")


def test_a_decrypted_file_turns_red_when_its_disc_image_turns_out_to_hold_one(shown, program_pins, tmp_path):
    # Bug: changing the disc image rechecks only the image and the program rows, so the file row keeps its old tick
    # though the file is now a second source.
    plain, encrypted, xbox_image = program_pins
    win, root = shown
    (tmp_path / "encrypted").mkdir()
    (tmp_path / "held").mkdir()
    good = tmp_path / "plain.pe"
    good.write_bytes(plain)
    win.vars["wo2-x360"].set(str(xbox_image(tmp_path / "encrypted", b"default.xex", encrypted)))
    win.vars["wo2-x360-file"].set(str(good))
    _wait(root, lambda: win.good["wo2-x360-file"])
    win.vars["wo2-x360"].set(str(xbox_image(tmp_path / "held", b"default.xex", plain)))
    _wait(root, lambda: win.status["wo2-x360-file"].cget("text") == installer.FILE_IN_IMAGE)


def test_the_install_reason_counts_the_rows_still_missing(shown):
    # Bug: the reason miscounts (off by one, or counts the Advanced file rows the program rows stand in for),
    # or it stays stale after a row turns good.
    win, root = shown
    assert "Pick the 6 missing items above" in win.reason.cget("text")
    for name in ("game", "wo1-x360", "wo2-x360"):
        win.good[name] = True
    win.refresh_buttons()
    assert "Pick the 3 missing items above" in win.reason.cget("text")
    win.good["wo2-psp"] = win.good["x360-tool"] = True
    win.refresh_buttons()
    assert win.reason.cget("text") == "Pick the missing item above to enable Install."
    win.good["psp-tool"] = True
    win.refresh_buttons()
    assert win.reason.cget("text") == "" and "disabled" not in win.install_button.state()


def test_the_run_controls_are_hidden_until_a_run_starts(shown, monkeypatch):
    # Bug: an empty progress bar, Cancel and "Show details" sit on screen before anything has run.
    win, root = shown
    gate = __import__("threading").Event()
    monkeypatch.setattr(window, "install", lambda *a, **k: gate.wait(10))
    assert not win.run_frame.winfo_manager()
    win.vars["game"].set("/g")
    win.run("install")
    assert win.run_frame.winfo_manager() == "grid" and win.cancel_button.winfo_manager() == "grid"
    gate.set()
    _wait(root, lambda: not win.busy)
    assert win.run_frame.winfo_manager() == "grid" and not win.cancel_button.winfo_manager()  # result stays


def test_a_long_path_box_shows_the_end_of_the_path(shown):
    # Bug: a path longer than the box shows its start, so the player never sees the file name.
    win, root = shown
    root.deiconify()
    win.vars["wo2-psp"].set("/very/long/" + "folder/" * 30 + "Warriors Orochi 2.iso")
    _wait(root, lambda: win.entries["wo2-psp"].xview()[1] == 1.0)
    assert win.entries["wo2-psp"].xview()[0] > 0


def test_the_help_link_opens_the_list_of_tested_programs(shown, monkeypatch):
    # Bug: the link opens a wrong or broken address, or a click does nothing.
    win, root = shown
    opened = []
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    win.help_link.event_generate("<Button-1>")
    assert opened == ["https://github.com/Defiance4331/wozmod#tested-decryption-programs"]


def _edges_in_view(root, widget) -> set[str]:
    """Which of a widget's top and bottom edges show in the window; a long message may never show both at once."""
    if not widget.winfo_viewable():
        return set()
    top, bottom = root.winfo_rooty(), root.winfo_rooty() + root.winfo_height()
    edges = {"top": widget.winfo_rooty(), "bottom": widget.winfo_rooty() + widget.winfo_height()}
    return {edge for edge, y in edges.items() if top <= y <= bottom}


@pytest.mark.parametrize("how", ["wheel", "tab"])
def test_every_control_and_message_can_be_brought_into_view_in_a_short_window(shown, monkeypatch, how):
    # Bug: content taller than the window is cut off at the bottom (Install, the error, the version) with no way to reach
    # it, as on a 768-pixel screen or after the window was resized and then grew (Advanced open, a long error).
    win, root = shown
    root.deiconify()
    _wait(root, root.winfo_viewable)
    monkeypatch.setattr(window, "install", lambda *a, **k: 1 / 0)  # a long traceback in the result
    win.vars["game"].set("/g")
    win.set_advanced(True)
    win.run("install")
    _wait(root, lambda: not win.busy)
    win.toggle_details()
    root.geometry(f"{root.winfo_reqwidth()}x300")
    _wait(root, lambda: root.winfo_height() == 300)
    version = next(w for w in win.frame.winfo_children() if w.winfo_class() == "TLabel"
                   and w.cget("text").startswith("wozmod "))
    targets = {"title": win.frame.winfo_children()[0], "install": win.install_button, "uninstall": win.uninstall_button,
               "result": win.result, "details": win.details, "version": version}
    if how == "tab":  # the keyboard reaches each control by focus; a label is shown by focusing its neighbour
        targets = {"install": win.install_button, "uninstall": win.uninstall_button, "details": win.toggle}
    seen, focus = set(), win.frame
    for _ in range(120):
        seen |= {(name, edge) for name, widget in targets.items() for edge in _edges_in_view(root, widget)}
        if how == "wheel":
            win.frame.event_generate("<MouseWheel>", delta=-120, when="now")
        else:
            focus = focus.tk_focusNext()
            focus.focus_force()
        root.update()
    assert seen == {(name, edge) for name in targets for edge in ("top", "bottom")}


def test_the_scroll_bar_shows_only_while_the_content_does_not_fit(shown):
    # Bug: a scroll bar that never hides changes the window's look, or one that never shows leaves no way down.
    win, root = shown
    root.deiconify()
    _wait(root, lambda: root.winfo_height() == win.frame.winfo_reqheight())
    assert not win.scrollbar.winfo_manager()
    root.geometry(f"{root.winfo_width()}x300")
    _wait(root, lambda: win.scrollbar.winfo_manager() == "grid")
    root.geometry(f"{root.winfo_width()}x{win.frame.winfo_reqheight() + 50}")
    _wait(root, lambda: not win.scrollbar.winfo_manager())
