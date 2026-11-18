"""Publishing a release closes the issues its commits name on a `Reported in #N` line, and no others."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging/close_released_issues.py"
URL = "https://example.invalid/releases/tag/v2.0.0"

# A stand-in for gh: answers `gh api repos/R/issues/N` from issues.json, and records `gh issue close` in it.
FAKE_GH = """\
import json, sys
from pathlib import Path
store = Path(__file__).with_name("issues.json")
issues = json.loads(store.read_text())
args = sys.argv[1:]
if args[0] == "api":
    issue = issues.get(args[1].rsplit("/", 1)[1])
    if issue is None:
        sys.exit("HTTP 404: Not Found")
    print(json.dumps(issue))
elif args[:2] == ["issue", "close"]:
    issue = issues[args[2]]
    issue["state"] = "closed"
    issue.setdefault("comments", []).append(args[args.index("--comment") + 1])
    store.write_text(json.dumps(issues))
"""


GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, check=True)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


@pytest.mark.linux_only("the close-issues workflow runs it on ubuntu-latest, its only caller")
def test_publishing_closes_each_open_issue_the_release_fixes_once(tmp_path: Path) -> None:
    # Bug: the commit range starts at the wrong tag (so #5, fixed in v1 and reopened, is closed again, or this
    # release's issues are missed), a bare mention or a pull request gets closed, one unreadable number stops the
    # rest, or a rerun comments twice; or publishing a release that is not a version closes the fixes still
    # waiting for the next version, linking the wrong release.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "Fix the first crash\n\nReported in #5")
    _git(repo, "tag", "v1.0.0")
    _commit(repo, "Fix two crashes\n\nSee #9 for the history.\n\nReported in #3 and #4")
    _commit(repo, "Fix a slowdown\n\nReported in #7, #8, #6")
    _git(repo, "tag", "v2.0.0")
    _commit(repo, "Fix after the release\n\nReported in #10")
    _git(repo, "tag", "tools")  # another release on the page, published before the next version

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "fake_gh.py").write_text(FAKE_GH, encoding="utf-8")
    (bin_dir / "gh").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bin_dir / "fake_gh.py"}" "$@"\n', encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    # #6 does not exist; #7 is a pull request; #8 was closed by hand.
    issues = {"3": {"state": "open"}, "4": {"state": "open"}, "5": {"state": "open"},
              "7": {"state": "open", "pull_request": {}}, "8": {"state": "closed"},
              "9": {"state": "open"}, "10": {"state": "open"}}
    (bin_dir / "issues.json").write_text(json.dumps(issues), encoding="utf-8")

    run_env = {**GIT_ENV, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "GITHUB_REPOSITORY": "o/r"}
    other = subprocess.run([sys.executable, str(SCRIPT), "tools", URL.replace("v2.0.0", "tools")], cwd=repo,
                             env=run_env, capture_output=True, text=True, encoding="utf-8")
    assert other.returncode == 0 and json.loads((bin_dir / "issues.json").read_text(encoding="utf-8")) == issues
    runs = [subprocess.run([sys.executable, str(SCRIPT), "v2.0.0", URL], cwd=repo, env=run_env,
                           capture_output=True, text=True, encoding="utf-8") for _ in range(2)]

    after = json.loads((bin_dir / "issues.json").read_text(encoding="utf-8"))
    closed = {n: issue.get("comments") for n, issue in after.items() if issue["state"] == "closed"}
    assert closed == {"3": [f"Fixed in v2.0.0: {URL}"], "4": [f"Fixed in v2.0.0: {URL}"], "8": None}
    # The missing #6 fails the run, so a mistyped number is seen, after the other issues are closed.
    assert [run.returncode for run in runs] == [1, 1]
