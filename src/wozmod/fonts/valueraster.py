"""Valueraster rendering support."""
import argparse
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from . import valuevec
from .menu_effects import Label, coverage, ink_layout, outline_table, path
from .word_key import word_key


# texts picks a face per drawn word, keyed by word_key(word), the word's SHA-256, so the official word is not stored.
LOOKS = {
    "value_dim": dict(face="BOLD", fill=(156, 156, 156), grow=0.0,
                   baseline_faces=("BOLD", "LIGHT"),
                   texts={"5fbdda1b8c72e849493a8534652682ed35b5bde51896cd1f457278883b0c5cf4": "LIGHT",
                          "b43e72eed0488d784f1e3092b48320e2355e9d6313f7ac401542018eec20d8a3": "LIGHT",
                          "7c48dd672a68098c3a9dac57805004a146db39c8d60c81d091537ee9c4a76054": "BOLD_MEDIUM",
                          "db2cb3fe28e2c54fce50bd9c03fd2b131091229c0ceda79289527aaba47226ad": "BOLD_NORMAL",
                          "5b6728469b192620a47d6e12723f3180553bffc934d5ab62ebb1a0ea9cb4d8aa": "BOLD_CUSTOMIZE"}, rings=((3.5, (49, 52, 49), 1.0),)),
    "value_lit": dict(face="BOLD", fill=(253, 238, 156), grow=0.0,
                   baseline_faces=("BOLD", "LIGHT"),
                   texts={"5fbdda1b8c72e849493a8534652682ed35b5bde51896cd1f457278883b0c5cf4": "LIGHT",
                          "b43e72eed0488d784f1e3092b48320e2355e9d6313f7ac401542018eec20d8a3": "LIGHT",
                          "7c48dd672a68098c3a9dac57805004a146db39c8d60c81d091537ee9c4a76054": "BOLD_MEDIUM",
                          "db2cb3fe28e2c54fce50bd9c03fd2b131091229c0ceda79289527aaba47226ad": "BOLD_NORMAL",
                          "5b6728469b192620a47d6e12723f3180553bffc934d5ab62ebb1a0ea9cb4d8aa": "BOLD_CUSTOMIZE"}, rings=((3.5, (49, 52, 49), 1.0),)),
    "choice_dim": dict(face="REGULAR", fill=(201, 201, 201), grow=0.0,
                   baseline_faces=("REGULAR",), texts={"0e24076baf990d55aa1511d1af1d6741b19c9a2e9e9911f9ec7b5d5dbe5a5e9f": "HARD_FACE",
                                                       "db2cb3fe28e2c54fce50bd9c03fd2b131091229c0ceda79289527aaba47226ad": "DIFFICULTY_FACE",
                                                       "dc1a2f9d7bd77176adf5cfec6d2efaddd343e7562000ee31f8f986f037d33ddf": "CHAOS_DIM"},
                   rings=((3.71, (79, 78, 79), 1.0), (1.93, (48, 51, 48), 0.97),)),
    "choice_lit": dict(face="REGULAR", fill=(251, 250, 251), grow=-0.03,
                   baseline_faces=("REGULAR",), texts={"0e24076baf990d55aa1511d1af1d6741b19c9a2e9e9911f9ec7b5d5dbe5a5e9f": "HARD_FACE",
                                                       "db2cb3fe28e2c54fce50bd9c03fd2b131091229c0ceda79289527aaba47226ad": "DIFFICULTY_FACE",
                                                       "dc1a2f9d7bd77176adf5cfec6d2efaddd343e7562000ee31f8f986f037d33ddf": "CHAOS_LIT"},
                   rings=((4.45, (79, 79, 79), 1.0), (2.04, (53, 52, 53), 0.85),)),
    "confirm_dim": dict(face="CONFIRM_FACE", fill=(205, 204, 205), grow=0.0, rings=((2.53, (82, 81, 82), 1.0),)),
    "confirm_lit": dict(face="CONFIRM_FACE", fill=(254, 254, 254), grow=0.0, rings=((2.53, (82, 81, 82), 1.0),)),
}


@lru_cache(maxsize=None)
def glyphs(face):

    V = getattr(valuevec, face)
    return outline_table(V, V.layer)


def layout(face, text):


    V = getattr(valuevec, face)
    return ink_layout(text, V, glyphs(face), V.SETS.get(word_key(text)))


def face_for(text: str, look: str, ref: str | None = None) -> str:

    L = LOOKS[look]
    if look.startswith("choice_") and ref is not None:
        variant = valuevec.REF_FACES.get(f"{word_key(text)}@{ref}")
        if variant is not None:
            return variant
    return L.get("texts", {}).get(word_key(text), L["face"])


def render(text, scale=1.0, hscale=1.0, look="value_dim", ref=None):


    L = LOOKS[look]
    face = face_for(text, look, ref)
    V, g = getattr(valuevec, face), glyphs(face)
    placed, advance = layout(face, text)
    s, sx = scale, scale * hscale
    reach = max(r for r, _, _ in L["rings"]) + L["grow"]
    m = math.ceil(reach * s) + 2
    faces = L.get("baseline_faces", (L["face"],))
    asc = max(getattr(valuevec, f).CAP - b[1] for f in faces for _, b, _ in glyphs(f).values())
    desc = max(b[3] - getattr(valuevec, f).CAP for f in faces for _, b, _ in glyphs(f).values())
    baseline = m + math.ceil(asc * s)
    h = baseline + math.ceil(desc * s) + m
    xmin = min(x + g[ch][1][0] for ch, x in placed)
    xmax = max(x + g[ch][1][2] for ch, x in placed)
    ymin = min(g[ch][1][1] for ch, _ in placed) - V.CAP
    ymax = max(g[ch][1][3] for ch, _ in placed) - V.CAP
    ox = m - xmin * sx
    w = math.ceil((xmax - xmin) * sx) + 2 * m

    def draw(ctx):
        for ch, x in placed:
            path(ctx, g[ch][0], ox + x * sx, baseline - V.CAP * s, sx, s)

    pre, alpha = np.zeros((h, w, 3)), np.zeros((h, w))
    for r, rgb, a in [*L["rings"], (0.0, L["fill"], 1.0)]:
        c = coverage(draw, w, h, max((r + L["grow"]) * s, 0.0)) * a  # a negative grow leaves the plain fill
        pre = c[..., None] * np.array(rgb, float) + (1 - c[..., None]) * pre
        alpha = c + (1 - c) * alpha
    rgb = pre / np.maximum(alpha, 1e-9)[..., None]
    rgba = np.dstack([np.clip(rgb + 0.5, 0, 255), np.clip(alpha * 255 + 0.5, 0, 255)]).astype(np.uint8)
    return Label(Image.fromarray(rgba, "RGBA"), baseline,
                 (m, baseline + ymin * s, ox + xmax * sx, baseline + ymax * s), ox, advance * sx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text")
    parser.add_argument("output", type=Path)
    parser.add_argument("--look", default="value_dim", choices=sorted(LOOKS))
    parser.add_argument("--scale", type=float, default=1.0, help="device px per official 1x texel")
    parser.add_argument("--hscale", type=float, default=1.0, help="horizontal condensing, for A/B only")
    parser.add_argument("--ref", help="official sheet, e.g. 15.9 or 15.10")
    args = parser.parse_args()
    label = render(args.text, args.scale, args.hscale, args.look, args.ref)
    label.emit(args.output)
