"""Close the GitHub issues a newly published release fixes, each with a comment that links the release.

A commit that fixes an issue ends with a line such as `Reported in #17` or `Reported in #16 and #17`. GitHub does not
close an issue for that wording, so the issue stays open until players can download the fix. When a release is
published, the close-issues workflow runs this script with the release's tag and page:

    python3 packaging/close_released_issues.py v0.2.2 https://github.com/OWNER/REPO/releases/tag/v0.2.2

It reads the commits since the previous v* tag, and closes each issue they name that is still open. A rerun finds
those issues closed and leaves them alone. It needs only the standard library, git, and gh with GH_TOKEN and
GITHUB_REPOSITORY set, as GitHub Actions sets them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

REPORTED = re.compile(r"^Reported in (#\d+.*)$", re.MULTILINE)
# A wozmod release's tag. Any other release on the page (a test build, a tool) holds no wozmod fix, so publishing it
# closes nothing.
VERSION_TAG = re.compile(r"v\d+\.\d+\.\d+")


def reported_issues(messages: str) -> list[int]:
    """The issue numbers on the `Reported in` lines of these commit messages, in order, without repeats."""
    numbers = (int(n) for line in REPORTED.findall(messages) for n in re.findall(r"#(\d+)", line))
    return list(dict.fromkeys(numbers))


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def release_messages(tag: str) -> str:
    """The messages of the commits in this release: since the previous v* tag, or every commit for the first."""
    previous = _git("describe", "--tags", "--abbrev=0", "--match", "v*", f"{tag}^")
    span = f"{previous.stdout.strip()}..{tag}" if previous.returncode == 0 else tag
    log = _git("log", "--format=%B", span)
    if log.returncode != 0:
        sys.exit(f"git log {span} failed: {log.stderr.strip()}")
    return log.stdout


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.exit("usage: close_released_issues.py TAG RELEASE_URL")
    tag, url = argv
    if not VERSION_TAG.fullmatch(tag):
        print(f"{tag} is not a wozmod version, so its release fixes no issue.")
        return 0
    repo = os.environ.get("GITHUB_REPOSITORY") or sys.exit("GITHUB_REPOSITORY is not set (OWNER/REPO).")
    failed = False
    for number in reported_issues(release_messages(tag)):
        view = subprocess.run(["gh", "api", f"repos/{repo}/issues/{number}"], capture_output=True, text=True)
        if view.returncode != 0:
            print(f"::warning::#{number} is named in a commit but could not be read: {view.stderr.strip()}")
            failed = True
            continue
        issue = json.loads(view.stdout)
        if "pull_request" in issue:
            print(f"::warning::#{number} is a pull request, not an issue; left as it is.")
        elif issue["state"] != "open":
            print(f"#{number} is already closed.")
        else:
            close = subprocess.run(["gh", "issue", "close", str(number), "--repo", repo,
                                    "--comment", f"Fixed in {tag}: {url}"])
            failed |= close.returncode != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
