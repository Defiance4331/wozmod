"""Menu lettering drawn by the vector faces: the large-menu sheets (hires.bin), the dark plates and the native menu
labels, with the tables that place them and the review variants that choose their styles."""
from __future__ import annotations

import json
import math
import struct
import sys
import zlib
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from statistics import mode

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from wozmod.formats.linkdata import parse_lzp2
from wozmod import native
from wozmod.donors import WO1_X360, WO2_X360
from wozmod.progress import report as report_progress
from . import lettering, words
from .hires import HIRES_SCALE, HiresRow, hires_bank, hires_blocks
from wozmod.formats.textures import G1T_TEXTURE_HEADER, G1tFile, parse_g1t, parse_texture_package
from wozmod.fonts.menu_plates import X360_PLATES, clean_substrate
from wozmod.fonts.menu_references import reference
from .imaging import ink, restriped, rgba, tinted
from .packing import packed_lzp2, worker_pool
from .faces import BASELINE, gaps, ink_rows, source_image


TIME_LABEL_LAYOUT = {
    (4080, 14): ("time-purple", 5.0, 23.5),
    (4080, 27): ("time-blue", 24.0, 23.5),  
    (4074, 5): ("time-x360", 4.0, 24.0),
    (4077, 27): ("time-x360", 4.0, 24.0),
    (4085, 9): ("time-x360", 8.0, 24.0),
}

def menu_render_options(entry: int, item: int, row: int, piece: dict, style: str,
                        *, destination: tuple[int, int] | None = None) -> list[str]:

    from wozmod.fonts.menu_styles import help_treatment

    menu_style = "dark.camp-light" if piece.get(WO1_X360.name, [])[:2] == [1, 17] else MENU_STYLE[style]
    record = reference(f"{entry}.{item}", row) or {}
    role = record.get("role_variant")
    if menu_style == "dark.help":
        role = help_treatment(entry, item, row)
    elif not role and menu_style in {"dark.pause", "dark.camp"}:
        role = "blue" if piece.get(WO1_X360.name, [])[:2] in [[1, 18], [1, 19]] else "purple"
    time_key = (entry, item) if destination is None else destination
    if time_key in TIME_LABEL_LAYOUT:
        role = TIME_LABEL_LAYOUT[time_key][0]
    return ["--style", menu_style] + (["--role", role] if role else [])

HIRES_NATIVE_MENU = {(4079, item) for item in [*range(60, 78), 80]}

ABILITY_QUAD_MINIMUM = {(4079, 69): 144, (4079, 71): 156, (4079, 75): 132}

def menu_label_key(style: str, text: str, role: str | None = None) -> tuple:

    return style, role, text

def sound_menu_rows(mapping: dict, item: int) -> list[dict]:

    rows = {}
    for source in mapping.get("faces", {}).get("sound", {}).get("rows", []):
        if source.get("base") == [4069, item, 0] and "box" not in source:
            rows.update({k: {"text": text, "at": [3, 31 + 42 * k]}
                         for k, text in enumerate(source["texts"]) if text is not None})

    for k in range(70 if item == 25 else 35):
        record = reference(f"4069.{item}", k)
        if record and record.get("pc_text"):
            rows[k] = {"text": words.resolved(record["pc_text"]),
                       "at": [56 if item == 25 else 3, (32 if item == 25 else 31) + 42 * k]}
    for edit in mapping.get("textures", []):
        if (edit["entry"], edit.get("item")) == (4069, item):
            for piece in edit.get("draw", []):
                if piece.get("face") == "sound":
                    rows[piece["at"][1] // 42] = {"text": piece["text"], "at": piece["at"]}
    return [{"row": k, **row} for k, row in sorted(rows.items())]

def native_menu_text(entry: int, item: int, texture: int = 0, donors: dict | None = None) -> str:

    text = (donors or {}).get("menu_native_texts", {}).get((entry, item, texture))
    record = reference(f"{entry}.{item}", 0)
    text = text or words.resolved((record or {}).get("pc_text"))
    if not text:
        raise ValueError(f"native menu label {entry}.{item}.{texture} has no PC wording")
    return text

def native_menu_role(entry: int, item: int) -> str:

    role = (reference(f"{entry}.{item}", 0) or {}).get("role_variant")
    if role not in {"blue", "pink", "yellow"}:
        raise ValueError(f"native ability {entry}.{item} has no confirmed colour role")
    return role

def native_menu_label(donors: dict, style: str, text: str, role: str | None = None) -> tuple:

    key = menu_label_key(style, text, role)
    if key not in donors.get("menu_labels", {}):
        raise ValueError(f"native menu label was not prepared: {key}")
    return donors["menu_labels"][key]

def squished_native_label(donors: dict, style: str, text: str, role=None,
                          *, entry: int, item: int, row: int = 0, room: float,
                          scale: int = 1, label=None, metrics=None, wo2_x360: bool = True) -> tuple:
    """Condense a native label to the width its X360 counterpart fills, or (wo2_x360=False or no
    counterpart) only as far as it overflows `room`."""
    if label is None:
        label, metrics = native_menu_label(donors, style, text, role)
    if not donors.get("squish"):
        return label, metrics
    original = (reference(f"{entry}.{item}", row) or {}).get("original", {})
    geometry = None
    if wo2_x360 and original.get("donor") == "wo2-x360" and original.get("ens"):
        ens, member, texture = original["ens"]
        source = rgba(donors[(WO2_X360.name, ens)][member].textures[texture])
        source = source.crop(original.get("crop"))
        geometry = row_geometry(source, lambda r, g, b, a:
                                a / 255 * max(0, (max(r, g, b) - 100) / 140))
    if geometry is None:
        box = label.getbbox()
        natural = (metrics.get("fill") or metrics["ink"][::2])
        width = natural[1] - natural[0]
        target = width * min(1.0, room * scale / (box[2] - box[0]))
        geometry = (0, target / scale, 0)
    return lettering.squeezed(label, metrics, geometry, scale, True)


def native_reference_entries(mapping: dict) -> set[int]:
    keys = {(4079, item) for item in [*range(60, 78), 80]}
    for spec in mapping["stage_names"]["strips"]:
        keys |= {(spec["entry"], spec["item"] + k) for k in range(spec["count"])}
    keys |= {(4069, item) for item in (23, 24, 25)}
    from wozmod.fonts.menu_references import load_catalog
    return {record["original"]["ens"][0] for entry, item in keys
            for record in load_catalog().get(f"{entry}.{item}", {}).get("rows", {}).values()
            if record.get("original", {}).get("donor") == "wo2-x360"
            and record["original"].get("ens")}

def draw_native_menu_label(image: Image.Image, label: Image.Image, metrics: dict,
                           left: float, baseline: int,
                           sampled_window: tuple[int, int, int, int] | None = None) -> None:

    x, y = round(left - metrics["ink"][0]), baseline - metrics["baseline"]
    box = label.getbbox()
    if box is None:
        raise ValueError("native menu label has no visible pixels")
    bounds = tuple(value + offset for value, offset in zip(box, (x, y, x, y), strict=True))
    window = sampled_window or (0, 0, image.width, image.height)
    if (bounds[0] < max(0, window[0]) or bounds[1] < max(0, window[1])
            or bounds[2] > min(image.width, window[2])
            or bounds[3] > min(image.height, window[3])):
        raise ValueError(f"native menu alpha bounds {bounds} do not fit sampled window {window} "
                         f"inside {image.width}x{image.height}")
    image.alpha_composite(label, (x, y))

def native_stage_left(label: Image.Image, metrics: dict) -> int:

    left = round(2 - metrics["ink"][0]) + label.getbbox()[0]
    return 2 + max(0, -left)

def native_stage_baseline(label: Image.Image, metrics: dict) -> int:

    top = BASELINE - metrics["baseline"] + label.getbbox()[1]
    return BASELINE + max(0, -top)

def native_menu_layout(edit: dict, donors: dict) -> dict | None:

    key = edit.get("entry"), edit.get("item")
    if key[0] == 4079 and (60 <= (key[1] or 0) < 78 or key[1] == 80):
        text = native_menu_text(*key, edit.get("texture", 0), donors)
        ability = key[1] != 80
        style = "rounded.ability" if ability else "rounded.base"
        role = native_menu_role(*key) if ability else None
        label, metrics = squished_native_label(donors, style, text, role, entry=key[0], item=key[1],
                                               room=112 if ability else 140)
        source = source_image(edit, donors, None)
        geometry = row_geometry(source, lambda r, g, b, a: a / 255 * max(0, (max(r, g, b) - 100) / 140))
        if geometry is None:
            raise ValueError(f"native menu source {key} has no measurable fill")
        left, baseline = geometry[0], geometry[2]
        window = [0, 0, 112 if ability else 140, 35 if ability else 31]
        x, y = round(left - metrics["ink"][0]), baseline - metrics["baseline"]
    else:
        return None
    return {"style": style, "role": role, "text": text, "left": left, "baseline": baseline,
            "x": x, "y": y, "sampled_window": window,
            "alpha_bbox": [value + offset for value, offset in zip(label.getbbox(), (x, y, x, y), strict=True)],
            "metrics": metrics, "label": label}

def native_menu_payload(target, edit: dict, donors: dict) -> bytes | None:

    key = edit.get("entry"), edit.get("item")
    if "stage_name" in edit:
        width, height = (432, 42) if key[0] == 4108 else (440, 35)
        # MOZ strips: a stage name condenses only where it overflows its own strip.
        label, metrics = squished_native_label(donors, "stage.stage", edit["stage_name"],
                                               entry=key[0], item=key[1], room=width - 2, wo2_x360=False)
        image = Image.new("RGBA", (target.width, target.height))
        draw_native_menu_label(image, label, metrics, native_stage_left(label, metrics),
                               native_stage_baseline(label, metrics), (0, 0, width, height))
        return target.encode_rgba(image.tobytes())
    if key[0] == 4069 and key[1] in (23, 24, 25):
        image = rgba(target)
        rows = donors["menu_sound_rows"][key[1]]
        for row in rows:
            
            left = 53 if key[1] == 25 else 0
            top = row["row"] * 42
            image = restriped(image, (left, top, image.width, top + 42), image.width - 1)
            label, metrics = squished_native_label(donors, "sound.sound", row["text"],
                                                   entry=key[0], item=key[1], row=row["row"],
                                                   room=(614 if key[1] == 25 else 600) - row["at"][0])
            draw_native_menu_label(image, label, metrics, row["at"][0], row["at"][1])
        return target.encode_rgba(image.tobytes())
    layout = native_menu_layout(edit, donors)
    if layout:
        label, metrics = layout["label"], layout["metrics"]
        image = Image.new("RGBA", (target.width, target.height))
        draw_native_menu_label(image, label, metrics, layout["left"], layout["baseline"])
        return target.encode_rgba(image.tobytes())
    return None

def rekeyed(g1t: G1tFile) -> G1tFile:

    data = g1t.encode()
    return parse_g1t(data[:4] + struct.pack("<I", zlib.crc32(data)) + data[8:])

HIRES = [(4069, 2, 512, 1596, 42, 19), (4079, 43, 512, 256, 35, 0), (4085, 16, 512, 256, 35, 0),
         
         (4060, 14, 512, 630, 42, 0), (4064, 5, 512, 420, 42, 0), (4080, 0, 256, 462, 42, 0),
         (4080, 44, 512, 210, 42, 0), (4080, 45, 512, 210, 42, 0), (4081, 0, 512, 168, 42, 0),
         (4079, 223, 256, 64, 64, 0), (4098, 14, 256, 128, 64, 0),
         (4060, 44, 256, 64, 64, 0), (4060, 45, 256, 64, 64, 0),
         
         (4060, 33, 1024, 1024, 56, 0), (4064, 4, 1024, 392, 56, 0), (4069, 26, 512, 512, 56, 0),
         (4080, 38, 1024, 1024, 56, 0), (4079, 221, 512, 64, 56, 0), (4079, 222, 512, 64, 56, 0),
         (4098, 10, 512, 64, 56, 0), (4094, 5, 512, 64, 56, 0), (4081, 18, 512, 224, 56, 0),
         (4082, 31, 512, 64, 56, 0), (4077, 168, 1024, 512, 56, 0), (4061, 12, 1024, 256, 56, 0),
         (4061, 13, 1024, 64, 56, 0), (4061, 14, 1024, 64, 56, 0), (4061, 15, 1024, 64, 56, 0),
         (4061, 16, 1024, 64, 56, 0), (4099, 17, 1024, 64, 56, 0), (4074, 0, 512, 512, 56, 0),

         (4085, 5, 1024, 756, 84, 0), (4085, 6, 1024, 756, 84, 0), (4085, 7, 1024, 756, 84, 0),
         (4085, 8, 1024, 756, 84, 0),
         
         (4069, 14, 256, 714, 42, 0), (4069, 15, 256, 714, 42, 0), (4077, 20, 256, 256, 256, 0),
         (4077, 21, 256, 256, 256, 0), (4077, 22, 256, 256, 256, 0), (4094, 6, 256, 256, 256, 0),
         (4094, 7, 256, 256, 256, 0), (4094, 8, 256, 256, 256, 0), (4079, 20, 128, 128, 128, 0),
         (4079, 21, 128, 128, 128, 0), (4080, 40, 128, 84, 42, 0),
         (4080, 41, 128, 84, 42, 0)]

HEADER = {h[:2] for h in HIRES[13:31]}
HEADER_RASTER = "wozmod.fonts.headerraster"

COMPACT_HEADER = {(4060, 33, 3): ("CHARACTER GROWTH", 0.774252, 1.5)}

MAIN = {(4085, 5): "lit", (4085, 6): "dim", (4085, 7): "lit2", (4085, 8): "dim2"}
MAIN_RASTER = "wozmod.fonts.mainraster"
MAIN_PITCH, MAIN_STREAK_END, MAIN_EDGE_FADE = 84, 506, 8

HEADER_TEXTS = {(12, 0, 0): ["NEW CHARACTER", "RESULTS", "ABILITY ACQUIRED", "WEAPON ACQUIRED", "ITEM ACQUIRED",
                              "NEW SCENARIO"]}

PAGE_LABEL = {(4079, 43), (4085, 16)}
# The page labels are the first rows of the main menu (the art data's main_rows), right-aligned at these x.
PAGE_RIGHT = [256.8 + 0.15] * 5 + [256.8 + 0.54, 256.8 - 0.69]
LABEL_RASTER = "wozmod.fonts.labelraster"
HIRES_TWIN = {(4080, 45): (4080, 44)}  

VALUE = {(4069, 14): "value_dim", (4069, 15): "value_lit", (4077, 20): "choice_dim", (4077, 21): "choice_lit",
         (4077, 22): "choice_lit", (4094, 6): "choice_dim", (4094, 7): "choice_lit", (4094, 8): "choice_lit",
         (4079, 20): "confirm_dim", (4079, 21): "confirm_lit", (4080, 40): "value_dim", (4080, 41): "value_lit"}
VALUE_LEVELS = {"value_dim": (156, 49), "value_lit": (253, 49), "choice_dim": (201, 48), "choice_lit": (251, 53),
                "confirm_dim": (205, 82), "confirm_lit": (254, 82)}
VALUE_RASTER = "wozmod.fonts.valueraster"
# The Options values are lettered in the option labels' face, white art multiplied by the quad colours the
# runtime tints its own values with (runtime value_dim and value_lit: R, G, B, 0x80 = 1).
VALUE_TINT = {"value_dim": (0x4E, 0x4E, 0x4E), "value_lit": (0x7F, 0x77, 0x4E)}

DARK = {**{(4080, i): "pause" for i in range(6, 34)}, **{(4060, i): "help" for i in range(2, 14)},
        (4069, 0): "help", (4069, 1): "help", (4074, 10): "help", (4069, 19): "controls"}
DARK_PLATE = {**{(e, i): "plate" for e, items in {4069: range(27, 30), 4080: range(56, 59)}.items() for i in items},
              **{(e, i): "records" for e, items in {4077: [26], 4085: [9, 10, 11],
                 4094: [3]}.items() for i in items},
              **{(e, i): "results" for e, items in {4074: [3, 4, 5, 6], 4081: [39],
                 4077: [27, 28]}.items() for i in items},
              **{(e, i): "camp" for e, items in {4060: [23, 24, 25, 26, *range(36, 44)],
                 4064: [22, 23, 24], 4094: [4, 16], 4098: [6, 7, 11, 15], 4081: [40, 41]}.items() for i in items}}
DARK |= DARK_PLATE | {(4079, i): "attributes" for i in range(83, 97)}

# Attribute labels condense only past this room: the effect plate and the weapon card cell before its value.
ATTRIBUTE_ROOM = 96
ATTRIBUTE_QUAD_MINIMUM = {(4079, 83 + offset): width for offset, width in enumerate(
    [76, 48, 52, 72, 56, 68, 92, 52, 76, 80, 64, 80, 72, 68])}
MENU_RASTER = "wozmod.fonts.menu"
MENU_STYLE = {name: f"dark.{name}" for name in
              ("pause", "help", "controls", "plate", "records", "results", "camp", "attributes")}

HELP_QUAD_WIDTH = {(4060, i): width for i, width in
                   [(2, 548), (3, 544), (4, 392), (5, 400), (6, 476),
                    (7, 420), (8, 448), (9, 528), (10, 560), (11, 632), (13, 648)]}
HELP_QUAD_WIDTH[(4074, 10)] = 420
BUTTON_RASTER = "wozmod.fonts.buttonraster"
VECTOR_WINDOW = {(4069, 2): (101, 411), (4060, 14): (0, 288), (4064, 5): (100, 412), (4080, 0): (0, 208),
                 (4081, 0): (106, 406), (4079, 223): (0, 232), (4098, 14): (0, 256), (4060, 44): (0, 210),
                 (4060, 45): (0, 210), (4080, 44): (0, 332), (4080, 45): (0, 332),
                 (4060, 33): (0, 556), (4064, 4): (0, 612), (4069, 26): (0, 412), (4080, 38): (0, 556),
                 (4079, 221): (0, 400), (4079, 222): (0, 436), (4098, 10): (0, 412), (4094, 5): (0, 480),
                 (4081, 18): (0, 400), (4082, 31): (0, 372), (4077, 168): (0, 600), (4061, 12): (0, 600),
                 (4061, 14): (0, 600), (4061, 16): (0, 600), (4099, 17): (0, 532), (4074, 0): (0, 504)}

TRACK = {1: 1.070, 17: 1.894, 9: 0.0}  # WO1 X360 entries
TRACK_AUTHORED = 0.338  
CONDENSED, CONDENSED_MIN = 0.75, 4.0

# The rows wozmod adds to Game Settings: one sheet of 42 px bands in the stock label face (the runtime draws
# them, tinted, where the stock code draws no row). Labels first, then the values; every text is centred in the
# 310 px quad the stock labels use.
OPTION_ROWS = ["TEXT LANGUAGE", "VOICE LANGUAGE", "FIELD OF VIEW", "FRAME RATE", "MOVING CAMERA FIX", "ENGLISH",
               "JAPANESE", "60 FPS", "120 FPS", "FULLSCREEN", "WINDOWED", "BORDERLESS"]
OPTION_LABELS = 5
OPTION_W, OPTION_BAND, OPTION_ROOM = 310, 42, 300
OPTION_BASELINES = (34, 31)  # labels, and values 3 px higher as the stock values sit against the stock labels
# Every value's slot is its row's pitch less this one margin, so a word squeezed to its slot stops short of the next.
VALUE_MARGIN = 20
# The display modes step by the runtime's DISPLAY_MODE_PITCH, wide enough for each word at its natural width.
DISPLAY_MODE_PITCH = 195
OPTION_NARROW = {text: DISPLAY_MODE_PITCH - VALUE_MARGIN for text in ("FULLSCREEN", "WINDOWED", "BORDERLESS")}

# Each pause button row: the SHA-256 of the word it letters (a pairing check), then its geometry.
PAUSE_BUTTON_ROWS = {
    (4080, 0, 0): ("ae75b2880b5b5e401de30cd4b3fbbdcb8a4248f0c0018da2135cf7779a0974d5", 108.342679, 6.657321, 32, None, None),
    (4080, 0, 1): ("cf888f8318b597b573b26a108bb71f0896e998299382dfbe86493046b56a25b6", 122.068536, 4.975078, 32, None, None),
    (4080, 0, 2): ("bca41978330e868e846dfdc61d5c2fdd5b60ccba38d80acb66307c7d7a6ad80e", 126.376947, 4.660436, 32, None, None),
    (4080, 0, 3): ("0bad33e27107687ce9ed6814354915a6e42d00483e3e6c3f3e25f5a2193f9c0c", 128.468848, 5.537383, 32, None, None),
    (4080, 0, 4): ("10aae6592b39c03b0a584e048c3eee188998f69f1b7f28b924f3e83e74cdaa41", 124.012462, 5.993769, 32, None, None),
    (4080, 0, 5): ("1e367f240be1b68c21cfbed3dd0026a80008facf31d7ca1db684447384f06b19", 74.242991, 5.993769, 32, None, None),
    (4080, 0, 6): ("23e04084df125efcc954cba154e2f4c9e06e45d9a0111c8789bc7e6e723f5782", 110.210281, 5.993769, 32, None, None),
    (4080, 0, 7): ("af7300795e9ccc44c05bffd849d532ecc3c6e0339cf316552e903d6ebe3b7cff", 128.082554, 4.975078, 32, None, None),
    (4080, 0, 8): ("cb2a495822a6dd4c079ca88308e0164c183e8929dddb47373a52a03cd2ba0094", 127.049845, 4.993769, 32, None, None),
    (4080, 0, 9): ("0747c96a23dbfa586507337fc4ed4bdddb7b05833243622c4e6bb357b87adb0c", 105.344237, 5.956386, 32, None, None),
    (4080, 0, 10): ("de956cf5e135821be80428369b28aab96b246eefd7876e3b6ebdd399ef7c3e52", 103.228972, 3.79595, 32, None, None),
    (4080, 44, 0): ("41babf76aa9ae9e26135154cafb20265a64abb0d09b0a431008f9e1849da122d", 197.35514, 3.975078, 32, None, None),
    (4080, 44, 1): ("68b6931e23d871e990eca6819bfe5da40f7ad4163283fcd4b72f1806fb32eee6", 192.654206, 7.546729, 32, None, None),
    (4080, 44, 2): ("bf151de81086363eb82fce98b85d9cc4f7e550d6e4d15c6be2c04d1579df538c", 137.57944, 6.663551, 32, None, None),
    (4080, 44, 3): ("1a19b3383ce6afc33d680a4f224ac3f359559755313614834baf6bbe34ab50a9", 198.062306, 4.981308, 32, None, None),
    (4080, 44, 4): ("89dbf71048801678ca4abfbaa3ea8f7c651aae193357a3e23d68e21512cd07f5", 113.328661, 5.993769, 31, None, None),
    (4080, 45, 0): ("41babf76aa9ae9e26135154cafb20265a64abb0d09b0a431008f9e1849da122d", 197.35514, 3.975078, 32, None, None),
    (4080, 45, 1): ("68b6931e23d871e990eca6819bfe5da40f7ad4163283fcd4b72f1806fb32eee6", 192.654206, 7.546729, 32, None, None),
    (4080, 45, 2): ("bf151de81086363eb82fce98b85d9cc4f7e550d6e4d15c6be2c04d1579df538c", 137.57944, 6.663551, 32, None, None),
    (4080, 45, 3): ("1a19b3383ce6afc33d680a4f224ac3f359559755313614834baf6bbe34ab50a9", 198.062306, 4.981308, 32, None, None),
    (4080, 45, 4): ("89dbf71048801678ca4abfbaa3ea8f7c651aae193357a3e23d68e21512cd07f5", 113.328661, 5.993769, 31, None, None),

    (4081, 0, 0): ("0747c96a23dbfa586507337fc4ed4bdddb7b05833243622c4e6bb357b87adb0c", 105.095015, 165.962617, 32, None, None),
    (4081, 0, 1): ("0ce770318c438ba39fa6bab8cb1ada4861faa15ee9df6449680aef202a6062ed", 105.132399, 165.904984, 32, None, None),
    (4081, 0, 2): ("fd0823c6c01954d2b8939e58a38d804150300694d1229aa32105cf68bced80c3", 182.580996, 164.718069, 32, None, None),
    (4081, 0, 3): ("cb2a495822a6dd4c079ca88308e0164c183e8929dddb47373a52a03cd2ba0094", 131.076324, 165.981308, 32, None, None),
    (4079, 223, 0): ("691954b4306f9a9bc76a99e711fe48a7bdf1ba5f1aadf79ebaeb6e426a89f39b", 199.037383, 5.0, 32, None, None),
}

STYLE = {(4069, 2): "501.3", (4060, 14): "493.9", (4060, 44): "493.9", (4060, 45): "493.9", (4064, 5): "493.9",
         (4098, 14): "493.9", (4080, 0): "508.0", (4080, 44): "508.0", (4079, 223): "508.0", (4081, 0): "508.0"}

def row_track(entry: int, item: int, piece: dict) -> float:
    return TRACK.get((piece.get(WO1_X360.name) or [None])[0], TRACK_AUTHORED)

def solved_track(text: str, natural: dict, geometry, scale: int, mean: float) -> float | None:

    x0, x1 = natural["fill"]
    track = ((geometry[1] - geometry[0]) * scale - (x1 - x0)) / (gaps(text) * scale) if gaps(text) else mean
    short = (mean - track) * gaps(text)  
    return None if short > max(CONDENSED * gaps(text), CONDENSED_MIN) else track

def page_label_art(rows: list[str], scale: int, width: int = 512, served: Image.Image | None = None,
                   squeeze: bool = False) -> Image.Image:

    art = Image.new("RGBA", (width * scale, 256 * scale))
    for i, text in enumerate(rows[:len(PAGE_RIGHT)]):
        label, metrics = lettering.header(text, scale, LABEL_RASTER)
        geometry = row_geometry(served.crop((0, 35 * i, width, 35 * (i + 1))),
                                lambda r, g, b, a: (250 - (r + g + b) / 3) / 180
                                if a > 204 else 0) if squeeze and served is not None else None
        label, metrics = lettering.squeezed(label, metrics, geometry, scale, squeeze)
        art.paste(label,
                  (round(PAGE_RIGHT[i] * scale - metrics["ink"][2]), round((25.6 + 35 * i) * scale - metrics["baseline"])))
    return art

def button_cover(r: int, g: int, b: int, a: int = 255) -> float:

    return ((r + g + b) / 3 - 38) / 214 if a > 204 else 0.0

def header_cover(r: int, g: int, b: int, a: int = 255) -> float:

    return (r - 8) / 206 if a > 204 else 0.0

def main_cover(r: int, g: int, b: int, a: int = 255) -> float:

    return ((r + g + b) / 3 - 45) / 70 if a > 204 else 0.0

def alpha_cover(r: int, g: int, b: int, a: int) -> float:

    return a / 255

def row_geometry(row: Image.Image, cover=button_cover) -> tuple[float, float, int] | None:

    px, bottoms, profile, cover_fn = row.load(), {}, [0.0] * row.width, cover
    for y in range(row.height):
        for x in range(row.width):
            r, g, b, a = px[x, y]
            cover = min(max(cover_fn(r, g, b, a), 0.0), 1.0)
            profile[x] = max(profile[x], cover)
            if cover >= 0.5:
                bottoms[x] = y
    if not bottoms:
        return None
    vals = list(bottoms.values())  
    while True:                    
        deep = [v for v in vals if v >= max(vals) - 2]
        if len(deep) * 4 >= len(bottoms) or len(deep) == len(vals):
            return *lettering.fill_edges(profile), mode(deep) + 1
        vals = [v for v in vals if v < max(vals) - 2]

def row_offset(geometry, metrics: dict, scale: int, align: str) -> tuple[int, int]:

    left, right, baseline = geometry
    x0, x1 = metrics.get("fill") or metrics["ink"][::2]
    x = scale * (left + right) / 2 - (x0 + x1) / 2 if align == "full" else scale * left - x0
    return round(x), scale * baseline - metrics["baseline"]

def sheet_key(spec: dict) -> tuple:
    """The sheet a piece names: WO1 entries as numbered, WO2 X360 ones marked apart (their numbers overlap)."""
    return (WO2_X360.name, *spec[WO2_X360.name]) if WO2_X360.name in spec else tuple(spec[WO1_X360.name])

def donor_texts(mapping: dict) -> dict:

    texts = {sheet_key(s): s["texts"] for f in mapping["faces"].values() for s in f["rows"]
             if "texts" in s and (WO1_X360.name in s or WO2_X360.name in s)}
    labels = {tuple(s[WO1_X360.name]): s["texts"] for group in mapping.get("labels", {}).values() for s in group}
    return texts | HEADER_TEXTS | labels

def hires_texts(edit: dict, mapping: dict, donors: dict) -> list[str | None]:

    texts, out = donor_texts(mapping), []
    named = lambda p: WO1_X360.name in p or WO2_X360.name in p and "row" in p  # a donor piece, or a header band of a WO2 sheet
    for row in edit.get("rows") or [edit["draw"]]:  
        piece = next((p for p in row if "text" in p or named(p)), row[0])
        if "text" in piece:
            out.append(piece["text"])
        elif not named(piece):
            out.append(None)  
        elif "row" in piece:
            out.append(texts.get(sheet_key(piece), [None] * (piece["row"] + 1))[piece["row"]])
        else:
            entry, item, index = piece[WO1_X360.name]
            bands = ink_rows(rgba(donors[entry][item].textures[index]))
            y0, y1 = piece["box"][1], piece["box"][3]
            out.append(next(t for (top, bottom), t in zip(bands, texts[tuple(piece[WO1_X360.name])], strict=True) if y0 <= top and bottom <= y1))

    if edit.get("style") == "results":
        out = [words.resolved((reference(f"{edit['entry']}.{edit['item']}", row) or {}).get("pc_text")) or text
               for row, text in enumerate(out)]
    return out

def value_label(text: str, scale: int, look: str, *ref: str) -> tuple:
    """A value in its look's face: the Options values in the option labels' (white, tinted after any squeeze)."""
    if look in VALUE_TINT:
        return lettering.button(text, scale, TRACK_AUTHORED)
    return lettering.header(text, scale, VALUE_RASTER, "--look", look, *ref)

def value_tinted(label: Image.Image, look: str | None) -> Image.Image:
    return tinted(label, [c / 0x80 for c in VALUE_TINT[look]]) if look in VALUE_TINT else label

def value_cover(look: str):

    peak, ring = VALUE_LEVELS[look]
    return lambda r, g, b, a: (max(r, g, b) - ring) / (peak - ring) if a > 204 else 0.0

def rows_edit(entry: int, item: int, mapping: dict, served: Image.Image, **face) -> dict:

    edits = mapping["textures"]
    edit = next((e for e in edits if (e["entry"], e.get("item")) == (entry, item) or [entry, item] in e.get("also", [])), None)
    if not edit:  
        edit = next(e for e in edits if e["entry"] == entry and 0 < item - e["item"] < e.get("count", 1))
        sheet = edit[WO1_X360.name]
        edit = {**edit, WO1_X360.name: [sheet[0], sheet[1] + item - edit["item"], sheet[2]]}
    if "rows" in edit:
        rows = edit["rows"]
    elif "draw" in edit:
        rows = [[p] for p in edit["draw"] if "text" in p or WO1_X360.name in p]
    else:
        rows = [[{WO1_X360.name: edit[WO1_X360.name], "row": k}] for k in range(len(donor_texts(mapping).get(tuple(edit[WO1_X360.name]), [None])))]
    bands = [(max(top - 2, 0), min(bottom + 2, served.height)) for top, bottom in ink_rows(served)]
    if (entry, item) in DARK_PLATE or face.get("style") == "attributes":
        bands = [(0, served.height)]
    if (entry, item) in {(4069, 14), (4069, 15), (4080, 40), (4080, 41)}:
        bands = [(42 * k, 42 * (k + 1)) for k in range(len(rows))]
    if len(bands) != len(rows):
        raise ValueError(f"{entry}.{item}: {len(rows)} rows on {len(bands)} ink bands")

    return dict(edit, entry=entry, item=item,
                render_reference=(edit["entry"], edit["item"]),
                rows=rows, bands=bands, **face)

def header_edit(edit: dict, mapping: dict, served: Image.Image) -> dict:

    texts = {sheet_key(s): s["texts"] for s in mapping["faces"]["header"]["rows"]} | HEADER_TEXTS
    bands = edit.get("bands") or ink_rows(served)
    if "rows" in edit:
        rows = edit["rows"]
    elif "draw" in edit:  
        sheet, rows = {k: v for k, v in edit["draw"][0].items() if k in (WO1_X360.name, WO2_X360.name)}, []
        # the sheet's own bands, in order; a line its face leaves unread is lettered by an into piece
        lines = [k for k, text in enumerate(texts[sheet_key(sheet)]) if text is not None]
        for top, bottom in bands:
            into = next((p for p in edit["draw"] if "into" in p and p["into"][1] <= top and bottom <= p["into"][3]), None)
            rows.append([into] if into else [dict(sheet, row=lines[sum("into" not in r[0] for r in rows)])])
    else:
        rows = [[{WO1_X360.name: edit[WO1_X360.name], "row": i}] for i in range(len(texts[tuple(edit[WO1_X360.name])]))]
    return dict(edit, rows=rows, bands=bands, align="left", face="header")

def stock_split(band: Image.Image, gap: int) -> int:

    alpha = band.getchannel("A")
    on = [alpha.crop((x, 0, x + 1, band.height)).getbbox() is not None for x in range(band.width)]
    x = on.index(True)
    while any(on[x:x + gap]):
        x += 1
    return x

def help_layout(entry: int, item: int, right: float, width: float) -> tuple[float, int]:

    quad = HELP_QUAD_WIDTH.get((entry, item))
    if width + 2 <= right:
        return right, 0
    if quad is None:
        growth = 4 * math.ceil((width + 2 - right) / 4)
        return right + growth, growth
    minimum = max(quad, 4 * math.ceil((width + quad - right + 2) / 4))
    return right + minimum - quad, minimum if minimum > quad else 0

def vector_art(edit: dict, served: Image.Image, texts: list, scale: int, window=None,
               squeeze: bool = False) -> Image.Image:

    art = Image.new("RGBA", (served.width * scale, served.height * scale))
    render_entry, render_item = edit.get("render_reference", (edit["entry"], edit["item"]))
    header = edit.get("face") == "header"
    official_left = next((row_geometry(served.crop((0, top, served.width, bottom)), header_cover)[0]
                          for (top, bottom), row in zip(edit.get("bands", []), edit.get("rows", []), strict=True)
                          if (WO1_X360.name in row[0] or WO2_X360.name in row[0]) and "box" not in row[0]), None) if header else None
    help_labels = {}
    help_right = None
    if edit.get("style") == "help":
        geometries, widths = [], []
        for k, ((top, bottom), text) in enumerate(zip(edit.get("bands") or [(0, served.height)], texts, strict=True)):
            geometry = row_geometry(served.crop((0, top, served.width, bottom)), alpha_cover)
            if not text or geometry is None:
                continue
            piece = (edit.get("rows") or [edit["draw"]])[k][0]
            options = menu_render_options(render_entry, render_item, k, piece, "help",
                                          destination=(edit["entry"], edit["item"]))
            label, metrics = lettering.header(text, scale, MENU_RASTER, *options)
            label, metrics = lettering.squeezed(label, metrics, geometry, scale, squeeze)
            help_labels[k] = label, metrics
            geometries.append(geometry)
            widths.append((metrics["ink"][2] - metrics["ink"][0]) / scale)
        if widths:
            help_right, minimum = help_layout(edit["entry"], edit["item"],
                                              max(g[1] for g in geometries), max(widths))
            art.info["minimum"] = minimum
            if (edit["entry"], edit["item"]) not in HELP_QUAD_WIDTH and minimum:
                art.info["right_growth"] = minimum
                wider = Image.new("RGBA", ((served.width + minimum) * scale, art.height))
                wider.info.update(art.info)
                art = wider
    for k, ((top, bottom), text) in enumerate(zip(edit.get("bands") or [(0, served.height)], texts, strict=True)):
        band_1x = served.crop((0, top, served.width, bottom))
        if text is None:  
            art.paste(band_1x.resize((band_1x.width * scale, band_1x.height * scale), Image.Resampling.NEAREST),
                      (0, top * scale))
            continue
        row = (edit.get("rows") or [edit["draw"]])[k]
        piece, split = row[0], 0
        if header and WO1_X360.name not in piece and WO2_X360.name not in piece and "text" not in piece:
            split = stock_split(band_1x, edit.get("gap", 9))
            art.paste(band_1x.crop((0, 0, split, band_1x.height)).resize((split * scale, band_1x.height * scale),
                                                                          Image.Resampling.NEAREST), (0, top * scale))
        look, style = edit.get("look"), edit.get("style")
        geometry = row_geometry(band_1x.crop((split, 0, band_1x.width, band_1x.height)),
                                header_cover if header else value_cover(look) if look else alpha_cover if style else button_cover)
        if "slots" in edit and geometry:  # a value keeps its width, squeezed only past its slot, centred in it
            left, right = edit["slots"][k]
            geometry = (left + VALUE_MARGIN / 2, right - VALUE_MARGIN / 2, geometry[2])
        if not text or not geometry:
            continue
        geometry = (geometry[0] + split, geometry[1] + split, geometry[2])
        if (edit["entry"], edit["item"]) in TIME_LABEL_LAYOUT:
            _, left, baseline = TIME_LABEL_LAYOUT[(edit["entry"], edit["item"])]
            geometry = (left, left, baseline - top)
        ref = ["--ref", "{}.{}".format(*piece[WO1_X360.name][:2])] if WO1_X360.name in piece else []
        if look or style:  
            if look:
                label, metrics = value_label(text, scale, look, *ref)
            else:
                options = menu_render_options(render_entry, render_item, k, piece, style,
                                              destination=(edit["entry"], edit["item"]))
                label, metrics = help_labels[k] if k in help_labels else lettering.header(
                    text, scale, MENU_RASTER, *options)
            fit = (geometry[0], geometry[0] + ATTRIBUTE_ROOM, geometry[2]) if style == "attributes" else geometry
            label, metrics = lettering.squeezed(label, metrics, fit, scale, squeeze)
            if "tint" in edit:
                label = tinted(label, edit["tint"])
            label = value_tinted(label, look)
            if look in VALUE_TINT:  # on the baseline the runtime's own values keep
                geometry = (geometry[0], geometry[1], OPTION_BASELINES[1])
            if style == "attributes":

                geometry = (geometry[0], geometry[1], 23)
            x, y = row_offset(geometry, metrics, scale, "full" if look else "left")
            if (edit["entry"], edit["item"]) in TIME_LABEL_LAYOUT:
                y = round(y)
            if style == "help":
                x = round(help_right * scale - metrics["ink"][2])
            if x + metrics["ink"][0] < 0 or x + metrics["ink"][2] > art.width:
                raise ValueError(f"{edit['entry']}.{edit['item']} row {k} {text}: full-width ink "
                                 f"{x / scale:.1f}-{(x + metrics['ink'][2]) / scale:.1f} "
                                 f"leaves the {served.width}-wide texture")
            band = Image.new("RGBA", (art.width, (bottom - top) * scale))
            band.paste(label, (x, y))
            art.alpha_composite(band, (0, top * scale))
            continue
        if header:
            if "text" in piece and official_left is not None:  
                geometry = (official_left, geometry[1], geometry[2])
            compact = COMPACT_HEADER.get((edit["entry"], edit["item"], k))
            if compact:
                if text != compact[0]:
                    raise ValueError("compact Camp title no longer resolves to CHARACTER GROWTH")
                ref += ["--hscale", str(compact[1])]
            label, metrics = lettering.header(text, scale, HEADER_RASTER, *ref)
            label, metrics = lettering.squeezed(label, metrics, geometry, scale, squeeze)
            x, y = row_offset(geometry, metrics, scale, "left")
            if compact:
                x = round(compact[2] * scale) - label.getbbox()[0]
            if window and x + metrics["ink"][2] > window[1] * scale:
                print(f"{edit['entry']}.{edit['item']} row {k} {text}: ink to {(x + metrics['ink'][2]) / scale:.1f} "
                      f"leaves the quad window {window[1]}", file=sys.stderr)
            band = Image.new("RGBA", (art.width, (bottom - top) * scale))
            band.paste(label, (x, y))
            art.alpha_composite(band, (0, top * scale))
            continue
        pause = PAUSE_BUTTON_ROWS.get((edit["entry"], edit["item"], k))
        if pause:
            expected, target, left, baseline, centre, counterpart = pause
            if words.sha256(text) != expected:
                raise ValueError(f"pause row pairing changed: {edit['entry']}.{edit['item']} row {k}: {text!r}")
            label, metrics = lettering.pause(text, scale, target, counterpart)
            label, metrics = lettering.squeezed(label, metrics, (left, left + target, baseline), scale, squeeze)
            width = target if counterpart is None else (metrics["fill"][1] - metrics["fill"][0]) / scale
            placement = ((centre - width / 2, centre + width / 2, baseline)
                         if centre is not None else (left, left + width, baseline))
            x, y = row_offset(placement, metrics, scale, "full" if centre is not None else "left")
            if (edit["entry"], edit["item"]) in {(4080, 44), (4080, 45)}:
                x = max(x, 0)  
            band = Image.new("RGBA", (art.width, (bottom - top) * scale))
            band.paste(label, (x, y))
            art.paste(band, (0, top * scale))
            continue
        track = row_track(edit["entry"], edit["item"], piece)
        if WO1_X360.name in piece:  
            label, metrics = lettering.button(text, scale, 0.0, STYLE.get((edit["entry"], edit["item"]), "501.3"))
            solved = solved_track(text, metrics, geometry, scale, track)
            if solved is None:
                print(f"{edit['entry']}.{edit['item']} row {k} {text}: condensed in the official art, "
                      f"set at the donor's tracking {track}", file=sys.stderr)
            track = track if solved is None else solved
        label, metrics = lettering.button(text, scale, track, STYLE.get((edit["entry"], edit["item"]), "501.3"))
        quad = piece.get("window") or window
        if quad and squeeze:  # buttonraster's ink is the glyphs' box; the drawn outline and glow, squeezed with them, stay in the quad
            fill, drawn = metrics.get("fill") or metrics["ink"][::2], metrics["drawn"]
            factor = min(1.0, (quad[1] - quad[0]) * scale / (drawn[2] - drawn[0]))
            geometry = (max(geometry[0], quad[0] + (fill[0] - drawn[0]) * factor / scale),
                        min(geometry[1], quad[1] - (drawn[2] - fill[1]) * factor / scale), geometry[2])
        label, metrics = lettering.squeezed(label, metrics, geometry, scale, squeeze)
        x, y = row_offset(geometry, metrics, scale, edit.get("align", "full"))
        if (edit["entry"], edit["item"]) in {(4080, 44), (4080, 45)}:

            x = max(x, 0)
        drawn = [round(x + v) for v in (metrics.get("drawn") or metrics["ink"])[::2]]  # the squeezed edges, in pixels
        if quad and (drawn[0] < quad[0] * scale or drawn[1] > quad[1] * scale):
            print(f"{edit['entry']}.{edit['item']} row {k} {text}: ink {drawn[0]}-{drawn[1]} "
                  f"leaves the quad window "
                  f"{quad[0] * scale}-{quad[1] * scale}", file=sys.stderr)
        band = Image.new("RGBA", (art.width, (bottom - top) * scale))
        band.paste(label, (x, y))  
        art.paste(band, (0, top * scale))
    return art

def main_art(rows: list[str], served: Image.Image, scale: int, style: str, squeeze: bool = False) -> Image.Image:

    art = Image.new("RGBA", (served.width * scale, served.height * scale))
    bands = [(k * MAIN_PITCH, (k + 1) * MAIN_PITCH) for k in range(len(rows))]
    geometry = [row_geometry(served.crop((0, top, served.width, bottom)), main_cover) for top, bottom in bands]
    official_geometry = geometry.copy()
    geometry[-1] = geometry[-2]  
    for k, ((top, bottom), text, (left, right, baseline)) in enumerate(zip(bands, rows, geometry, strict=True)):
        label, metrics = lettering.header(text, scale, MAIN_RASTER, "--style", style, "--room", str(MAIN_STREAK_END - left))
        label, metrics = lettering.squeezed(label, metrics, official_geometry[k], scale, squeeze)
        band = Image.new("RGBA", (art.width, MAIN_PITCH * scale))
        band.paste(label, row_offset((left, right, baseline), metrics, scale, "left"))
        art.alpha_composite(band, (0, top * scale))
    fade = Image.new("L", art.size, 255)  
    for x in range(MAIN_EDGE_FADE * scale):
        fade.paste(round(255 * (x + 0.5) / (MAIN_EDGE_FADE * scale)), (x, 0, x + 1, art.height))
    art.putalpha(ImageChops.multiply(art.getchannel("A"), fade))
    return art

def option_rows_art(scale: int, squeeze: bool) -> Image.Image:
    """The sheet of the added Game Settings rows, one OPTION_ROWS text per band, white for the runtime to tint."""
    art = Image.new("RGBA", (OPTION_W * scale, len(OPTION_ROWS) * OPTION_BAND * scale))
    for k, text in enumerate(OPTION_ROWS):
        room = OPTION_NARROW.get(text, OPTION_ROOM)
        geometry = ((OPTION_W - room) / 2, (OPTION_W + room) / 2, OPTION_BASELINES[k >= OPTION_LABELS])
        label, metrics = lettering.button(text, scale, TRACK_AUTHORED)
        label, metrics = lettering.squeezed(label, metrics, geometry, scale, squeeze)
        x, y = row_offset(geometry, metrics, scale, "full")
        band = Image.new("RGBA", (art.width, OPTION_BAND * scale))
        band.paste(label, (x, y))
        art.paste(band, (0, k * OPTION_BAND * scale))
    return art

def plate_cover(r: int, g: int, b: int, a: int) -> float:

    return (220 - (r + g + b) / 3) / 180 if a > 16 else 0.0

def native_plate(g1t: G1tFile, image: Image.Image) -> G1tFile:

    if len(g1t.textures) != 1 or g1t.textures[0].mipmap_count != 1:
        raise ValueError("a native plate needs one single-mip texture")
    texture = g1t.textures[0]
    if image.size != (texture.width, texture.height):
        raise ValueError("native plate pixels must keep their original dimensions")
    header = G1T_TEXTURE_HEADER.pack(0x10, 1,
                                    (texture.width.bit_length() - 1) | (texture.height.bit_length() - 1) << 4,
                                    texture.flags)
    changed = bytearray(g1t.source[:texture.offset] + header + image.tobytes("raw", "BGRA"))
    struct.pack_into("<I", changed, 8, len(changed))
    return parse_g1t(bytes(changed))

def plate_lettering(served: Image.Image, badge: bool = False) -> tuple[Image.Image, Image.Image, tuple]:

    cover = (lambda r, g, b, a: min(r, g, b) / 255 if a > 204 else 0) if badge else plate_cover
    fill = Image.new("L", served.size)
    fill.putdata([255 if round(255 * min(max(cover(*p), 0), 1)) >= 64 else 0
                  for p in served.get_flattened_data()])
    box = fill.getbbox()
    box = (max(0, box[0] - 1), max(0, box[1] - 1),
           min(served.width, box[2] + 1), min(served.height, box[3] + 1))
    
    near = fill.filter(ImageFilter.MaxFilter(7))
    stripe, mask = Image.new("RGBA", (1, served.height)), Image.new("L", served.size)
    ImageDraw.Draw(mask).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
    for y in range(served.height):
        row = [served.getpixel((x, y)) for x in range(served.width)]
        edge = max((x for x, p in enumerate(row) if p[3] > 16), default=0)

        background = [p for x, p in enumerate(row) if 0 < x < edge - 1 and not near.getpixel((x, y))
                      and (p[3] > 204 and min(p[:3]) < 64 and max(p[:3]) - min(p[:3]) > 30
                           if badge else 16 < p[3] < 250 and min(p[:3]) > 170)]
        body = mode(background) if background else (0, 0, 0, 0)
        stripe.putpixel((0, y), body)
        for x, p in enumerate(row):
            if not (x > 0 and near.getpixel((x, y)) and p[3] > 16 and p != body):
                continue

            ring = (min(p[:3]) > min(body[:3]) + 8 if badge
                    else x < edge - 1 and p[3] >= body[3] - 3)
            if ring:
                mask.putpixel((x, y), 255)
        if not badge:

            for x in range(served.width):
                if (y == 0 or x == 0 or x >= edge - 1 or row[x][3] < body[3] - 3
                        or x == 1 and row[x][3] < 250):
                    mask.putpixel((x, y), 0)
    return stripe, mask, box

def plate_art(entry: int, item: int, g1t, edit: dict, text: str, scale: int,
              squeeze: bool = False, donors: dict | None = None) -> tuple:

    texture, style = g1t.textures[0], DARK_PLATE[(entry, item)]
    served = rgba(texture)
    spec = X360_PLATES.get((entry, item)) if donors is not None else None
    if spec:
        ens, member, index = spec.reference
        served = rgba(donors[(WO2_X360.name, ens)][member].textures[index])
    badge = (entry, item) == (4094, 16)
    cover = (lambda r, g, b, a: min(r, g, b) / 255 if a > 204 else 0) if badge else plate_cover
    geometry = tuple(edit["geometry"]) if "geometry" in edit else row_geometry(served, cover)  # a recipe's own room
    if (entry, item) in TIME_LABEL_LAYOUT:
        _, left, baseline = TIME_LABEL_LAYOUT[(entry, item)]
        geometry = (left, left, baseline)
    piece = edit["rows"][0][0]
    options = menu_render_options(entry, item, 0, piece, style)
    label, metrics = lettering.header(text, scale, MENU_RASTER, *options)
    label, metrics = lettering.squeezed(label, metrics, geometry, scale, squeeze)
    if badge:
        alpha = label.getchannel("A")
        label = Image.new("RGBA", label.size, (255, 255, 255, 0))
        label.putalpha(alpha)
    x, y = row_offset(geometry, metrics, scale, "left")
    if (entry, item) in TIME_LABEL_LAYOUT:
        y = round(y)
    right = (x + label.width) / scale
    old_right = ink(served)[2]
    width = max(old_right, 4 * math.ceil((right + (32 if spec else 29)) / 4))
    if badge:
        width = max(old_right, 4 * math.ceil((right + 12) / 4))
    if spec:
        canvas_width = 1 << (max(texture.width, served.width, width) - 1).bit_length()
        background = None
        if getattr(spec, "cleanup_reference", None):
            ens, member, index = spec.cleanup_reference
            background = rgba(donors[(WO2_X360.name, ens)][member].textures[index])
        clean, mask, cleanup = clean_substrate(
            served, canvas_size=(canvas_width, served.height), minimum_right=width,
            background_reference=background, left_bevel=getattr(spec, "left_bevel", 2),
            restore_fade=getattr(spec, "restore_fade", False),
        )
        if (texture.width, texture.height) != clean.size:
            g1t = g1t.with_texture_resized(0, *clean.size, bytes(4 * canvas_width * served.height))
        native = native_plate(g1t, clean)
        overlay = Image.new("RGBA", (canvas_width * scale, served.height * scale))
        if x + metrics["ink"][0] < 0 or x + metrics["ink"][2] > overlay.width:
            raise ValueError(f"{entry}.{item}: full-width vector leaves its X360 plate")
        overlay.paste(label, (x, y))
        overlay.info["plate"] = dict(reference=list(spec.reference), role=spec.role,
                                    text=text, **cleanup)
        return native, overlay, width
    if width > served.width:
        raise ValueError(f"{entry}.{item}: full-width plate needs a {width}-wide texture")
    stripe, mask, _ = plate_lettering(served, badge)
    clean = served.copy()
    clean.paste(stripe.resize(served.size, Image.Resampling.NEAREST), (0, 0), mask)
    if width > old_right:
        end = 12 if badge else 29
        slant = clean.crop((old_right - end, 0, old_right, served.height))
        clean.paste(stripe.resize((width - old_right, served.height), Image.Resampling.NEAREST),
                    (old_right - end, 0))
        clean.paste(slant, (width - end, 0))
    g1t = native_plate(g1t, clean)
    overlay = Image.new("RGBA", (served.width * scale, served.height * scale))
    if x + metrics["ink"][0] < 0 or x + metrics["ink"][2] > overlay.width:
        raise ValueError(f"{entry}.{item}: full-width vector leaves its plate texture")
    overlay.paste(label, (x, y))
    return g1t, overlay, width

def native_menu_art(entry: int, item: int, served: Image.Image, mapping: dict,
                    donors: dict, scale: int, squeeze: bool = False) -> Image.Image:

    edit = next((e for e in mapping["textures"]
                 if (e["entry"], e.get("item")) == (entry, item)
                 or [entry, item] in e.get("also", [])), None)
    if edit is None:
        edit = next(e for e in mapping["textures"] if e["entry"] == entry
                    and 0 < item - e["item"] < e.get("count", 1))
        sheet = edit[WO1_X360.name]
        edit = {**edit, WO1_X360.name: [sheet[0], sheet[1] + item - edit["item"], 0]}
    edit = dict(edit, entry=entry, item=item)
    layout = native_menu_layout(edit, donors)
    options = ["--style", layout["style"]]
    if layout["role"]:
        options += ["--role", layout["role"]]
    label, metrics = lettering.header(layout["text"], scale, MENU_RASTER, *options)
    if squeeze:
        label, metrics = squished_native_label(dict(donors, squish=True), layout["style"], layout["text"],
                                               layout["role"], entry=entry, item=item,
                                               room=layout["sampled_window"][2], scale=scale,
                                               label=label, metrics=metrics)
    art = Image.new("RGBA", (served.width * scale, served.height * scale))
    x = round(layout["left"] * scale - metrics["ink"][0])
    y = round(layout["baseline"] * scale - metrics["baseline"])
    box = label.getbbox()
    sampled_height = layout["sampled_window"][3] * scale

    vertical_shift = max(0, y + box[3] - sampled_height) if box else 0
    y -= vertical_shift
    # Our outline can be wider than the room the donor leaves before its fill: move right until it fits, never
    # past the fill itself.
    horizontal_shift = max(0, -(x + box[0])) if box else 0
    x += horizontal_shift
    if box and (horizontal_shift > metrics["ink"][0] - box[0] or y + box[1] < 0 or y + box[3] > sampled_height):
        raise ValueError(f"{entry}.{item}: complete native overlay leaves its sampled crop")
    art.alpha_composite(label, (x, y))

    box = art.getbbox()

    fits = not box or box[2] <= layout["sampled_window"][2] * scale
    natural_minimum = 0 if fits else 4 * math.ceil((box[2] / scale + 2) / 4)
    art.info["minimum"] = max(natural_minimum, ABILITY_QUAD_MINIMUM.get((entry, item), 0))
    art.info["native_layout"] = dict({k: v for k, v in layout.items() if k != "label"}, hires_placement={
        "scale": scale, "x": x, "y": y, "vertical_shift": vertical_shift, "horizontal_shift": horizontal_shift,
        "sampled_height": sampled_height, "alpha_bbox": list(box) if box else None,
    })
    return art

def _now(function, *args) -> Future:
    done = Future()
    done.set_result(function(*args))
    return done


def hires_bin(entries: Path, vector: tuple, squeeze: bool = False, jobs: int = 1,
              review: Path | None = None) -> bytes:
    """The large-menu sheets. With the native codecs (they let go of the GIL) the texture encoding and the
    LZP2 packing of each sheet run on `jobs` threads while the next sheet is drawn; the bytes are the same.
    vector is (the menu_textures mapping, the donors); review is the developer folder that keeps the plate sources
    and the per-sheet layout details."""
    if not (native.module and jobs > 1):
        return _hires_bin(entries, vector, squeeze, jobs, _now, review)
    with ThreadPoolExecutor(jobs) as pool:
        return _hires_bin(entries, vector, squeeze, jobs, pool.submit, review)


def _hires_bin(entries: Path, vector: tuple, squeeze: bool, jobs: int, later, review: Path | None) -> bytes:
    rows, packages, parsed = [], {}, {}
    plate_sources = review and review / "vector-plate-sources"
    if review:
        plate_sources.mkdir(parents=True, exist_ok=True)
    extras = list(DARK) + sorted(HIRES_NATIVE_MENU)
    plates = HIRES + [(e, i, 0, 0, 0, 0) for e, i in extras]
    for number, (entry, item, w, h, band, per_col) in enumerate(plates):
        scale = HIRES_SCALE
        report_progress("art", "drawing the large menu plates", 0.65 + 0.3 * number / len(plates),
               (number, len(plates)))
        if entry not in parsed:  # decode each entry once, however many of its items are used
            data = (entries / f"{entry}.bin").read_bytes()
            parsed[entry] = parse_texture_package(parse_lzp2(data).decoded if data[:4] == b"LZP2" else data)
        package = parsed[entry]
        g1t = package.items[item].g1t
        if review and (entry, item) in DARK_PLATE:
            original = plate_sources / f"{entry}.{item}.g1t"
            if not original.exists():
                original.write_bytes(g1t.encode())
            g1t = parse_g1t(original.read_bytes())
        served = rgba(g1t.textures[0])
        if not w:  
            w, h, band = served.width, served.height, served.height
        if served.width != w or served.height < h:
            raise ValueError(f"served {entry}.{item} is {served.size}, not {w} x at least {h}")
        if (entry, item) in PAGE_LABEL:
            art = page_label_art(vector[0]["main_rows"], scale, w, served, squeeze)
        elif (entry, item) in MAIN:
            art = main_art(vector[0]["main_rows"], served, scale, MAIN[(entry, item)], squeeze)
        else:
            mapping, donors = vector
            if (entry, item) in HIRES_NATIVE_MENU:
                art = native_menu_art(entry, item, served, mapping, donors, scale, squeeze)
                if review:
                    details = review / "native-menu-layout"
                    details.mkdir(parents=True, exist_ok=True)
                    (details / f"{entry}.{item}.json").write_text(
                        json.dumps(art.info["native_layout"], indent=2) + "\n", encoding="utf-8")
                edit = None
            else:
                art = None
            edit = None if (entry, item) in HIRES_NATIVE_MENU else next(
                (e for e in mapping["textures"] if (e["entry"], e.get("item")) == HIRES_TWIN.get((entry, item), (entry, item))
                 and ("rows" in e or "draw" in e or (entry, item) in HEADER)), None)
            if (entry, item) in HIRES_NATIVE_MENU:
                pass
            elif (entry, item) in VALUE:
                edit = rows_edit(entry, item, mapping, served, look=VALUE[(entry, item)])
            elif (entry, item) in DARK:
                edit = rows_edit(entry, item, mapping, served, style=DARK[(entry, item)])
            elif not edit:  
                continue
            elif (entry, item) in HEADER:
                edit = header_edit(edit, mapping, served)
            texts = hires_texts(edit, mapping, donors) if edit else []
            if (entry, item) in HIRES_NATIVE_MENU:
                pass
            elif (entry, item) in DARK_PLATE:
                clean, art, minimum = plate_art(entry, item, g1t, edit, texts[0], scale, squeeze, donors)
                packages[entry] = packages.get(entry, package).with_g1t(item, rekeyed(clean))
                w, h = clean.textures[0].width, clean.textures[0].height
                if review and "plate" in art.info:
                    details = review / "plate-cleanup"
                    details.mkdir(parents=True, exist_ok=True)
                    (details / f"{entry}.{item}.json").write_text(json.dumps(art.info["plate"], indent=2) + "\n", encoding="utf-8")
            else:
                art = vector_art(edit, served, texts, scale, VECTOR_WINDOW.get((entry, item)), squeeze)
        growth = art.info.get("right_growth", 0)
        if growth:
            w += growth
        else:

            w = max(w, art.info.get("minimum", 0))
        art = art.crop((0, 0, w * scale, h * scale))
        overlay = (entry, item) in DARK_PLATE
        if not overlay:
            minimum = 0
        if (entry, item) in (set(DARK) - set(DARK_PLATE)) | HIRES_NATIVE_MENU:
            box = art.getbbox()
            minimum = art.info.get("minimum", 4 * math.ceil((box[2] / scale + 2) / 4) if box else 0)
            if (entry, item) in ATTRIBUTE_QUAD_MINIMUM:
                minimum = max(minimum, ATTRIBUTE_QUAD_MINIMUM[entry, item])
        rows.append((dict(entry=entry, item=item, w=w, h=h, band=band, per_col=per_col, overlay=overlay,
                          right_grow=bool(growth), minimum=minimum), later(hires_blocks, art, w, band, per_col)))
    if (4069, 2) in {plate[:2] for plate in plates}:  # entry 0: no stock texture, the runtime draws it
        rows.append((dict(entry=0, item=0, w=OPTION_W, h=len(OPTION_ROWS) * OPTION_BAND, band=OPTION_BAND, per_col=0),
                     later(hires_blocks, option_rows_art(HIRES_SCALE, squeeze), OPTION_W, OPTION_BAND, 0)))
    encoded = {entry: package.encode() for entry, package in packages.items()}
    packed = [entry for entry in encoded if (entries / f"{entry}.bin").read_bytes()[:4] == b"LZP2"]
    if jobs > 1 and len(packed) > 1 and not native.module:  # the Python encoder is slow: one entry per process
        with worker_pool(min(jobs, len(packed))) as pool:
            results = dict(zip(packed, pool.map(packed_lzp2, [encoded[entry] for entry in packed]), strict=True))
    else:
        packing = {entry: later(packed_lzp2, encoded[entry]) for entry in packed}
        results = {entry: done.result() for entry, done in packing.items()}
    for entry, data in encoded.items():
        (entries / f"{entry}.bin").write_bytes(results.get(entry, data))
    return hires_bank([HiresRow(**fields, blocks=blocks.result()) for fields, blocks in rows])
