#!/usr/bin/env python3
"""Lay out a check run's captures beside their PASS/FAIL rows as one self-contained HTML page.

    uv run python tools/contact_sheet.py work/runtime-tests/<run>/evidence

Reads evidence/check-report.json and writes evidence/contact-sheet.html, quicker to review than the
table alone. A screen whose assertions read no pixels was not captured, and the page says so.
"""

from __future__ import annotations

import base64
import html
import io
import json
import sys
from pathlib import Path

from PIL import Image

THUMB_WIDTH = 960  # px: the text stays legible at a 1.5x capture

STYLE = """
:root { --bg: #fafaf8; --fg: #1c1c1a; --muted: #6b6b66; --card: #ffffff; --line: #e2e2dc;
        --pass: #1f7a3a; --fail: #b3261e; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
        --bg: #161615; --fg: #ecece8; --muted: #9a9a94; --card: #20201e; --line: #34342f;
        --pass: #5cc27a; --fail: #ff8a80; } }
:root[data-theme="dark"] { --bg: #161615; --fg: #ecece8; --muted: #9a9a94; --card: #20201e; --line: #34342f;
        --pass: #5cc27a; --fail: #ff8a80; }
body { margin: 0; padding: 24px 16px; background: var(--bg); color: var(--fg);
       font: 14px/1.45 system-ui, sans-serif; }
main { max-width: 1000px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; }
.summary { color: var(--muted); margin: 0 0 24px; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px;
          margin: 0 0 16px; }
h2 { font-size: 16px; margin: 0 0 8px; }
img { display: block; width: 100%; height: auto; border-radius: 4px; }
.none { color: var(--muted); font-style: italic; }
ul { list-style: none; padding: 0; margin: 8px 0 0; font: 12px/1.5 ui-monospace, monospace; }
li { overflow-wrap: anywhere; }
.PASS b { color: var(--pass); } .FAIL b { color: var(--fail); }
"""


def thumbnail(png: Path) -> str:
    image = Image.open(png).convert("RGB")
    if image.width > THUMB_WIDTH:
        image = image.resize((THUMB_WIDTH, round(image.height * THUMB_WIDTH / image.width)), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def sheet(report: dict, title: str) -> str:
    screens: dict[str, list[dict]] = {}
    for row in report["rows"]:
        screens.setdefault(row["screen"], []).append(row)
    failed = sum(not row["ok"] for row in report["rows"])
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8>"
             f"<meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>Check contact sheet</title><style>{STYLE}</style></head><body><main>"
             f"<h1>{html.escape(title)}</h1><p class=summary>{len(screens)} screens, {len(report['rows'])} rows, "
             f"{failed} failed</p>"]
    for name, rows in screens.items():
        capture = next((Path(row["capture"]) for row in rows if row["capture"]), None)
        shown = (f"<img alt='{html.escape(name)}' src='{thumbnail(capture)}'>" if capture and capture.is_file()
                 else "<p class=none>No capture: none of this screen's checks read pixels.</p>")
        items = "".join(
            f"<li class={'PASS' if row['ok'] else 'FAIL'}><b>{'PASS' if row['ok'] else 'FAIL'}</b> "
            f"{html.escape(row['assertion'])} {html.escape(json.dumps(row['numbers']))}</li>" for row in rows)
        parts.append(f"<section><h2>{html.escape(name)}</h2>{shown}<ul>{items}</ul></section>")
    return "".join(parts) + "</main></body></html>\n"


def write(evidence: Path) -> Path:
    report = json.loads((evidence / "check-report.json").read_text())
    out = evidence / "contact-sheet.html"
    out.write_text(sheet(report, f"{evidence.parent.name}: {Path(report['spec']).name}"))
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(write(Path(sys.argv[1])))
