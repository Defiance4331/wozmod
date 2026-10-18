"""wozmod-setup: a window that runs the same install and uninstall as the `wozmod` command."""

from __future__ import annotations

import multiprocessing
import os
import queue
import sys
import threading
import webbrowser
import traceback
import tkinter
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from wozmod import resources
from wozmod import __version__
from wozmod.install import FILES, PROGRAMS, TOOLS, InstallError, check_input, install, install_state, resolve_program, uninstall
from wozmod.progress import DONE, Cancelled, Progress, duration, minutes_left
from wozmod.remembered import load_settings, save_settings

NOTICE = "Use your own copies of the games. wozmod includes no game files."
LANGUAGES = "Switch the text and voice languages in the game's Options."
TOOLS_HELP = "Programs you already have."
HELP_TEXT = "Which programs we tested"
HELP_URL = "https://github.com/Defiance4331/wozmod#tested-decryption-programs"
# name, label, kind of picker, what to pick
ROWS = (
    ("game", "Musou Orochi Z folder", "folder",
     "The folder that contains the game's .exe (the Japanese PC release)."),
    ("wo1-x360", "Warriors Orochi (Xbox 360, US)", "image", "The .iso file made from this disc."),
    ("x360", "Warriors Orochi 2 (Xbox 360, US)", "image", "The .iso file made from this disc."),
    ("psp", "Warriors Orochi 2 (PSP, US)", "image", "The .iso file made from this disc."),
    ("x360-tool", "Xbox 360 decryption program", "file",
     "A decryption program you already have for default.xex, the encrypted file on both Xbox 360 discs."),
    ("psp-tool", "PSP decryption program", "file", "A decryption program you already have for EBOOT.BIN, the PSP game's encrypted file."),
)
# Hidden until "Advanced" is opened. A good one replaces its program row.
ADVANCED = (
    ("x360-file", "Already decrypted Warriors Orochi 2 Xbox 360 program file", "file",
     "The decrypted program inside its default.xex (about 18 MB). Use it instead of the program above."),
    ("wo1-x360-file", "Already decrypted Warriors Orochi Xbox 360 program file", "file",
     "The decrypted program inside its default.xex (about 17 MB). Use it instead of the program above."),
    ("psp-file", "Already decrypted PSP EBOOT", "file",
     "The decrypted PSP_GAME/SYSDIR/EBOOT.BIN. Use it instead of the program above."),
)
SECTIONS = {"game": "1. Your game", "wo1-x360": "2. Your disc images", "x360-tool": "3. Your decryption programs"}
NOT_NEEDED = {"disc": "Not needed: your disc image already has the decrypted file",
              "file": "Not needed: using the decrypted file under Advanced"}
GOOD, BAD, QUIET, ACCENT = "#1a7f37", "#b42318", "#595959", "#0b5cad"


def check_row(name: str, values: dict[str, str]) -> tuple[str | None, str]:
    """(plain reason or None, tick text) for one row, from the installer's own checks."""
    try:
        if name in TOOLS:  # per disc: the disc image's copy, else the decrypted file, else run the program
            found = [resolve_program(disc, values) for disc in TOOLS[name]]
            for source, result in found:
                if isinstance(result, str):
                    if source != name or not values[name]:  # a bad decrypted file is shown on its own row
                        return None, ""
                    return result, ""
            if any(source == name for source, _ in found):
                return None, "OK. It made the files wozmod needs." if len(found) > 1 else "OK. It made the file wozmod needs."
            return None, NOT_NEEDED["disc" if all(source in PROGRAMS for source, _ in found) else "file"]
        return check_input(name, values[name]), "OK"
    except Exception as error:  # a check must never leave a row on "Checking..."
        return f"Could not check this ({error}).", ""


class SetupWindow:
    def __init__(self, root: tkinter.Tk):
        self.root = root
        root.title("wozmod setup")
        self.events: queue.Queue = queue.Queue()
        self.vars = {name: tkinter.StringVar() for name, *_ in (*ROWS, *ADVANCED)}
        self.status: dict[str, ttk.Label] = {}
        self.entries: dict[str, ttk.Entry] = {}
        self.good = dict.fromkeys(self.vars, False)
        self.ticket = dict.fromkeys(self.vars, 0)
        self.pending: dict[str, str] = {}
        self.busy = False
        self.rows: list = []
        self.log_lines: list[str] = []
        self.status_row: dict[str, int] = {}
        self.make_styles()

        frame = ttk.Frame(root, padding=(20, 16, 20, 12))
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        self.frame = frame
        ttk.Label(frame, text="Install English text, voices and menus into Musou Orochi Z.",
                  style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text=NOTICE, style="Quiet.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        saved = load_settings()
        self.advanced_widgets: list = []
        row = 2
        for name, label, kind, note in ROWS:
            if name in SECTIONS:
                ttk.Label(frame, text=SECTIONS[name], style="Heading.TLabel").grid(
                    row=row, column=0, columnspan=3, sticky="w", pady=(18, 4))
                row += 1
                if name == "x360-tool":
                    help_line = ttk.Frame(frame)
                    help_line.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 2))
                    ttk.Label(help_line, text=TOOLS_HELP + " ", style="Quiet.TLabel").pack(side="left")
                    self.help_link = ttk.Label(help_line, text=HELP_TEXT, style="Link.TLabel", cursor="hand2",
                                               takefocus=True)
                    self.help_link.pack(side="left")
                    for event in ("<Button-1>", "<Return>", "<space>"):
                        self.help_link.bind(event, lambda _: self.open_help())
                    row += 1
            self.add_row(row, name, label, kind, note, saved)
            row += 2
        self.advanced_button = ttk.Button(frame, command=self.toggle_advanced, style="Link.Toolbutton")
        self.advanced_button.grid(row=row, column=0, sticky="w", pady=(8, 0))
        row += 1
        for name, label, kind, note in ADVANCED:
            self.add_row(row, name, label, kind, note, saved)
            row += 2
        self.set_advanced(any(saved.get(name) for name, *_ in ADVANCED))  # a returning player sees what is used

        ttk.Label(frame, text="4. Install", style="Heading.TLabel").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(18, 6))
        buttons = ttk.Frame(frame)
        buttons.grid(row=row + 1, column=0, columnspan=3, sticky="ew")
        buttons.columnconfigure(1, weight=1)
        self.install_button = ttk.Button(buttons, text="Install", style="Accent.TButton",
                                         command=lambda: self.run("install"))
        self.install_button.grid(row=0, column=0)
        self.reason = ttk.Label(buttons, style="Quiet.TLabel", wraplength=420, justify="left")
        self.reason.grid(row=0, column=1, sticky="w", padx=12)
        self.uninstall_button = ttk.Button(buttons, text="Uninstall", style="Link.Toolbutton",
                                           command=lambda: self.run("uninstall"))
        self.uninstall_button.grid(row=0, column=2, sticky="e")

        # Nothing here shows until a run starts.
        self.run_frame = ttk.Frame(frame)
        self.run_frame.grid(row=row + 2, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        self.run_frame.columnconfigure(0, weight=1)
        self.bar = ttk.Progressbar(self.run_frame, mode="determinate", maximum=100)
        self.bar.grid(row=0, column=0, sticky="ew")
        self.left = ttk.Label(self.run_frame, text="")
        self.left.grid(row=0, column=1, sticky="w", padx=10)
        self.cancel_button = ttk.Button(self.run_frame, text="Cancel", command=self.cancel_run)
        self.cancel_button.grid(row=0, column=2)
        self.now = ttk.Label(self.run_frame, text="", justify="left")
        self.now.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.result = ttk.Label(self.run_frame, text="", justify="left", style="Result.TLabel")
        self.result.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.toggle = ttk.Button(self.run_frame, text="▸ Show details", command=self.toggle_details,
                                 style="Link.Toolbutton")
        self.toggle.grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.details = tkinter.Text(self.run_frame, height=9, width=1, state="disabled", wrap="word",
                                    font="TkFixedFont", relief="flat", borderwidth=1, highlightthickness=1,
                                    highlightbackground="#c8c8c8")
        self.details.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        self.details.grid_remove()
        self.run_frame.rowconfigure(4, weight=1)
        self.run_frame.grid_remove()
        frame.rowconfigure(row + 2, weight=1)
        ttk.Label(frame, text=f"wozmod {__version__}", style="Quiet.TLabel").grid(
            row=row + 3, column=0, columnspan=3, sticky="se", pady=(10, 0))
        self.run_frame.bind("<Configure>", lambda _: self.wrap_to(self.run_frame, self.now, self.result))
        self.cancel = threading.Event()
        self.refresh_buttons()
        for name in self.vars:
            self.changed(name, run=True)
        root.update_idletasks()
        root.minsize(frame.winfo_reqwidth(), frame.winfo_reqheight())  # sized to its content; still resizable
        self.pump_id = root.after(100, self.pump)

    def make_styles(self) -> None:
        """Named fonts and styles derived from the system font, so Windows keeps Segoe UI and Linux its own."""
        base = tkfont.nametofont("TkDefaultFont")
        size = base.cget("size")
        grow = lambda factor: (1 if size > 0 else -1) * round(abs(size) * factor)  # noqa: E731  (negative means pixels)
        self.fonts = {
            "title": base.copy(), "heading": base.copy(), "label": base.copy(), "result": base.copy(),
            "link": base.copy()}
        self.fonts["title"].configure(weight="bold", size=grow(1.3))
        self.fonts["heading"].configure(weight="bold", size=grow(1.2))
        self.fonts["label"].configure(weight="bold")
        self.fonts["result"].configure(weight="bold", size=grow(1.1))
        self.fonts["link"].configure(underline=True)
        style = ttk.Style(self.root)
        style.configure("Title.TLabel", font=self.fonts["title"])
        style.configure("Heading.TLabel", font=self.fonts["heading"], foreground=ACCENT)
        style.configure("Label.TLabel", font=self.fonts["label"])
        style.configure("Quiet.TLabel", foreground=QUIET)
        style.configure("Result.TLabel", font=self.fonts["result"])
        style.configure("Accent.TButton", font=self.fonts["label"], padding=(18, 6))
        style.configure("Link.TLabel", font=self.fonts["link"], foreground=ACCENT)
        style.configure("Link.Toolbutton", font=self.fonts["link"], foreground=ACCENT)

    def add_row(self, row: int, name: str, label: str, kind: str, note: str, saved: dict) -> None:
        """Two grid rows: the name and what to pick on the left; the path box, then its verdict, on the right."""
        frame = self.frame
        box = ttk.Frame(frame)
        box.grid(row=row, column=0, rowspan=2, sticky="nw", padx=(0, 16), pady=(6, 0))
        ttk.Label(box, text=label, style="Label.TLabel").pack(anchor="w")
        ttk.Label(box, text=note, style="Quiet.TLabel", wraplength=300, justify="left").pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=self.vars[name], width=40)
        entry.grid(row=row, column=1, sticky="ew", pady=(6, 0))
        self.entries[name] = entry
        for event in ("<Configure>", "<FocusOut>"):  # show the end of a long path, the file name
            entry.bind(event, lambda _, e=entry: self.show_end(e), add=True)
        if name in TOOLS:  # a typed path runs only when finished, never on a half-typed name
            for event in ("<Return>", "<FocusOut>"):
                entry.bind(event, lambda _, n=name: self.check_program(n), add=True)
        browse = ttk.Button(frame, text="Browse...", command=lambda: self.browse(name, kind))
        browse.grid(row=row, column=2, padx=(8, 0), pady=(6, 0))
        status = ttk.Label(frame, wraplength=400, justify="left")
        status.grid(row=row + 1, column=1, columnspan=2, sticky="nw", pady=(2, 0))
        self.status[name] = status
        entry.bind("<Configure>", lambda _, s=status, e=entry: s.configure(wraplength=max(e.winfo_width(), 200)),
                   add=True)
        # room for two lines, so a long reason wraps in place and does not push the rows below
        self.status_row[name] = row + 1
        self.reserve(name, name not in FILES.values())
        if name in FILES.values():
            self.advanced_widgets += [box, entry, browse, status]
        self.vars[name].set(saved.get(name, ""))
        self.vars[name].trace_add("write", lambda *_: self.changed(name))

    def reserve(self, name: str, on: bool) -> None:
        """Keep two lines of height under a path box. A hidden row must give it back, or it leaves a gap."""
        lines = 2 * tkfont.nametofont("TkDefaultFont").metrics("linespace") + 2 if on else 0
        self.frame.rowconfigure(self.status_row[name], minsize=lines)

    def open_help(self) -> None:
        webbrowser.open(HELP_URL)

    def wrap_to(self, frame, *labels) -> None:
        for label in labels:
            label.configure(wraplength=max(frame.winfo_width() - 20, 200))

    def show_end(self, entry: ttk.Entry) -> None:
        """Scroll a path box to its end so the file name shows, unless the player is typing in it."""
        if self.root.focus_get() is not entry:
            entry.xview_moveto(1)

    # --- the input rows

    def set_advanced(self, show: bool) -> None:
        for widget in self.advanced_widgets:
            (widget.grid if show else widget.grid_remove)()
        for name in FILES.values():
            self.reserve(name, show)
        self.advanced_open = show
        self.advanced_button.configure(text="▾ Advanced: use decrypted files instead" if show
                                       else "▸ Advanced: use decrypted files instead")

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
            self.show_end(self.entries[name])
            if name in TOOLS:
                self.check_program(name)

    def inputs(self) -> dict[str, str]:
        return {name: var.get().strip() for name, var in self.vars.items()}

    def changed(self, name: str, run: bool = False) -> None:
        self.root.after_idle(self.show_end, self.entries[name])  # after a load or a pick, show the file name
        save_settings(self.inputs())
        if name in TOOLS and not run:  # typing a program path: wait for Enter, focus-out or Browse
            self.ticket[name] += 1
            self.good[name] = False
            self.status[name].configure(text="Press Enter to check" if self.vars[name].get().strip() else "",
                                        foreground=QUIET)
            self.refresh_buttons()
            return
        for other in dict.fromkeys([name, *(tool for disc, (_, tool, file) in PROGRAMS.items() if name in (disc, file))]):
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
        if not values[name] and name not in TOOLS:  # an empty program row may still be covered by the image or a file
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
        unmet = [name for name, good in self.good.items() if not good and name not in FILES.values()]  # the program rows carry the file rows
        ready = not unmet
        state, found = self.install_state()
        self.install_button.configure(text={
            "update": f"Update from {found or 'an earlier version'} to {__version__}",
            "reinstall": "Reinstall"}.get(state, "Install"))
        self.install_button.state(["!disabled"] if ready and not self.busy else ["disabled"])
        self.reason.configure(text="" if ready or self.busy else self.install_reason(len(unmet)))
        self.uninstall_button.state(["!disabled"] if self.vars["game"].get().strip() and not self.busy
                                    else ["disabled"])
        showing = self.busy and self.action == "install"
        (self.cancel_button.grid if showing else self.cancel_button.grid_remove)()
        self.cancel_button.state(["!disabled"] if showing and not self.cancel.is_set() else ["disabled"])

    @staticmethod
    def install_reason(count: int) -> str:
        """Why Install is greyed out: how many rows above are empty or marked with a problem."""
        return ("Pick the missing item above to enable Install." if count == 1
                else f"Pick the {count} missing items above to enable Install.")

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
                                "frame rate, camera)?", default="yes")
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
        self.run_frame.grid()
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
                          for name in (*PROGRAMS, *TOOLS, *FILES.values())}
                inputs["lettering"] = resources.root() / "lettering.zip"
                inputs["dll"] = resources.root() / "d3d9.dll"
                install(Path(values["game"]), inputs, force=True, log=log, progress=progress)
                message = f"Done. Start the game as usual. {LANGUAGES}"
                if cancel.is_set():
                    message = f"The files were already being placed, so the install finished. Start the game as usual. {LANGUAGES}"
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
