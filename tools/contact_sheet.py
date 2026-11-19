#!/usr/bin/env python3
"""Lay out a check run's captures beside their PASS/FAIL rows as one self-contained HTML page.

    uv run python tools/contact_sheet.py work/runtime-tests/<run>/evidence

Reads evidence/check-report.json and writes evidence/contact-sheet.html, quicker to review than the
table alone. A screen whose assertions read no pixels was not captured, and the page says so.
`run_check.py --ab` writes evidence/ab-sheet.html the same way from two runs (write_ab): each screen's A and B
captures side by side, cropped to the screen's `crop` box when its check names one.
"""

from __future__ import annotations

import base64
import html
import io
import json
import sys
from pathlib import Path

from PIL import Image

from screen_checks import LAYOUT_W, layout_area

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
main.ab { max-width: 1600px; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 640px) { .pair { grid-template-columns: 1fr; } }
figure { margin: 0; min-width: 0; }
figcaption { color: var(--muted); margin: 0 0 4px; overflow-wrap: anywhere; }
img { display: block; width: 100%; height: auto; border-radius: 4px; }
.none { color: var(--muted); font-style: italic; }
ul { list-style: none; padding: 0; margin: 8px 0 0; font: 12px/1.5 ui-monospace, monospace; }
li { overflow-wrap: anywhere; }
.PASS b { color: var(--pass); } .FAIL b { color: var(--fail); }
"""


def thumbnail(png: Path, crop: list[int] | None = None) -> str:
    """The capture as an inline JPEG, at most THUMB_WIDTH wide; ``crop``: only that [x0, y0, x1, y1] box of the
    layout's 1280x720 space."""
    image = Image.open(png).convert("RGB")
    if crop:
        area = layout_area(image)
        s = area.width / LAYOUT_W
        image = area.crop(tuple(round(v * s) for v in crop))
    if image.width > THUMB_WIDTH:
        image = image.resize((THUMB_WIDTH, round(image.height * THUMB_WIDTH / image.width)), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def head(title: str, summary: str, wide: bool = False) -> str:
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{'A/B' if wide else 'Check'} contact sheet</title><style>{STYLE}</style></head><body><main{' class=ab' if wide else ''}>"
            f"<h1>{html.escape(title)}</h1><p class=summary>{summary}</p>")


def by_screen(report: dict) -> dict[str, list[dict]]:
    screens: dict[str, list[dict]] = {}
    for row in report["rows"]:
        screens.setdefault(row["screen"], []).append(row)
    return screens


def shown(name: str, rows: list[dict], crop: list[int] | None = None) -> str:
    """The screen's capture and its PASS/FAIL rows."""
    capture = next((Path(row["capture"]) for row in rows if row["capture"]), None)
    image = (f"<img alt='{html.escape(name)}' src='{thumbnail(capture, crop)}'>" if capture and capture.is_file()
             else "<p class=none>No capture: " + ("none of this screen's checks read pixels." if rows
                                                  else "this run did not reach the screen.") + "</p>")
    items = "".join(
        f"<li class={'PASS' if row['ok'] else 'FAIL'}><b>{'PASS' if row['ok'] else 'FAIL'}</b> "
        f"{html.escape(row['assertion'])} {html.escape(json.dumps(row['numbers']))}</li>" for row in rows)
    return f"{image}<ul>{items}</ul>"


def sheet(report: dict, title: str) -> str:
    screens = by_screen(report)
    failed = sum(not row["ok"] for row in report["rows"])
    parts = [head(title, f"{len(screens)} screens, {len(report['rows'])} rows, {failed} failed")]
    for name, rows in screens.items():
        parts.append(f"<section><h2>{html.escape(name)}</h2>{shown(name, rows)}</section>")
    return "".join(parts) + "</main></body></html>\n"


def write_ab(a: Path, b: Path, crops: dict[str, list[int]]) -> Path:
    """b/ab-sheet.html: each screen of the two runs' evidence folders ``a`` and ``b``, A left and B right, in B's
    order, cropped to ``crops``[screen] when the check names a box."""
    reports = [json.loads((side / "check-report.json").read_text(encoding="utf-8")) for side in (a, b)]
    screens = [by_screen(report) for report in reports]
    names = [run.parent.name for run in (a, b)]
    failed = [sum(not row["ok"] for row in report["rows"]) for report in reports]
    parts = [head(f"A/B: {Path(reports[1]['spec']).name}",
                  f"A {html.escape(names[0])} ({failed[0]} failed), B {html.escape(names[1])} ({failed[1]} failed)",
                  wide=True)]
    for name in dict.fromkeys([*screens[1], *screens[0]]):
        cells = "".join(f"<figure><figcaption>{side} {html.escape(run)}</figcaption>"
                        f"{shown(name, rows.get(name, []), crops.get(name))}</figure>"
                        for side, run, rows in zip("AB", names, screens, strict=True))
        parts.append(f"<section><h2>{html.escape(name)}</h2><div class=pair>{cells}</div></section>")
    out = b / "ab-sheet.html"
    out.write_text("".join(parts) + "</main></body></html>\n", encoding="utf-8")
    return out


def write(evidence: Path) -> Path:
    report = json.loads((evidence / "check-report.json").read_text(encoding="utf-8"))
    out = evidence / "contact-sheet.html"
    out.write_text(sheet(report, f"{evidence.parent.name}: {Path(report['spec']).name}"), encoding="utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(write(Path(sys.argv[1])))
