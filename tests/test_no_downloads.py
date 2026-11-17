"""Outside pieces ship inside the release zip: the installer's code makes no network request and names no download.

The installer is the Python under src/wozmod and the Rust under native/src that the release carries. A link the player
opens in a browser (the README's sections) is not a download. native/build.rs fetches PocketSphinx's source while the
release is built, on the developer machine, and is not part of the installer.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Modules that open a connection; webbrowser only hands a page to the player's browser.
NETWORK_MODULES = {"urllib.request", "urllib3", "http.client", "httpx", "requests", "aiohttp", "socket", "ssl",
                   "ftplib", "smtplib", "xmlrpc.client", "websocket", "websockets", "asyncio.streams"}
DOWNLOADERS = {"curl", "wget", "bitsadmin", "Invoke-WebRequest", "Start-BitsTransfer"}
README_LINK = re.compile(r"https://github\.com/Defiance4331/wozmod#[a-z0-9-]+")
RUST_NETWORK = re.compile(r"://|std::net|\breqwest\b|\bureq\b|\bhyper\b|TcpStream|UdpSocket")


def _python_findings(path: Path) -> list[str]:
    found, tree = [], ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
        for name in names:
            if any(name == m or name.startswith(m + ".") for m in NETWORK_MODULES):
                found.append(f"{path.relative_to(REPO)}:{node.lineno}: imports {name}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if ("://" in text and not README_LINK.fullmatch(text)) or text in DOWNLOADERS:
                found.append(f"{path.relative_to(REPO)}:{node.lineno}: {text[:80]!r}")
    return found


def test_the_installer_makes_no_network_request_and_names_no_download():
    # Bug: an install step fetches a helper, model or update at install time (a URL in the code, an HTTP module, a
    # curl run), so a player without a connection, or after the host takes the file down, cannot install.
    found = [f for path in sorted((REPO / "src/wozmod").rglob("*.py")) for f in _python_findings(path)]
    for path in sorted((REPO / "native/src").rglob("*.rs")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if RUST_NETWORK.search(line):
                found.append(f"{path.relative_to(REPO)}:{number}: {line.strip()[:80]}")
    assert found == []
