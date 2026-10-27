"""Every program the installer starts goes through wozmod.process.run, which gives it no console on Windows."""

import ast
import os
import subprocess
from pathlib import Path

from wozmod import process

SOURCE = Path(__file__).resolve().parents[1] / "src/wozmod"
# Developer-only starts (the DLL build) may open what they like.
DEVELOPER_ONLY = {"runtime/english_runtime.py", "runtime/true_rate.py", "process.py"}
STARTS = {"subprocess": {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
          "os": {"system", "popen", "startfile", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
                 "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp",
                 "execvpe", "posix_spawn", "posix_spawnp"},
          "asyncio": {"create_subprocess_exec", "create_subprocess_shell"}}


def test_installer_code_starts_programs_only_through_the_helper():
    # Bug: a new call site uses subprocess.run directly, and the setup window opens a console per run on Windows.
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        name = path.relative_to(SOURCE).as_posix()
        if name in DEVELOPER_ONLY:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.attr in STARTS.get(node.value.id, ())):
                found.append(f"{name}:{node.lineno} {node.value.id}.{node.attr}")
            elif isinstance(node, ast.ImportFrom) and node.module in STARTS:
                found += [f"{name}:{node.lineno} from {node.module} import {a.name}"
                          for a in node.names if a.name in STARTS[node.module] or a.name == "*"]
    assert not found, "start these through wozmod.process.run:\n" + "\n".join(found)


def test_windows_starts_have_no_console(monkeypatch):
    # Bug: the helper drops DETACHED_PROCESS (or the caller's other flags), so each ffmpeg run opens a console window.
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda command, **options: seen.update(options))
    monkeypatch.setattr(os, "name", "nt")
    process.run(["ffmpeg"], creationflags=0x200, check=True)
    monkeypatch.undo()
    assert seen == {"creationflags": 0x208, "check": True}  # Win32 DETACHED_PROCESS is 0x8
