"""Terminal prompts: the questions the command asks when a terminal is attached."""

from __future__ import annotations

import sys
from pathlib import Path


def interactive() -> bool:
    return bool(sys.stdin and sys.stdin.isatty())


def clean(answer: str) -> str:
    """A path as typed or dragged into a terminal: without the quotes some terminals put around it."""
    answer = answer.strip()
    if len(answer) >= 2 and answer[0] == answer[-1] and answer[0] in "\"'":
        answer = answer[1:-1]
    return answer.strip()


def ask_yes(question: str, default: bool) -> bool:
    while True:
        answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def ask_choice(options: list[str]) -> int | None:
    """Show a numbered menu and return the 1-based pick; the last option is Quit and returns None."""
    for number, text in enumerate(options, 1):
        print(f"  {number}) {text}")
    while True:
        answer = input(f"Choose 1-{len(options)}: ").strip()
        if answer.isdecimal() and 1 <= int(answer) <= len(options):
            return None if int(answer) == len(options) else int(answer)


def ask_path(label: str, offered: str | None, check) -> Path:
    """Ask until the answer passes the check; an empty answer takes the remembered path."""
    while True:
        answer = clean(input(f"{label}{f' [{offered}]' if offered else ''}: ")) or offered or ""
        if not answer:
            continue
        print("  checking...", flush=True)
        problem = check(Path(answer))
        if problem is None:
            print("  ✓ OK")
            return Path(answer)
        print(f"  {problem}")
