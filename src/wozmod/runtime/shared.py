"""The defines the installer's build steps read from the runtime: runtime/shared.h only.

The DLL includes the same file, so the steps and the runtime agree on these values. Nothing else of the runtime's C is
read here: a change to it keeps every install step's key (wozmod.install.step_keys)."""

from __future__ import annotations

import re

from wozmod import resources

SHARED_H = resources.root() / "runtime/shared.h"  # one string, so the step keys name this file alone
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_INTEGER = re.compile(r"(-?(?:0x[0-9A-Fa-f]+|\d+))[uU]?")


def defines(source: str) -> dict[str, int]:
    """Read every #define whose value is one integer: bare, in parentheses or behind casts (``0x1F``, ``(-3)``,
    ``((void (*)(u32))0x4F4230)``). Defines of anything else (an expression, a float, a macro) are left out."""
    found = {}
    for name, value in re.findall(r"^[ \t]*#define[ \t]+(\w+)[ \t]+(.*?)[ \t]*$", _COMMENTS.sub("", source), re.M):
        literal = re.search(rf"{_INTEGER.pattern}(\)*)$", value)
        if literal is None:
            continue
        head = value[:literal.start()].rstrip()
        if head and (head[-1] not in "()" or head.count("(") != head.count(")") + len(literal[2])):
            continue  # the number ends an expression, not a cast
        found[name] = int(literal[1], 0)
    return found


DEFINES = defines(SHARED_H.read_text(encoding="utf-8"))
