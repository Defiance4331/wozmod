"""The commit-msg hook keeps commit messages to the changelog format in AGENTS.md."""

from __future__ import annotations

import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "tools/githooks/commit-msg"
WHY = "Players who are not developers could not tell what to pick."
GOOD = ["- Add -v/--version, and show the version in the window's corner.",
        "- Say why Install is disabled, and what to do after an error."]


def _message(subject: str = "Show the version and redesign the setup window", changes: list[str] = GOOD,
             extra: str = "") -> str:
    return "\n".join([subject, "", WHY + extra, "", "Changes:", *changes, ""])


CASES = [
    ("the AGENTS.md example", _message(), True),
    ("a merge", "Merge branch 'x'\n", True),
    ("git's comment lines", _message() + "# Please enter the commit message\n", True),
    ("a 73-character subject", _message("S" * 73), False),
    ("one change", _message(changes=GOOD[:1]), False),
    ("no Changes section", "Fix the thing\n\nIt broke.\n", False),
    ("seven changes", _message(changes=GOOD * 3 + GOOD[:1]), False),
    ("a test count", _message(extra=" 574 passed."), False),
    ("a file list", _message(changes=GOOD + ["- src/wozmod/window.py: redesign"]), False),
    ("an open to-do", _message(changes=GOOD + ["- Open: the PSP rows"]), False),
    ("a private ADR", _message(extra=" See ADR 0026."), False),
    ("the private folder", _message(extra=" Notes in private/layout-decisions.md."), False),
    ("the vault", _message(extra=" Filed in the vault."), False),
]


def test_commit_msg_hook_accepts_the_changelog_format_and_refuses_the_rest(tmp_path: Path) -> None:
    # Bug: a regex typo lets a file list, a test count or a private ADR number into a public commit
    # message, or the hook starts refusing the format AGENTS.md documents.
    wrong = []
    for name, message, accepted in CASES:
        path = tmp_path / "COMMIT_EDITMSG"
        path.write_text(message, encoding="utf-8")
        run = subprocess.run(["sh", str(HOOK), str(path)], capture_output=True, text=True)
        if (run.returncode == 0) != accepted:
            wrong.append(f"{name}: exit {run.returncode} {run.stderr.strip()}")
    assert wrong == []
