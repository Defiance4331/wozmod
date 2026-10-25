"""Developer contact sheet for the drawn button prompts (wozmod.art.prompt_glyphs), beside today's Xbox 360 art.

Writes work/glyphs/sheet-<scale>.png and work/glyphs/sheet.html: per slot, the Xbox 360 art, the modern Xbox set and
the PlayStation set, drawn as the game draws them (a 32-wide slot as a 30x30 quad, a 64-wide one as 60x30, at 1280x720
logical, scaled 1x, 1.5x and 3x), on a crop of the in-game prompt bar and on bright menu art.

Inputs, in the main checkout's work/glyphs/: current-x360-prompts.png (each slot's texture at 2x on a flat dark
ground, so the Xbox 360 column keeps that ground) and in-game-keyconfig-keyboard-prompts.png (a 1920x1080 capture).
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

from PIL import Image, ImageDraw

from wozmod.art.prompt_glyphs import SLOT_WIDTH, prompt_art

ROOT = Path(__file__).resolve().parent.parent
GLYPHS = ROOT.parent.parent / "work/glyphs" if ROOT.parent.name == ".worktrees" else ROOT / "work/glyphs"
SCALES = (1.0, 1.5, 3.0)
CAPTURE_SCALE = 1.5  # the capture is 1920x1080 of a 1280x720 layout
BAR = (1780, 940, 1920, 1016)  # empty stretch of the prompt bar, with a little of the art above and below
BRIGHT = (600, 297, 800, 349)  # the highlighted row, empty at its right
CELL_PAD = 6


def x360_art(reference: Image.Image) -> dict[int, Image.Image]:
    """Each slot's texture at 1x, cut from the reference sheet (2x art, 7 per row, 150 px apart, rows 110 apart)."""
    order = [*range(16), 44, 178, 179]
    out = {}
    for i, slot in enumerate(order):
        col, row = i % 7, i // 7
        x, y = col * 150 + 5, row * 110 + 18
        if slot not in SLOT_WIDTH:
            continue
        w = SLOT_WIDTH[slot]
        out[slot] = reference.crop((x, y, x + 2 * w, y + 64)).resize((w, 32), Image.Resampling.BOX)
    return out


def _ground(capture: Image.Image, box, width: int, height: int) -> Image.Image:
    """A capture crop stretched to width, scaled so its height is height."""
    return capture.crop(box).resize((width, height), Image.Resampling.BICUBIC)


def _drawn(texture: Image.Image, scale: float) -> Image.Image:
    """The texture as the game draws it: a quad of 30/32 its size at 1280x720, sampled bilinearly."""
    w = round(texture.width * 30 / 32 * scale)
    h = round(30 * scale)
    return texture.convert("RGBa").resize((w, h), Image.Resampling.BILINEAR).convert("RGBA")


def sheet(scale: float, sets: list[tuple[str, dict[int, Image.Image]]], capture: Image.Image) -> Image.Image:
    col_w = round(60 * scale) + 2 * CELL_PAD
    bar_h = round((BAR[3] - BAR[1]) / CAPTURE_SCALE * scale)
    bright_h = round((BRIGHT[3] - BRIGHT[1]) / CAPTURE_SCALE * scale)
    row_h = max(bar_h, bright_h) + 4
    label_w, head_h = 44, 30
    groups = (("prompt bar", BAR, bar_h), ("bright art", BRIGHT, bright_h))
    width = label_w + len(groups) * (len(sets) * col_w + 12)
    out = Image.new("RGB", (width, head_h + row_h * len(SLOT_WIDTH)), (24, 24, 28))
    draw = ImageDraw.Draw(out)
    for g, (gname, _, _) in enumerate(groups):
        gx = label_w + g * (len(sets) * col_w + 12)
        draw.text((gx, 2), gname, fill=(200, 200, 200))
        for s, (name, _) in enumerate(sets):
            draw.text((gx + s * col_w + 2, 15), name, fill=(160, 160, 160))
    for r, slot in enumerate(SLOT_WIDTH):
        y = head_h + r * row_h
        draw.text((4, y + row_h // 2 - 6), f"{slot} ({SLOT_WIDTH[slot]})", fill=(200, 200, 200))
        for g, (_, box, h) in enumerate(groups):
            gx = label_w + g * (len(sets) * col_w + 12)
            ground = _ground(capture, box, len(sets) * col_w, h).convert("RGBA")
            for s, (_, art) in enumerate(sets):
                icon = _drawn(art[slot], scale)
                ground.alpha_composite(icon, (s * col_w + CELL_PAD, (h - icon.height) // 2))
            out.paste(ground.convert("RGB"), (gx, y))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=GLYPHS)
    args = parser.parse_args()
    capture = Image.open(GLYPHS / "in-game-keyconfig-keyboard-prompts.png").convert("RGB")
    sets = [("x360", x360_art(Image.open(GLYPHS / "current-x360-prompts.png").convert("RGBA"))),
            ("xbox", prompt_art("xbox")), ("ps", prompt_art("playstation"))]
    args.out.mkdir(parents=True, exist_ok=True)
    names = []
    for scale in SCALES:
        image = sheet(scale, sets, capture)
        name = f"sheet-{scale:g}x.png"
        image.save(args.out / name)
        names.append((name, f"{scale:g}x"))
        if scale == 1.0:  # the 1x sheet enlarged without smoothing, to judge single pixels
            image.resize((image.width * 4, image.height * 4), Image.Resampling.NEAREST).save(args.out / "sheet-1x-zoom4.png")
            names.append(("sheet-1x-zoom4.png", "1x, enlarged 4x (nearest)"))
    for family, art in sets[1:]:  # the raw textures, for the record
        strip = Image.new("RGBA", (sum(a.width + 4 for a in art.values()), 32))
        x = 0
        for a in art.values():
            strip.paste(a, (x, 0))
            x += a.width + 4
        strip.save(args.out / f"textures-{family}.png")
    body = "\n".join(f"<h2>{html.escape(title)}</h2><img src='{n}'>" for n, title in names)
    (args.out / "sheet.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Button prompt glyphs</title>"
        "<style>body{background:#18181c;color:#ddd;font:14px sans-serif}img{image-rendering:pixelated}</style>"
        "<h1>Button prompts: Xbox 360 (today) / modern Xbox / PlayStation</h1>"
        "<p>Columns per background: x360, xbox, ps. Each texture is drawn as the game draws it (30/32 of its size "
        "at 1280x720, times the scale). The x360 column keeps the flat ground of its reference image.</p>"
        f"{body}\n")
    print(args.out / "sheet.html")


if __name__ == "__main__":
    main()
