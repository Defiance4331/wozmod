"""The main checkout, from it or any linked worktree. Imports nothing POSIX-only: tests/conftest.py uses it on Windows."""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main_checkout() -> Path:
    """The main checkout; a linked worktree's .git file names its folder in the main checkout's .git/worktrees/."""
    git = REPO / ".git"
    return Path(git.read_text().removeprefix("gitdir:").strip()).parents[2] if git.is_file() else REPO


def work() -> Path:
    """The developer tools' work/: the main checkout's, from it or any linked worktree, so runs, game slots and caches
    are shared."""
    return main_checkout() / "work"


def config() -> Path:
    """The machine's harness.toml: the file WOZMOD_HARNESS_CONFIG names, else work()/harness.toml."""
    return Path(os.environ.get("WOZMOD_HARNESS_CONFIG", work() / "harness.toml"))
