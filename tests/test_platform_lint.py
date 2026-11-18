"""What keeps wozmod's OS branches in wozmod.platform, where the contract suite runs them on each system, and its
program starts behind platform.run_hidden."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src/wozmod"
PLATFORM = SOURCE / "platform"
# Asks which system this is, or does what only one system can.
OS_ATTRIBUTES = {("sys", "platform"), ("os", "name")}
OS_NAMES = {"killpg", "setpgrp", "setpgid", "setsid", "nice", "process_group", "sched_setscheduler", "SCHED_BATCH",
            "windll", "WinDLL", "creationflags", "startupinfo", "start_new_session"}
OS_MODULES = {"ctypes", "winreg", "msvcrt", "_winapi", "fcntl", "termios", "resource"}
# Developer-only starts (the DLL build) may open what they like.
DEVELOPER_ONLY = {"runtime/english_runtime.py", "runtime/true_rate.py"}
STARTS = {"subprocess": {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
          "os": {"system", "popen", "startfile", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
                 "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp",
                 "execvpe", "posix_spawn", "posix_spawnp"},
          "asyncio": {"create_subprocess_exec", "create_subprocess_shell"}}


def _outside_platform():
    for path in sorted(SOURCE.rglob("*.py")):
        if PLATFORM not in path.parents:
            yield path.relative_to(SOURCE).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _os_branches(name: str, tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and (node.value.id, node.attr) in OS_ATTRIBUTES):
            found.append(f"{name}:{node.lineno} {node.value.id}.{node.attr}")
        elif isinstance(node, ast.Attribute) and node.attr in OS_NAMES:
            found.append(f"{name}:{node.lineno} .{node.attr}")
        elif isinstance(node, ast.keyword) and node.arg in OS_NAMES:
            found.append(f"{name}:{node.value.lineno} {node.arg}=")
        elif isinstance(node, ast.Constant) and node.value in OS_NAMES:  # hasattr(os, "killpg")
            found.append(f"{name}:{node.lineno} {node.value!r}")
        elif isinstance(node, ast.Import):
            found += [f"{name}:{node.lineno} import {a.name}" for a in node.names
                      if a.name.split(".")[0] in OS_MODULES]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] in OS_MODULES:
            found.append(f"{name}:{node.lineno} from {node.module}")
    return found


def test_no_module_outside_platform_branches_on_the_system():
    # Bug: a new `if os.name == "nt":` (or a ctypes call, a killpg, a nice) in installer code. Linux runs only its
    # POSIX side, the Windows side runs first on a player's machine, and the contract suite never sees it.
    found = [line for name, tree in _outside_platform() for line in _os_branches(name, tree)]
    assert not found, "move these into wozmod/platform/ (posix.py and windows.py):\n" + "\n".join(found)


def test_both_adapters_offer_the_same_functions():
    # Bug: a function is added to posix.py only, so Linux is green and the Windows release fails at its first import
    # of wozmod.platform.
    def public(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                and not n.name.startswith("_")}

    assert public(PLATFORM / "posix.py") == public(PLATFORM / "windows.py")


def test_installer_code_starts_programs_only_through_the_helper():
    # Bug: a new call site uses subprocess.run directly, and the setup window opens a console per run on Windows.
    found = []
    for name, tree in _outside_platform():
        if name in DEVELOPER_ONLY:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.attr in STARTS.get(node.value.id, ())):
                found.append(f"{name}:{node.lineno} {node.value.id}.{node.attr}")
            elif isinstance(node, ast.ImportFrom) and node.module in STARTS:
                found += [f"{name}:{node.lineno} from {node.module} import {a.name}"
                          for a in node.names if a.name in STARTS[node.module] or a.name == "*"]
    assert not found, "start these through wozmod.platform.run_hidden (or ProcessTree):\n" + "\n".join(found)
