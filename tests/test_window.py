"""The setup window: it shows the installer's own verdicts, remembers paths, and reports how a run ended."""

from __future__ import annotations

import time
import tkinter
from pathlib import Path

import pytest

import wozmod.install as installer
from wozmod import remembered, window
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
    values = {"game": str(tmp_path), "wo1-x360": str(wrong), "x360": str(tmp_path / "none"), "psp": str(wrong),
              "lettering": str(wrong), "dll": str(wrong)}
    with pytest.raises(InstallError) as caught:
        installer.check_inputs(tmp_path, {name: Path(path) for name, path in values.items()})
    lines = str(caught.value).splitlines()

    for name in values:
        problem, _ = window.check_row(name, values)
        assert problem, name
        assert f"{installer.LABELS[name]}: {problem}" in lines, name


def test_settings_survive_a_restart_and_a_broken_file_is_ignored():
    # Bug: the chosen paths are lost, or a half-written settings file stops the window from opening.
    remembered.save_settings({"game": "/some/folder"})
    assert remembered.load_settings() == {"game": "/some/folder"}
    remembered.settings_path().write_text("{not json")
    assert remembered.load_settings() == {}


@pytest.fixture
def shown(monkeypatch):
    try:
        root = tkinter.Tk()
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


def test_a_fresh_window_cannot_install_and_says_where_the_files_come_from(shown):
    # Bug: Install is clickable with nothing picked, or the own-copies notice is missing.
    win, root = shown
    assert "disabled" in win.install_button.state() and "disabled" in win.uninstall_button.state()
    assert [w.cget("text") for w in root.winfo_children()[0].winfo_children() if w.winfo_class() == "TLabel"
            ].count(window.NOTICE) == 1



def test_the_window_shows_the_released_version(shown):
    # Bug: the version label is missing, or shows a version other than the release's.
    import tomllib
    win, root = shown
    version = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["version"]
    labels = [w.cget("text") for w in root.winfo_children()[0].winfo_children() if w.winfo_class() == "TLabel"]
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
    import hashlib

    import wozmod.donors as donors
    from test_install_programs import DECRYPTED, ENCRYPTED, _xbox_image
    from wozmod.donors import ProgramIdentity

    installer._TOOL_RUNS.clear()
    for attribute, content in (("X360_PROGRAM", DECRYPTED), ("X360_ENCRYPTED_PROGRAM", ENCRYPTED),
                               ("WO1_X360_PROGRAM", DECRYPTED), ("WO1_X360_ENCRYPTED_PROGRAM", ENCRYPTED)):
        monkeypatch.setattr(donors, attribute, ProgramIdentity(
            attribute, len(content), hashlib.sha256(content).hexdigest(), "x360"))
    real = window.check_row
    monkeypatch.setattr(window, "check_row", lambda name, values: real(name, values) if name in (
        "x360-tool", "x360-file", "wo1-x360-file") else (None, "OK"))
    monkeypatch.setattr(installer, "check_input", lambda name, path: None)  # the disc image check is not under test
    return DECRYPTED, ENCRYPTED, _xbox_image


def test_advanced_is_collapsed_by_default_and_opens_when_a_value_was_saved(shown):
    # Bug: the section shows at first start (clutter), or stays shut over a saved file the install would use.
    win, root = shown
    assert not win.advanced_open and not win.advanced_widgets[0].winfo_manager()
    win.toggle_advanced()
    assert win.advanced_open and win.advanced_widgets[0].winfo_manager()
    remembered.save_settings({"psp-file": "/some/EBOOT.BIN"})
    again = tkinter.Tk()
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
    for name in ("game", "psp", "psp-tool"):
        win.vars[name].set(str(tmp_path))
    for name in ("x360", "wo1-x360"):
        win.vars[name].set(str(xbox_image(tmp_path, b"default.xex", encrypted)))
    win.vars["wo1-x360-file"].set(str(good))  # the program row stays required until both discs are covered
    win.vars["x360-file"].set(str(wrong))
    _wait(root, lambda: win.status["x360-file"].cget("text") == installer.WRONG_FILE)
    win.check_program("psp-tool")
    _wait(root, lambda: win.good["psp-tool"] and win.good["game"])
    assert not win.good["x360-tool"] and "disabled" in win.install_button.state()
    win.vars["x360-file"].set(str(good))
    _wait(root, lambda: win.status["x360-tool"].cget("text") == "Not needed: using the decrypted file under Advanced")
    _wait(root, lambda: "disabled" not in win.install_button.state())  # psp-file is empty and not needed


def test_a_decrypted_copy_in_the_disc_image_says_so_in_the_program_row(shown, program_pins, tmp_path):
    # Bug: the image's copy is found at install but the window still demands a program.
    plain, _, xbox_image = program_pins
    win, root = shown
    for name in ("x360", "wo1-x360"):
        win.vars[name].set(str(xbox_image(tmp_path, b"default.xex", plain)))
    _wait(root, lambda: win.status["x360-tool"].cget("text") == "Not needed: your disc image already has the decrypted file")
    assert win.good["x360-tool"]


def test_the_install_reason_counts_the_rows_still_missing(shown):
    # Bug: the reason miscounts (off by one, or counts the Advanced file rows the program rows stand in for),
    # or it stays stale after a row turns good.
    win, root = shown
    assert "Pick the 6 missing items above" in win.reason.cget("text")
    for name in ("game", "wo1-x360", "x360"):
        win.good[name] = True
    win.refresh_buttons()
    assert "Pick the 3 missing items above" in win.reason.cget("text")
    win.good["psp"] = win.good["x360-tool"] = True
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
    win.vars["psp"].set("/very/long/" + "folder/" * 30 + "Warriors Orochi 2.iso")
    _wait(root, lambda: win.entries["psp"].xview()[1] == 1.0)
    assert win.entries["psp"].xview()[0] > 0


def test_the_help_link_opens_the_list_of_tested_programs(shown, monkeypatch):
    # Bug: the link opens a wrong or broken address, or a click does nothing.
    win, root = shown
    opened = []
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    win.help_link.event_generate("<Button-1>")
    assert opened == ["https://github.com/Defiance4331/wozmod#tested-decryption-programs"]
