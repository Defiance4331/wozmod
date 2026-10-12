"""wozmod-setup: a window that runs the same install and uninstall as the `wozmod` command."""

from __future__ import annotations

import multiprocessing
import os
import queue
import sys
import threading
import traceback
import tkinter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from wozmod import resources
from wozmod import __version__
from wozmod.install import FILES, PROGRAMS, InstallError, check_input, install, install_state, resolve_program, uninstall
from wozmod.progress import DONE, Cancelled, Progress, duration, minutes_left
from wozmod.remembered import load_settings, save_settings

NOTICE = "Use files from your own legal copies of the games. wozmod includes no game files."
TOOL_NOTE = "A decryption program you already have. wozmod runs it on the file from your disc image."
# name, label, kind of picker, small note under the label
ROWS = (
    ("game", "Musou Orochi Z folder", "folder", "The Japanese PC release."),
    ("wo1", "Warriors Orochi (PC) disc image", "image", "The European release."),
    ("x360", "Warriors Orochi 2 (Xbox 360) disc image", "image", "The US release."),
    ("psp", "Warriors Orochi 2 (PSP) disc image", "image", "The US release."),
    ("x360-tool", "Xbox 360 decryption program", "file", TOOL_NOTE),
    ("psp-tool", "PSP decryption program", "file", TOOL_NOTE),
)
# Hidden until "Advanced" is opened. A good one replaces its program row.
ADVANCED = (
    ("x360-file", "Already decrypted Xbox 360 program file", "file",
     "The plain program inside default.xex, decrypted and decompressed (about 18 MB). Use it in place of the program above."),
    ("psp-file", "Already decrypted PSP EBOOT", "file",
     "PSP_GAME/SYSDIR/EBOOT.BIN, decrypted. Use it in place of the program above."),
)
NOT_NEEDED = {"disc": "Not needed: your disc image already has the decrypted file",
              "file": "Not needed: using the decrypted file under Advanced"}
GOOD, BAD, QUIET = "#1a7f37", "#b42318", "#555555"


def check_row(name: str, values: dict[str, str]) -> tuple[str | None, str]:
    """(plain reason or None, tick text) for one row, from the installer's own checks."""
    try:
        if name in PROGRAMS:  # the disc image's copy, else the decrypted file, else run the program
            source, found = resolve_program(name, values)
            if isinstance(found, bytes):
                return None, NOT_NEEDED["disc" if source == PROGRAMS[name][0] else "file"] if source != name else (
                    "OK. It made the file wozmod needs.")
            if source != name or not values[name]:  # a bad decrypted file is shown on its own row
                return None, ""
            return found, ""
        return check_input(name, values[name]), "OK"
    except Exception as error:  # a check must never leave a row on "Checking..."
        return f"Could not check this ({error}).", ""


class SetupWindow:
    def __init__(self, root: tkinter.Tk):
        self.root = root
        root.title("wozmod setup")
        root.minsize(900, 600)
        root.geometry("1020x680")  # the path boxes take the extra width
        self.events: queue.Queue = queue.Queue()
        self.vars = {name: tkinter.StringVar() for name, *_ in (*ROWS, *ADVANCED)}
        self.status: dict[str, ttk.Label] = {}
        self.good = dict.fromkeys(self.vars, False)
        self.ticket = dict.fromkeys(self.vars, 0)
        self.pending: dict[str, str] = {}
        self.busy = False
        self.rows: list = []
        self.log_lines: list[str] = []

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Install English text, voices and menus into Musou Orochi Z.",
                  font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(frame, text=NOTICE).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 8))
        saved = load_settings()
        self.advanced_widgets: list = []
        self.advanced_button = ttk.Button(frame, command=self.toggle_advanced)
        self.advanced_button.grid(row=len(ROWS) + 2, column=0, sticky="w", pady=(6, 0))
        rows = [*enumerate(ROWS, start=2), *enumerate(ADVANCED, start=len(ROWS) + 3)]
        for row, (name, label, kind, note) in rows:
            box = ttk.Frame(frame)
            box.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            ttk.Label(box, text=label).pack(anchor="w")
            if note:
                ttk.Label(box, text=note, foreground=QUIET, font=("TkDefaultFont", 8), wraplength=330,
                          justify="left").pack(anchor="w")
            entry = ttk.Entry(frame, textvariable=self.vars[name], width=20)
            entry.grid(row=row, column=1, sticky="ew")
            if name in PROGRAMS:  # a typed path runs only when finished, never on a half-typed name
                for event in ("<Return>", "<FocusOut>"):
                    entry.bind(event, lambda _, n=name: self.check_program(n))
            browse = ttk.Button(frame, text="Browse...", command=lambda n=name, k=kind: self.browse(n, k))
            browse.grid(row=row, column=2, padx=6)
            self.status[name] = ttk.Label(frame, wraplength=320, justify="left")
            self.status[name].grid(row=row, column=3, sticky="w")
            if name in FILES.values():
                self.advanced_widgets += [box, entry, browse, self.status[name]]
            self.vars[name].set(saved.get(name, ""))
            self.vars[name].trace_add("write", lambda *_, n=name: self.changed(n))
        self.set_advanced(any(saved.get(name) for name, *_ in ADVANCED))  # a returning player sees what is used
        first = len(ROWS) + len(ADVANCED) + 3
        buttons = ttk.Frame(frame)
        buttons.grid(row=first, column=0, columnspan=4, sticky="w", pady=(10, 4))
        self.install_button = ttk.Button(buttons, text="Install", command=lambda: self.run("install"))
        self.uninstall_button = ttk.Button(buttons, text="Uninstall", command=lambda: self.run("uninstall"))
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self.cancel_run)
        self.install_button.pack(side="left")
        self.uninstall_button.pack(side="left", padx=8)
        self.cancel_button.pack(side="left")
        self.bar = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.bar.grid(row=first + 1, column=0, columnspan=3, sticky="ew", pady=4)
        self.left = ttk.Label(frame, text="")
        self.left.grid(row=first + 1, column=3, sticky="w", padx=6)
        self.now = ttk.Label(frame, text="", justify="left", wraplength=700)
        self.now.grid(row=first + 2, column=0, columnspan=4, sticky="w")
        self.toggle = ttk.Button(frame, text="\u25b8 Show details", command=self.toggle_details)
        self.toggle.grid(row=first + 3, column=0, sticky="w", pady=(4, 0))
        self.details = tkinter.Text(frame, height=9, width=1, state="disabled", wrap="word")
        self.details.grid(row=first + 4, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
        self.details.grid_remove()
        self.result = ttk.Label(frame, text="", wraplength=700, justify="left", font=("TkDefaultFont", 10, "bold"))
        self.result.grid(row=first + 5, column=0, columnspan=4, sticky="w", pady=(6, 0))
        frame.rowconfigure(first + 4, weight=1)
        self.cancel = threading.Event()
        self.refresh_buttons()
        for name in self.vars:
            self.changed(name, run=True)
        self.pump_id = root.after(100, self.pump)

    # --- the input rows

    def set_advanced(self, show: bool) -> None:
        for widget in self.advanced_widgets:
            (widget.grid if show else widget.grid_remove)()
        self.advanced_open = show
        self.advanced_button.configure(text="\u25be Advanced" if show else "\u25b8 Advanced")

    def toggle_advanced(self) -> None:
        self.set_advanced(not self.advanced_open)

    def browse(self, name: str, kind: str) -> None:
        start = self.vars[name].get()
        initial = str(Path(start).parent if start else Path.home())
        if kind == "folder":
            chosen = filedialog.askdirectory(initialdir=start or initial)
        else:
            types = [("Disc images", "*.iso"), ("All files", "*")] if kind == "image" else [("All files", "*")]
            chosen = filedialog.askopenfilename(initialdir=initial, filetypes=types)
        if chosen:
            self.vars[name].set(chosen)
            if name in PROGRAMS:
                self.check_program(name)

    def inputs(self) -> dict[str, str]:
        return {name: var.get().strip() for name, var in self.vars.items()}

    def changed(self, name: str, run: bool = False) -> None:
        save_settings(self.inputs())
        if name in PROGRAMS and not run:  # typing a program path: wait for Enter, focus-out or Browse
            self.ticket[name] += 1
            self.good[name] = False
            self.status[name].configure(text="Press Enter to check" if self.vars[name].get().strip() else "",
                                        foreground=QUIET)
            self.refresh_buttons()
            return
        for other in [name, *(tool for tool, (image, _) in PROGRAMS.items() if name in (image, FILES[tool]))]:
            self.check_later(other)  # a disc image changes what its program makes

    def check_program(self, name: str) -> None:
        if not self.good[name]:
            self.check_later(name)

    def check_later(self, name: str) -> None:
        """Check one row a moment after the last key, in a worker thread."""
        self.ticket[name] += 1
        ticket, values = self.ticket[name], self.inputs()
        self.good[name] = False
        self.refresh_buttons()
        if not values[name] and name not in PROGRAMS:  # an empty program row may still be covered by the image or a file
            self.show(name, ticket, None, "")
            return
        self.status[name].configure(text="Checking...", foreground=QUIET)
        self.root.after(300, lambda: threading.Thread(
            target=self.check, args=(name, ticket, values), daemon=True).start()
            if ticket == self.ticket[name] else None)

    def check(self, name: str, ticket: int, values: dict[str, str]) -> None:
        self.events.put(("checked", name, ticket, *check_row(name, values)))

    def show(self, name: str, ticket: int, problem: str | None, note: str) -> None:
        if ticket != self.ticket[name]:
            return
        self.good[name] = problem is None and bool(note)
        if problem:
            self.status[name].configure(text=problem, foreground=BAD)
        else:
            self.status[name].configure(text=note if note.startswith("Not needed") else ("✔ " + note) if note else "",
                                        foreground=GOOD)
        self.refresh_buttons()

    def install_state(self) -> tuple[str, str | None]:
        game = Path(self.vars["game"].get().strip() or ".")
        try:
            return install_state(game) if game.is_dir() else ("none", None)
        except InstallError:
            return "none", None

    def refresh_buttons(self) -> None:
        ready = all(good for name, good in self.good.items() if name not in FILES.values())  # the program rows carry the file rows
        state, found = self.install_state()
        self.install_button.configure(text={
            "update": f"Update from {found or 'an earlier version'} to {__version__}",
            "reinstall": "Reinstall"}.get(state, "Install"))
        self.install_button.state(["!disabled"] if ready and not self.busy else ["disabled"])
        self.uninstall_button.state(["!disabled"] if self.vars["game"].get().strip() and not self.busy
                                    else ["disabled"])
        self.cancel_button.state(["!disabled"] if self.busy and self.action == "install"
                                 and not self.cancel.is_set() else ["disabled"])

    # --- install and uninstall

    action = ""

    def toggle_details(self) -> None:
        shown = self.details.winfo_ismapped()
        (self.details.grid_remove if shown else self.details.grid)()
        self.toggle.configure(text="\u25b8 Show details" if shown else "\u25be Hide details")

    def show_details(self, text: str, replace: bool) -> None:
        self.details.configure(state="normal")
        if replace:
            self.details.delete("1.0", "end")
        self.details.insert("end", text)
        self.details.see("end")
        self.details.configure(state="disabled")

    def cancel_run(self) -> None:
        self.cancel.set()
        self.result.configure(text="Cancelling. Your game folder will be left as it was.", foreground=QUIET)
        self.refresh_buttons()

    def ask_yes(self, title: str, text: str, default: str = "no") -> bool:
        return messagebox.askyesno(title, text, default=default)

    def run(self, action: str) -> None:
        values = self.inputs()
        keep = True
        if action == "uninstall":
            if not self.ask_yes("Uninstall wozmod", f"Remove wozmod from this folder?\n\n{values['game']}"):
                return
            keep = self.ask_yes("Your settings", "Keep your wozmod settings (language, field of view, "
                                "frame rate)?", default="yes")
        else:
            state, found = self.install_state()
            if state == "reinstall" and not self.ask_yes(
                    "Reinstall", f"wozmod {__version__} is already installed here.\n\nBuild and install it again? "
                    "Your settings stay."):
                return
            if state == "downgrade" and not self.ask_yes(
                    "Older version", f"wozmod {found or 'of an unknown version'} is installed here. This is wozmod "
                    f"{__version__}, which is older.\n\nGo back to this version?"):
                return
        self.busy, self.action = True, action
        self.cancel = threading.Event()
        self.refresh_buttons()
        self.result.configure(text="", foreground=QUIET)
        self.now.configure(text="")
        self.left.configure(text="")
        self.show_details("", True)
        self.rows, self.log_lines = [], []
        self.bar.configure(value=0)
        threading.Thread(target=self.work, args=(action, values, self.cancel, keep), daemon=True).start()

    def post_progress(self, progress: Progress) -> None:
        """The install calls this from its own thread; the Tk loop applies it."""
        self.events.put(("progress", progress.percent(), minutes_left(progress.seconds_left()),
                         progress.lines(), progress.rows()))

    def work(self, action: str, values: dict[str, str], cancel: threading.Event, keep: bool = True) -> None:
        log = lambda text: self.events.put(("log", text))  # noqa: E731
        progress = Progress(self.post_progress, cancel)
        try:
            if action == "install":
                inputs = {name: Path(values[name]) if values[name] else None
                          for name in ("wo1", "x360", "psp", *PROGRAMS, *FILES.values())}
                inputs["lettering"] = resources.root() / "lettering.zip"
                inputs["dll"] = resources.root() / "d3d9.dll"
                install(Path(values["game"]), inputs, force=True, log=log, progress=progress)
                message = "Done. Start the game as usual."
                if cancel.is_set():
                    message = "The files were already being placed, so the install finished. Start the game as usual."
            else:
                changed = uninstall(Path(values["game"]), log=log, keep_settings=keep)
                message = "Done. wozmod is removed." if not changed else (
                    "Done. wozmod is removed, except files you changed. They stay in place.")
            self.events.put(("finished", message, True, duration(progress.elapsed()) if action == "install" else ""))
        except Cancelled:
            self.events.put(("finished", "Cancelled. Your game folder was left as it was.", False, ""))
        except InstallError as error:
            self.events.put(("finished", f"Stopped. Nothing was changed.\n{error}", False, ""))
        except Exception:
            self.events.put(("finished", "Stopped, with an unexpected error.\n" + traceback.format_exc(), False, ""))

    def detail_text(self, rows, overall: str) -> str:
        lines = [f"{name:<8}{fraction:>5.0%}  {text}" for name, fraction, text in rows]
        return "\n".join([*lines, overall, *self.log_lines]) + "\n"

    def pump(self) -> None:
        """Apply what the worker threads posted; the Tk loop is the only place widgets change."""
        try:
            latest = None
            while True:
                event = self.events.get_nowait()
                if event[0] == "checked":
                    self.show(*event[1:])
                elif event[0] == "progress":
                    latest = event
                elif event[0] == "log":
                    self.log_lines.append(event[1])
                    if not self.rows:
                        self.show_details(event[1] + "\n", False)
                else:
                    _, message, ok, took = event
                    latest = None
                    self.busy = False
                    self.bar.configure(value=100 if ok else 0)
                    self.left.configure(text="")
                    self.now.configure(text="")
                    self.result.configure(text=message, foreground=GOOD if ok else BAD)
                    if ok and took and self.rows:  # the final view: every stage done, and the real time
                        self.show_details(self.detail_text([(n, 1.0, DONE) for n, _, _ in self.rows],
                                                           f"Installed in {took}"), True)
                    self.refresh_buttons()
        except queue.Empty:
            pass
        if latest and self.busy:
            _, percent, left, lines, self.rows = latest
            self.bar.configure(value=100 * percent)
            self.left.configure(text=f"{percent:.0%}  {left}")
            self.now.configure(text="\n".join(f"Now: {text}" for _, _, text in lines if text != DONE))
            self.show_details(self.detail_text(self.rows, f"{'Overall':<8}{percent:>5.0%}  {left}"), True)
        self.pump_id = self.root.after(100, self.pump)


def main() -> int:
    for name in ("stdout", "stderr"):  # a windowed Windows program has none; libraries still write to them
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    multiprocessing.freeze_support()  # the install starts its build workers through here
    root = tkinter.Tk()
    SetupWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
