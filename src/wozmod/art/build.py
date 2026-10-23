"""Build menu textures and font atlases from supplied archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import zlib
from concurrent.futures import as_completed
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from wozmod.formats.linkdata import (
    parse_index, parse_lzp2, wo1_entries,
)
from wozmod import resources
from wozmod.progress import report as report_progress
from . import lettering, words
from wozmod.runtime.latin_font import CELL, LATIN_GLYPH
from wozmod.donors import WO1_X360, WO2_X360, extract_donor, verify_file
from wozmod.formats.textures import (
    G1T_HEADER, G1T_TEXTURE_HEADER, PIXEL_FORMAT_BGRA8, G1tFile, parse_g1t, parse_texture_package,
)

REPO = resources.root()
from wozmod.fonts.menu_plates import X360_PLATE_ENTRIES
from wozmod.fonts.menu_references import reference, x360_members
from wozmod.fonts.glyph_outlines import from_json
from .imaging import (
    fitted, glowed, greyed, hue_shifted, ink, keyed, lettering_mask, mirrored, navy_mask, placed, plated, refilled,
    restriped, rgba, spliced, tinted, unlettered,
)
from . import packing
from .packing import packed_lzp2, worker_pool
from .faces import (
    BANNER_LINES, BANNER_SAMPLE_WIDTH, BANNER_SINGLE_CENTRE, BASELINE, CARD_CENTRE, CARD_DROP, DREAM_NAME_BASELINES,
    FILLS, faces, ink_rows, lettering_layer, nearest, source_image, stock_image, strokes, text_lines, used_faces,
)
from .vector import (
    ABILITY_QUAD_MINIMUM, DARK, HIRES, MENU_RASTER, hires_bin, menu_label_key, menu_render_options,
    native_menu_label, native_menu_layout, native_menu_payload, native_menu_role, native_menu_text, native_plate,
    native_reference_entries, native_stage_baseline, native_stage_left, rekeyed,
    sound_menu_rows, squished_native_label, value_label, value_tinted,
)
MAPPING = REPO / "data/art/menu_textures.json"
PACKAGE_MAGIC = 0x33B59
SHARED: dict = {}  

def expanded(edit: dict):
    """Each (entry, recipe) a texture recipe stands for: its count steps the item and its WO1 X360 or WO2 X360 pointer alike."""
    for entry, first in [(edit["entry"], edit.get("item")), *edit.get("also", [])]:
        for k in range(edit.get("count", 1)):
            step = dict(edit, entry=entry, item=first + k) if first is not None else dict(edit, entry=entry)
            for key in (WO1_X360.name, WO2_X360.name):
                if k and key in edit:
                    step[key] = [edit[key][0], edit[key][1] + k, edit[key][2]]
            yield entry, step

def native_officer_source(edit: dict) -> tuple[int, int, int] | None:
    """The WO2 X360 member an officer banner is served whole from, if its recipe names one."""
    return tuple(edit[WO2_X360.name]) if edit.get("entry") == 4063 and WO2_X360.name in edit else None

def officer_name_role(edit: dict, row: int = 0) -> str | None:

    lines = edit.get("card", {}).get("lines")
    if edit.get("entry") != 4095 or not lines:
        return None
    position = "single" if len(lines) == 1 else "first" if row == 0 else "second"
    return f"dream-x360-{position}"

def native_dream_card(g1t: G1tFile, edit: dict, donors: dict) -> G1tFile | None:

    spec = edit.get("card", {})
    if WO2_X360.name not in spec:
        return None
    original = (reference(f"{edit['entry']}.{edit['item']}", 0) or {}).get("original", {})
    if (edit.get("entry") != 4095 or original.get("ens") != spec[WO2_X360.name]
            or spec[WO2_X360.name][0] != 27):
        raise ValueError("native Dream card must use its paired ENS27 portrait")
    box = spec["box"]
    if (len(box) != 4 or box[0] != 0 or box[2] != 196
            or box[3] - box[1] != 196 or not 0 <= box[1] <= 70):
        raise ValueError("native Dream card needs a 196x196 window inside the 196x266 art")
    source = source_image(spec, donors, None)
    if source.width < 196 or source.height < 266:
        raise ValueError("native Dream donor cannot hold its 196x266 portrait art")
    texture = g1t.textures[edit["texture"]]
    if texture.width < 196 or texture.height < 196:
        raise ValueError("native Dream card allocation cannot hold the stock square window")
    image = Image.new("RGBA", (texture.width, texture.height))
    image.paste(source.crop(box), (0, 0))
    return native_plate(g1t, image)

def donor_g1ts(data: bytes) -> dict[int, G1tFile]:

    magic, count, table, _ = struct.unpack_from("<4I", data)
    if magic != PACKAGE_MAGIC:
        return {item.ordinal: item.g1t for item in parse_texture_package(data).items}
    items = {}
    for item in range(count):
        offset, size = struct.unpack_from("<II", data, table + 8 * item)
        member = data[offset * 16:offset * 16 + size]
        if member[:4] == b"GT1G":
            items[item] = parse_g1t(member)
    return items

# Donor art entries below WO1_PC_ENTRIES are WO1 X360 archive entries; from it on they would be WO1 PC ones, which
# release 1 no longer reads.
WO1_PC_ENTRIES = 492

def wo1_donors(stem: Path, entries: set[int]) -> dict:
    """The WO1 X360 donor art entries, read from the linkdata_ens archive at stem."""
    if pc := sorted(e for e in entries if e >= WO1_PC_ENTRIES):
        raise ValueError(f"art data names WO1 PC entries {pc}: the build reads no WO1 PC disc")
    return {k: x360_members(v) for k, v in wo1_entries(stem.with_suffix(".lnk"), stem.with_suffix(".idx"),
                                                       sorted(entries)).items()}

def wo2_g1ts(data: bytes) -> dict[int, G1tFile]:

    return x360_members(data)

def only_strokes(image: Image.Image, spec: dict) -> Image.Image:

    found = strokes(image, FILLS[spec.get("fill", "yellow")])
    start, stop = (0, spec["strokes"]) if isinstance(spec["strokes"], int) else spec["strokes"]
    keep = set(range(len(found))[start:stop])
    label, kept = nearest(image, found), Image.new("RGBA", image.size)
    for point, k in label.items():
        if k in keep:
            kept.putpixel(point, image.getpixel(point))
    return kept

def button_1x(spec: dict) -> Image.Image:
    return lettering.button(spec["text"], 1, style=spec["button"])[0]

def composed(stock: Image.Image, rows: list[list[dict]], donors: dict, align: str, gap: int,
             bands=None) -> Image.Image:
    """A row whose first piece names a window [x0, x1] (where its quad lets ink show) is centred in it
    and condensed to fit."""
    lines, bands = [], bands or ink_rows(stock)
    inks = [ink(stock.crop((0, top, stock.width, bottom))) for top, bottom in bands]
    left = min(box[0] for box in inks)
    for (top, bottom), pieces, (x0, _, x1, _) in zip(bands, rows, inks, strict=True):
        parts = []
        for spec in pieces:
            image = source_image(spec, donors, stock)
            if "face" in spec:  
                image = donors["faces"][spec["face"]].render(spec["text"])[0]
            elif "button" in spec:  # buttonvec at 1x, in the style its hi-res rows use
                image = button_1x(spec)
            elif "row" in spec:
                y0, y1 = ink_rows(image)[spec["row"]]
                image = image.crop((0, y0, image.width, y1))
            else:
                image = image.crop(spec["box"])
            if "strokes" in spec:
                image = only_strokes(image, spec)
            left_ink, _, right_ink, _ = ink(image)
            image = image.crop((left_ink, 0, right_ink, image.height))
            if "hue" in spec:
                image = hue_shifted(image, spec["hue"])
            parts.append((image, spec.get("dy", 0)))
        line = Image.new("RGBA", (sum(p.width for p, _ in parts) + gap * (len(parts) - 1),
                                 max(p.height + dy for p, dy in parts)))
        x = 0
        for part, dy in parts:
            line.alpha_composite(part, (x, dy))
            x += part.width + gap
        line = line.crop(ink(line))
        window = pieces[0].get("window")
        if window:
            x0, x1 = window
            if line.width > x1 - x0 and donors.get("squish", True):
                line = line.resize((x1 - x0, line.height), Image.Resampling.LANCZOS)
        elif align == "full":
            x0, x1 = 0, stock.width
        elif align == "left":
            x0 = left
        lines.append((line, top, bottom, x0, x1, align == "left" and not window))
    canvas = Image.new("RGBA", stock.size)
    for line, top, bottom, x0, x1, left in lines:
        placed(canvas, line, x0 if left else round((x0 + x1 - line.width) / 2), top + (bottom - top - line.height) // 2)
    return canvas

def recoloured(image: Image.Image, edit: dict) -> Image.Image:

    if "tint" in edit:
        image = tinted(image, edit["tint"])
    if "gradient" in edit:  
        stops, (_, top, _, bottom) = edit["gradient"], ink(image)
        ramp = Image.new("RGB", (1, image.height))
        for y in range(image.height):
            at = min(max(0, (y - top) / max(1, bottom - 1 - top)), 1) * (len(stops) - 1)
            k = min(int(at), len(stops) - 2)
            ramp.putpixel((0, y), tuple(round(a + (b - a) * (at - k)) for a, b in zip(stops[k], stops[k + 1], strict=True)))
        shaded = ImageChops.multiply(ramp.resize(image.size), image.convert("L").convert("RGB")).convert("RGBA")
        shaded.putalpha(image.getchannel("A"))
        image = shaded
    if "hue" in edit:
        image = hue_shifted(image, edit["hue"])
    if "duotone" in edit:  
        alpha = image.getchannel("A")
        dark, bright, *points = edit["duotone"]
        image = ImageOps.colorize(image.convert("L"), tuple(dark), tuple(bright), None, *points).convert("RGBA")
        image.putalpha(alpha)
    if "glow" in edit:  
        image = glowed(image, *edit["glow"])
    return image

def prepare_menu_labels(mapping: dict, output_dir: Path | None = None, entries: set[int] | None = None) -> dict:
    """The native menu labels, drawn in one batch: all of them, or only those the edits of entries letter."""
    requests = {}
    wanted = lambda *ordinals: entries is None or not entries.isdisjoint(ordinals)

    def add(style, text, role=None):
        requests.setdefault(menu_label_key(style, text, role),
                            {"style": style, "text": text, **({"role": role} if role else {})})

    stages = mapping.get("stage_names", {})
    if wanted(*(strips["entry"] for strips in stages.get("strips", []))):
        for text in stages.get("names", []):
            add("stage.stage", text)
    if wanted(4069):
        for item in (23, 24, 25):
            for row in sound_menu_rows(mapping, item):
                add("sound.sound", row["text"])
    if wanted(4079):
        for item in range(60, 78):
            add("rounded.ability", native_menu_text(4079, item),
                native_menu_role(4079, item))
        add("rounded.base", native_menu_text(4079, 80))
    for edit in mapping.get("textures", []):
        spec = edit.get("banner") or edit.get("card")
        if spec and "lines" in spec and wanted(*(ordinal for ordinal, _ in expanded(edit))):
            style = "banner.large" if len(spec["lines"]) == 1 else "banner.small"
            for row, text in enumerate(spec["lines"]):
                add(style, text, officer_name_role(edit, row))
    ordered = list(requests.values())
    return {menu_label_key(r["style"], r["text"], r.get("role")): label
            for r, label in zip(ordered, lettering.native(ordered, output_dir), strict=True)}

def native_menu_widths(mapping: dict, donors: dict) -> dict:

    rows = []

    def record(entry, item, row, style, text, left, baseline, window, role=None):
        label, metrics = native_menu_label(donors, style, text, role)
        x, y = round(left - metrics["ink"][0]), baseline - metrics["baseline"]
        box = [value + offset for value, offset in zip(label.getbbox(), (x, y, x, y), strict=True)]
        required_right = max(window[2], box[2])
        required_bottom = max(window[3], box[3]) if window[3] is not None else None
        rows.append({"entry": entry, "item": item, "row": row, "text": text,
                     "style": style, "role": role, "left": left, "baseline": baseline,
                     "natural_fill_width": metrics["ink"][2] - metrics["ink"][0],
                     "natural_alpha_width": label.getbbox()[2] - label.getbbox()[0],
                     "placed_alpha_bbox": box, "sampled_window": window,
                     "required_right": required_right, "required_bottom": required_bottom,
                     "left_clipping_needed": box[0] < window[0],
                     "top_clipping_needed": box[1] < window[1],
                     "widening_needed": box[2] > window[2],
                     "height_growth_needed": window[3] is not None and box[3] > window[3]})

    stages = mapping.get("stage_names", {})
    for strips in stages.get("strips", []):
        width, height = (432, 42) if strips["entry"] == 4108 else (440, 35)
        for k, text in enumerate(stages["names"][:strips["count"]]):
            label, metrics = native_menu_label(donors, "stage.stage", text)
            left, baseline = native_stage_left(label, metrics), native_stage_baseline(label, metrics)
            record(strips["entry"], strips["item"] + k, 0, "stage.stage", text,
                   left, baseline, [0, 0, width, height])
            rows[-1]["placement_delta"] = [left - 2, baseline - BASELINE]
    for item in (23, 24, 25):
        for row in sound_menu_rows(mapping, item):
            top = row["row"] * 42
            window = [54 if item == 25 else 0, top, 614 if item == 25 else 600, top + 42]
            record(4069, item, row["row"], "sound.sound", row["text"],
                   *row["at"], window)
    for edit in mapping.get("textures", []):
        for entry, step in expanded(edit):
            layout = native_menu_layout(step, donors)
            if layout:
                record(entry, step["item"], 0, layout["style"], layout["text"],
                       layout["left"], layout["baseline"], layout["sampled_window"], layout["role"])
            spec = step.get("banner") or step.get("card")
            if not spec or "lines" not in spec:
                continue
            name, baselines = BANNER_LINES[len(spec["lines"])]
            if "card" in step:
                baselines = DREAM_NAME_BASELINES[len(spec["lines"])]
            style = name.replace("_", ".")
            for row, (text, baseline) in enumerate(zip(spec["lines"], baselines, strict=True)):
                role = officer_name_role(step, row)
                label, metrics = native_menu_label(donors, style, text, role)
                box = label.getbbox()
                width = box[2] - box[0]
                if "banner" in step:
                    x = BANNER_SINGLE_CENTRE - width // 2
                    window = [0, 0, BANNER_SAMPLE_WIDTH, 112]
                else:
                    x = CARD_CENTRE - width // 2
                    window = [0, 0, 2 * CARD_CENTRE, 196]
                record(entry, step["item"], row, style, text,
                       x - box[0] + metrics["ink"][0], baseline, window, role)
    return {"schema": "moz-native-menu-widths-v1", "rows": rows,
            "widenings": [row for row in rows if row["widening_needed"] or row["height_growth_needed"]],
            "clippings": [row for row in rows if row["left_clipping_needed"] or row["top_clipping_needed"]],
            "stage_vertical_adjustments": [row for row in rows if row["style"] == "stage.stage"
                                           and row["placement_delta"][1]],
            "note": "Natural public-render fill and full alpha bounds; no row condensation. Banner windows are authored lettering boxes; other widths are the statically proven source windows. Texture growth alone does not widen a sampled quad."}

def payload(target, edit: dict, donors: dict, package) -> bytes:
    if edit.get("blank"):
        return target.encode_rgba(bytes(4 * target.width * target.height))
    native = native_menu_payload(target, edit, donors)
    if native is not None:
        return native
    if "stage_title" in edit:  
        image, top = donors["faces"]["title"].render(edit["stage_title"])
        box = image.getbbox()
        image = placed(Image.new("RGBA", (target.width, target.height)), image.crop(box), 0, edit["baseline"] - top + box[1])
        if WO1_X360.name in edit:
            entry, item, index = edit[WO1_X360.name]
            source = donors[entry][item].textures[index]
            if (ink(rgba(source))[2] >= ink(image)[2] - 2 and source.pixel_format == target.pixel_format
                    and len(source.payload) == len(target.payload)):  
                return source.payload
        return target.encode_rgba(image.tobytes())
    if "grey_of" in edit:
        return target.encode_rgba(greyed(rgba(package.items[edit["grey_of"]].g1t.textures[0])).tobytes())
    if "shadow" in edit:  
        image, spec = rgba(target), edit["shadow"]
        px, top = image.load(), spec["from"]
        for x in range(image.width):
            if all(px[x, y][3] == 255 and max(px[x, y][:3]) < 64 for y in (top - 2, top - 1)):
                for y, rgb in enumerate(spec["rgb"], top):
                    px[x, y] = (*rgb, 255)
        return target.encode_rgba(image.tobytes())
    if "banner" in edit:  
        spec, image = edit["banner"], rgba(target)
        image = refilled(image, lettering_mask(image, spec["erase"]))
        badge = spec["badge"]  
        donor, art = source_image(badge, donors, None), stock_image(badge["against"], donors)
        box, mask = badge["box"], Image.new("L", donor.size)
        mask.paste(ImageChops.difference(donor.convert("RGB"), art.convert("RGB")).convert("L").crop(box)
                   .point(lambda v: 255 if v > 16 else 0), box[:2])
        layer = Image.new("RGBA", donor.size)
        layer.paste(donor, (0, 0), mask.filter(ImageFilter.MaxFilter(3)))
        image.alpha_composite(layer)
        name, baselines = BANNER_LINES[1]  # an authored banner has one name line
        for row, (text, baseline) in enumerate(zip(spec["lines"], baselines, strict=True)):
            line, metrics = squished_native_label(donors, name.replace("_", "."), text,
                                                  entry=edit["entry"], item=edit["item"], row=row, room=172)
            top = metrics["baseline"]
            x0, y0, x1, _ = line.getbbox()
            x = BANNER_SINGLE_CENTRE - (x1 - x0) // 2
            if x < 0:
                raise ValueError(f"native banner line {text!r} starts outside its texture")
            if x + x1 - x0 > image.width:
                raise ValueError(f"native banner line {text!r} exceeds its {image.width}-pixel texture")
            image.alpha_composite(line.crop((x0, y0, x1, line.height)), (x, baseline - top + y0))
        return target.encode_rgba(image.tobytes())
    if "card" in edit:  
        spec, image = edit["card"], rgba(target)
        image = mirrored(image, navy_mask(image, spec["rows"]))
        drop = CARD_DROP
        lines = []  
        if WO1_X360.name in spec:  
            
            layer = lettering_layer(source_image(spec, donors, None), stock_image(spec["against"], donors), spec["box"])
            grey = layer.convert("L")
            layer = Image.merge("RGBA", (grey, grey, grey, layer.getchannel("A")))
            baselines = [b - spec["box"][1] for b in BANNER_LINES[2][1]]  
            lines += [(line, spec["box"][1] + top) for line, top in text_lines(layer, baselines)]
        else:  
            name = BANNER_LINES[len(spec["lines"])][0]
            baselines = DREAM_NAME_BASELINES[len(spec["lines"])]
            drop = 0
            for row, (text, baseline) in enumerate(zip(spec["lines"], baselines, strict=True)):
                line, metrics = squished_native_label(donors, name.replace("_", "."), text,
                                                      officer_name_role(edit, row),
                                                      entry=edit["entry"], item=edit["item"], row=row, room=196)
                top = metrics["baseline"]
                x0, y0, x1, y1 = line.getbbox()
                lines.append((line.crop((x0, y0, x1, y1)), baseline - top + y0))
        for line, top in lines:
            if "lines" in spec and line.width > 196:
                raise ValueError("Dream name including glow exceeds its 196-pixel square")
            image.alpha_composite(line, (CARD_CENTRE - line.width // 2, top + drop))
        return target.encode_rgba(image.tobytes())
    if "draw" in edit:  
        stock = rgba(target)
        image = Image.new("RGBA", stock.size) if edit.get("clear") else stock.copy()
        if "unlettered" in edit:  
            spec = edit["unlettered"]
            image = unlettered([rgba(donors["stock"].items[i].g1t.textures[edit["texture"]]) for i in spec["items"]],
                               spec["saturation"], spec["grow"])
        if "plate" in edit:
            spec = edit["plate"]
            image = plated(source_image(spec, donors, None), spec["width"], spec["column"], spec["right"])
            image = image.crop((0, 0, target.width, target.height))
        for spec in edit["draw"]:
            if "erase" in spec:  
                image = restriped(image, spec["erase"][:4], spec["erase"][4])
                continue
            if "refill" in spec:  
                (cx, cy, r), disk = spec["refill"], Image.new("L", image.size)
                ImageDraw.Draw(disk).ellipse((cx - r, cy - r, cx + r, cy + r), 255)
                image = refilled(image, disk)
                continue
            if "splice" in spec:  
                image = spliced(stock, **spec["splice"])
                continue
            top = 0
            mark = (lambda glyph, spec=spec: recoloured(glyph, spec["mark"])) if "mark" in spec else None
            if "parts" in spec:  
                face, (x, y) = donors["faces"][spec["face"]], spec["at"]
                for part in spec["parts"]:
                    if isinstance(part, str):
                        piece, top = face.render(part, mark)
                    else:  
                        piece, top = source_image(part, donors, stock).crop(part["box"]), part["rise"]
                        piece = keyed(piece, *part["key"]) if "key" in part else piece
                    image.alpha_composite(piece, (x, y - top))
                    x += piece.width + spec.get("gap", round(face.space))
                continue
            if "face" in spec:
                piece, top = donors["faces"][spec["face"]].render(spec["text"], mark)
            elif "button" in spec:
                piece = button_1x(spec)
            elif "look" in spec:  # the value face at 1x
                piece = value_tinted(value_label(spec["text"], 1, spec["look"])[0], spec["look"])
            elif "style" in spec:  # a menu style at 1x, as its hi-res row draws it
                options = menu_render_options(edit["entry"], edit["item"], 0, spec, spec["style"])
                piece = lettering.header(spec["text"], 1, MENU_RASTER, *options)[0]
            else:
                piece = source_image(spec, donors, stock).crop(spec.get("box"))
            if "key" in spec:
                piece = keyed(piece, *spec["key"])
            if "strokes" in spec:
                piece = only_strokes(piece, spec)
            if "drop" in spec:  
                piece.putdata([(*p[:3], 0) if FILLS[spec["drop"]](p) else p for p in piece.get_flattened_data()])
            pad = math.ceil(3 * spec["glow"][0]) if "glow" in spec and "at" in spec else 0  
            piece = recoloured(ImageOps.expand(piece, pad) if pad else piece, spec)
            if "at" in spec:  
                image.alpha_composite(piece, (spec["at"][0] - pad, spec["at"][1] - top - pad))
                continue
            image.alpha_composite(fitted(piece.crop(ink(piece)), target.width, target.height, spec["into"],
                                         spec.get("left", True), spec.get("shrink", False)))
        return target.encode_rgba(recoloured(image, edit).tobytes())
    if "rows" in edit:
        image = composed(rgba(target), edit["rows"], donors, edit.get("align", "centre"), edit.get("gap", 0),
                         edit.get("bands"))
        return target.encode_rgba(recoloured(image, edit).tobytes())
    if WO1_X360.name in edit or WO2_X360.name in edit:
        entry, item, index = edit.get(WO1_X360.name) or edit[WO2_X360.name]
        source = donors[entry if WO1_X360.name in edit else (WO2_X360.name, entry)][item].textures[index]
        if (source.width, source.height) == (target.width, target.height) and "box" not in edit:
            if source.pixel_format == target.pixel_format and len(source.payload) == len(target.payload):
                return source.payload
            return target.encode_rgba(rgba(source).tobytes())  
    image = source_image(edit, donors, None)
    image = image.crop(edit.get("box") or ink(image))
    box = ink(rgba(target)) or (0, 0, target.width, target.height)
    return target.encode_rgba(fitted(image, target.width, target.height, box).tobytes())

def enlarged(g1t: G1tFile, edit: dict, donors: dict | None = None) -> G1tFile:

    texture = g1t.textures[edit["texture"]]
    size = list(edit.get("size", [texture.width, texture.height]))
    layout = native_menu_layout(edit, donors) if donors else None
    if layout:
        sample_minimum = ABILITY_QUAD_MINIMUM.get((edit.get("entry"), edit.get("item")), 0)
        for axis, extent in enumerate(layout["alpha_bbox"][2:]):
            required = max(1, math.ceil(extent), sample_minimum if axis == 0 else 0)
            if required > size[axis]:
                size[axis] = 1 << (required - 1).bit_length()
    if size == [texture.width, texture.height]:
        return g1t
    canvas = Image.new("RGBA", tuple(size))
    canvas.paste(rgba(texture))
    return g1t.with_texture_resized(edit["texture"], *size, canvas.tobytes())

def preview_png(path: Path, stock, edited) -> None:

    before, after = rgba(stock), rgba(edited)
    width, height = before.width + 8 + after.width, max(before.height, after.height)
    sheet = Image.new("RGBA", (width, 2 * height + 8), (128, 0, 128, 255))  
    for y, grey in ((0, 48), (height + 8, 208)):
        for x, image in ((0, before), (before.width + 8, after)):
            sheet.paste((grey, grey, grey, 255), (x, y, x + image.width, y + image.height))
            sheet.alpha_composite(image, (x, y))
    sheet.save(path)

def edited_entry(data: bytes, edits: list[dict], donors: dict, preview: Path | None = None) -> bytes:

    package = parse_texture_package(data)
    donors = donors | {"stock": package}
    for edit in edits:
        if native_source := native_officer_source(edit):
            entry, member, _ = native_source

            g1t = donors[(WO2_X360.name, entry)][member]
        elif (dream := native_dream_card(package.items[edit["item"]].g1t, edit, donors)) is not None:
            g1t = dream
        else:
            g1t = enlarged(package.items[edit["item"]].g1t, edit, donors)
            if edit.get("entry") == 4095 and "lines" in edit.get("card", {}):

                g1t = native_plate(g1t, rgba(g1t.textures[edit["texture"]]))
            g1t = g1t.with_texture_payload(edit["texture"], payload(g1t.textures[edit["texture"]], edit, donors, package))
        if g1t.encode() != donors["stock"].items[edit["item"]].source:
            g1t = rekeyed(g1t)
        package = package.with_g1t(edit["item"], g1t)
        if preview:
            preview_png(Path(f"{preview}.{edit['item']}.{edit['texture']}.png"),
                        donors["stock"].items[edit["item"]].g1t.textures[edit["texture"]], g1t.textures[edit["texture"]])
    return package.encode()

FONT_ENTRY = 4059  
GREY = (0x80, 0x80, 0x80)  

def latin_font(data: bytes, cells: Image.Image) -> bytes:

    if cells.height != CELL:
        raise ValueError(f"the Latin cells are {cells.height} px; the atlas takes {CELL}")
    atlas = parse_g1t(data)
    pages = [rgba(texture) for texture in atlas.textures]
    for i in range(cells.width // CELL):
        index = LATIN_GLYPH + i
        ink = Image.new("RGBA", (CELL, CELL), GREY)
        ink.putalpha(cells.crop((i * CELL, 0, i * CELL + CELL, CELL)))
        pages[index & 1].paste(ink, ((index >> 1) % 42 * CELL, (index >> 1) // 42 * CELL))
    for page, image in enumerate(pages):
        atlas = atlas.with_texture_rgba(page, image.tobytes())
    return atlas.encode()

GLYPH_SHEET = 1024  # px square of the runtime's glyph sheet (runtime/english_runtime.c GLYPH_SHEET)


def glyph_sheet_header(atlas: bytes, size: int = GLYPH_SHEET) -> bytes:
    """Everything of a G1T before the pixels, for one blank BGRA8 texture size x size: the runtime appends the
    pixels and hands it to the game's texture loader. Header fields as in the stock font atlas; its identity is the
    CRC of the blank file, as rekeyed() keys a texture."""
    source = parse_g1t(atlas)
    magic, _, _, table, _, *fields = G1T_HEADER.unpack_from(atlas)
    texture = G1T_TEXTURE_HEADER.pack(1 << 4, PIXEL_FORMAT_BGRA8, (size.bit_length() - 1) * 0x11,
                                      G1T_TEXTURE_HEADER.unpack_from(atlas, table + source.texture_offsets[0])[3])
    head = table + 4 + len(texture)
    body = struct.pack("<I", 4) + texture
    blank = G1T_HEADER.pack(magic, b"\0" * 4, head + size * size * 4, table, 1, *fields) + atlas[G1T_HEADER.size:table]
    identity = zlib.crc32(blank + body + bytes(size * size * 4)).to_bytes(4, "little")
    return blank[:4] + identity + blank[8:] + body


def glyphs_bin(atlas: bytes, outlines: bytes) -> bytes:
    """wozmod/glyphs.bin: u32 header size, the glyph sheet's G1T header, then the font's outlines."""
    header = glyph_sheet_header(atlas)
    return struct.pack("<I", len(header)) + header + outlines

PROMPT_ENTRY = 4079  

XBOX_PROMPTS = {**{slot: slot for slot in range(16)}, 11: 2, 12: 3, 44: 44, 178: 178, 179: 179}
XBOX_PROMPT_SIZE = {3: (64, 32)}  
TWO_P_SLOT, TWO_P_ROWS = 44, 56  

def xbox_prompts(stock: bytes, wo2: dict[int, G1tFile]) -> bytes:

    package = parse_texture_package(stock)
    for slot, item in XBOX_PROMPTS.items():
        g1t = wo2[item]
        if size := XBOX_PROMPT_SIZE.get(slot):
            icon = rgba(g1t.textures[0])
            g1t = g1t.with_texture_resized(0, *size, fitted(icon, *size, (0, 0, *size)).tobytes())
        if slot == TWO_P_SLOT:  
            moz = parse_g1t(package.items[slot].source)
            art = rgba(moz.textures[0])
            art.paste(rgba(g1t.textures[0]).crop((0, 0, art.width, TWO_P_ROWS)), (0, 0))
            g1t = moz.with_texture_rgba(0, art.tobytes())
        package = package.with_g1t(slot, rekeyed(g1t))
    return package.encode()

def read_entry(stream, entry) -> tuple[bytes, bool]:

    if entry.record.allocated_bytes:
        raise ValueError(f"entry {entry.ordinal} is multi-member")
    stream.seek(entry.offset)
    data = stream.read(entry.stored_bytes)
    return (parse_lzp2(data).decoded, True) if data[:4] == b"LZP2" else (data, False)

def read_members(stream, entry) -> list[bytes]:

    stream.seek(entry.offset)
    allocation = stream.read(entry.allocated_bytes)
    return [allocation[m.relative_offset:m.relative_offset + m.stored_bytes] for m in entry.members]

# Shares of the art stage (measured): loading the donors 0 to 0.5, the entries to 0.65, the large plates to 0.95.
LINKDATA2_FIRST = 4251  

def archive(key: int) -> tuple[Path, object]:

    if key < LINKDATA2_FIRST:
        return SHARED["base_bin"], SHARED["index"].entries[key]
    return SHARED["ld2_bin"], SHARED["ld2_index"].entries[key - LINKDATA2_FIRST]

def edited_lone(data: bytes, edits: list[dict], donors: dict, preview: Path | None = None) -> bytes:

    out = bytearray(data)
    for edit in edits:
        at = edit["g1t_at"]
        stock = parse_g1t(data[at:at + struct.unpack_from("<I", data, at + 8)[0]])
        g1t = rekeyed(stock.with_texture_payload(edit["texture"], payload(stock.textures[edit["texture"]], edit, donors, None)))
        encoded = g1t.encode()
        if len(encoded) != len(stock.encode()):
            raise ValueError(f"G1T at {at} changed size: an in-place edit keeps the stock geometry")
        out[at:at + len(encoded)] = encoded
        if preview:
            preview_png(Path(f"{preview}.{at}.{edit['texture']}.png"), stock.textures[edit["texture"]], g1t.textures[edit["texture"]])
    return bytes(out)

def rebuilt(ordinal: int) -> tuple[int, bytes, int]:

    group = SHARED["edits"][ordinal]
    preview = SHARED["preview"] and SHARED["preview"] / str(ordinal)
    edit = edited_lone if any("g1t_at" in e for e in group) else edited_entry
    base_bin, entry = archive(ordinal)
    with base_bin.open("rb") as stream:
        if entry.record.allocated_bytes:  
            members = read_members(stream, entry)
            data = b"".join(parse_lzp2(m).decoded for m in members)
            if ordinal == FONT_ENTRY:
                data = latin_font(data, SHARED["donors"]["latin"])
            else:
                data = edit(data, group, SHARED["donors"], preview)
            return ordinal, data if SHARED["write"] else b"", len(group)
        data, packed = read_entry(stream, entry)
    data = edit(data, group, SHARED["donors"], preview)
    if not SHARED["write"]:  
        return ordinal, b"", len(group)
    return ordinal, packed_lzp2(data) if packed else data, len(group)

def donor_stem(stem: Path | None, image: Path | None, identity, destination: Path, check: bool = True) -> Path:
    """The donor's linkdata_ens stem: stem, else both members extracted from the disc image to destination, which
    checks them against their pins as it copies them. With check, the members at a given stem are checked too."""
    if stem is None and image is not None:
        extract_donor(image, identity, destination,
                      files=("linkdata_ens.idx", "linkdata_ens.lnk"))
        return destination / "linkdata_ens"
    if stem is None:
        raise ValueError(f"give the {identity.name} donor archive or disc image")
    stem = Path(stem)
    if check:
        for suffix in (".idx", ".lnk"):
            name = "linkdata_ens" + suffix
            member = next(member for member in identity.files if member.path == name)
            verify_file(stem.with_suffix(suffix), member)
    return stem

class ArtError(ValueError):
    """An art input the build cannot use: a donor archive that fails its pin, missing Latin cells, or entries without
    edits. The message names the input; donor is the disc whose archive failed, when one did."""

    def __init__(self, message: str, donor=None) -> None:
        super().__init__(message)
        self.donor = donor

def build_art(game: Path, wo1_x360: Path | None, wo2: Path | None, text_donors: Path | None,
              output_dir: Path | None, *, hires: bool = False, squeeze: bool = True, jobs: int | None = None,
              review: bool = False, lzp2_cache: Path | None = None, latin_cells: Path | None = None,
              preview_dir: Path | None = None, entries: list[int] | None = None) -> None:
    """Build the menu art into output_dir/wozmod: entries/<ordinal>.bin, prompts-xbox.bin, glyphs.bin, and with hires
    hires.bin (the large-menu sheets the vector faces letter).

    The donors are linkdata_ens paths without their suffix, checked against their pins; text_donors is the folder the
    menu words resolve from. Nothing but the player files is written unless review is set: then the label review,
    the plate details and the vector plate sources go beside wozmod/. lzp2_cache keeps packed entries between builds.
    With preview_dir and entries, only those entries are rebuilt and their stock|edited sheets go to preview_dir
    instead (output_dir None). jobs is the worker process count (default: CPU count).
    Raises ArtError for a wrong input; for a wrong donor archive, before writing anything.
    """
    packing.LZP2_CACHE = lzp2_cache
    jobs = jobs or os.process_cpu_count()
    words.use(text_donors)
    working = output_dir or preview_dir
    try:
        wo1_x360 = donor_stem(wo1_x360, None, WO1_X360, None)
    except ValueError as error:
        raise ArtError(str(error), WO1_X360) from error
    try:
        wo2 = donor_stem(wo2, None, WO2_X360, None)
    except ValueError as error:
        raise ArtError(str(error), WO2_X360) from error
    mapping = words.load(MAPPING)
    edits: dict[int, list[dict]] = {}
    for edit in mapping["textures"]:  
        for ordinal, step in expanded(edit):
            edits.setdefault(ordinal, []).append(step)
    stages = mapping["stage_names"]
    for strips in stages["strips"]:
        edits.setdefault(strips["entry"], []).extend(
            {"entry": strips["entry"], "item": strips["item"] + k, "texture": 0, "stage_name": name,
             **({"size": strips["size"]} if "size" in strips else {})}
            for k, name in enumerate(stages["names"][:strips["count"]]))
    titles = stages["titles"]
    edits.setdefault(titles["entry"], []).extend(
        {"item": k, "texture": 0, "stage_title": name, "baseline": titles["baseline"],
         **({WO1_X360.name: [titles[WO1_X360.name], stages["rows"].index(name), 0]} if name in stages["rows"] else {})}
        for k, name in enumerate(stages["names"]))
    flat = [e for group in edits.values() for e in group]
    flat += [piece for e in flat for piece in [*sum(e.get("rows", []), []), *e.get("draw", []), e.get("plate"),
                                               e.get("banner", {}).get("badge"), e.get("card")] if piece]
    flat += [source for face in mapping.get("faces", {}).values() for source in face["rows"]]
    edits[FONT_ENTRY] = []
    if entries:  
        if missing := set(entries) - edits.keys():
            raise ArtError(f"entries without edits: {sorted(missing)}")
        edits = {ordinal: edits[ordinal] for ordinal in entries}
    donors = wo1_donors(wo1_x360, {e[WO1_X360.name][0] for e in flat if WO1_X360.name in e} | {stages["font"][0], titles[WO1_X360.name]})
    try:
        donors["latin"] = lettering.latin(latin_cells or working / "latin-cells.png", latin_cells is None)
    except ValueError as error:
        raise ArtError(str(error)) from error
    donors |= {(WO2_X360.name, k): wo2_g1ts(v) for k, v in wo1_entries(
        wo2.with_suffix(".lnk"), wo2.with_suffix(".idx"),
        sorted({e[WO2_X360.name][0] for e in flat if WO2_X360.name in e}
               | {s[WO2_X360.name][0] for e in flat for s in e.get("draw", []) if WO2_X360.name in s}
               | {3} | native_reference_entries(mapping))).items()}
    donors["squish"] = squeeze
    base_bin = game / "LINKDATA1.BIN"
    index = parse_index((game / "LINKDATA1.IDX").read_bytes(), base_bin.stat().st_size)
    stored: dict[int, bytes] = {}
    with base_bin.open("rb") as stream:
        donors |= {("moz", k): donor_g1ts(read_entry(stream, index.entries[k])[0])
                   for k in sorted({e[key][0] for e in flat for key in ("against", "base") if key in e})}
    donors["faces"] = faces(mapping, donors, jobs, used_faces(list(edits.values())))
    review_dir = output_dir / "review" if output_dir else preview_dir
    donors["menu_labels"] = prepare_menu_labels(mapping, review_dir / "native-menu-labels" if review else None,
                                                set(edits) if entries else None)
    donors["menu_sound_rows"] = {item: sound_menu_rows(mapping, item) for item in (23, 24, 25)}
    if preview_dir:
        preview_dir.mkdir(parents=True, exist_ok=True)
    if review and lettering.ACTIVE is None and not entries:  # its rows cover every entry
        native_width_path = review_dir / "native-menu-widths.json"
        native_width_path.parent.mkdir(parents=True, exist_ok=True)
        with lettering.untracked():
            report = native_menu_widths(mapping, donors)
        native_width_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ld2_bin = game / "LINKDATA2.BIN"
    SHARED.update(base_bin=base_bin, index=index, donors=donors, edits=edits,
                  ld2_bin=ld2_bin, ld2_index=parse_index((game / "LINKDATA2.IDX").read_bytes(), ld2_bin.stat().st_size),
                  preview=preview_dir, write=bool(output_dir))

    with worker_pool(jobs) as pool:
        futures = [pool.submit(rebuilt, ordinal)
                   for ordinal in sorted(edits, key=lambda ordinal: -archive(ordinal)[1].stored_bytes)]
        try:
            for future in as_completed(futures):
                ordinal, data, count = future.result()
                stored[ordinal] = data
                report_progress("art", "building menu textures", 0.5 + 0.15 * len(stored) / len(edits),
                                (len(stored), len(edits)))
                print(f"entry {ordinal}: {count} textures, {archive(ordinal)[1].stored_bytes} -> {len(data)} bytes",
                      flush=True)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    if not output_dir:
        print(f"{preview_dir}: previews of {len(stored)} entries")
        return
    built = output_dir / "wozmod/entries"
    built.mkdir(parents=True, exist_ok=True)
    for ordinal, data in stored.items():
        (built / f"{ordinal}.bin").write_bytes(data)
    with base_bin.open("rb") as stream:
        prompts = read_entry(stream, index.entries[PROMPT_ENTRY])[0]
    wo2_prompts = wo1_entries(wo2.with_suffix(".lnk"), wo2.with_suffix(".idx"), [13])[13]
    (output_dir / "wozmod/prompts-xbox.bin").write_bytes(xbox_prompts(prompts, wo2_g1ts(wo2_prompts)))
    # an install takes the outlines from the lettering bundle: players have no FontForge
    outlines = lettering.outlines(working)
    with base_bin.open("rb") as stream:
        atlas = b"".join(parse_lzp2(m).decoded for m in read_members(stream, index.entries[FONT_ENTRY]))
    (output_dir / "wozmod/glyphs.bin").write_bytes(glyphs_bin(atlas, from_json(outlines)))
    if hires:
        write_hires(output_dir, wo1_x360, wo2, squeeze, jobs, review)
    print(f"{output_dir}: {len(stored)} entries")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wo1-x360-donor", type=Path, default=os.environ.get("WOZMOD_WO1_X360_DONOR"),
                        help="WO1 Xbox 360 USA linkdata_ens path without its .lnk/.idx suffix")
    parser.add_argument("--text-donors", type=Path, default=os.environ.get("WOZMOD_DONORS"),
                        help="the installer's donors folder, laid out as for the text table: the menu words "
                             "the art letters resolve from its programs and archives")
    parser.add_argument("--latin-cells", type=Path, default=os.environ.get("WOZMOD_LATIN_CELLS"),
                        help="24-pixel vector Latin cells; generated when omitted")
    parser.add_argument("--wo2-x360-donor", type=Path, default=os.environ.get("WOZMOD_WO2_X360_DONOR"),
                        help="WO2 Xbox 360 USA linkdata_ens path without its .lnk/.idx suffix")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--jobs", type=int, default=os.process_cpu_count(), help="entries rebuilt in parallel")
    parser.add_argument("--preview-dir", type=Path,
                        help="write <entry>.<item>.<texture>.png stock|edited sheets of every edited texture")
    parser.add_argument("--entries", type=int, nargs="+",
                        help="rebuild only these entries for --preview-dir; writes no entries")
    parser.add_argument("--hires", action="store_true",
                        help="also write wozmod/hires.bin, the large-menu sheets the vector faces letter")
    parser.add_argument("--hires-only", action="store_true", help="with --hires: the entries are already built")
    parser.add_argument("--no-squish", action="store_true",
                        help="developer oracle only: keep full-width vector lettering")
    parser.add_argument("--game", type=Path, default=os.environ.get("WOZMOD_GAME"))
    parser.add_argument("--wo1-x360-iso", type=Path, default=os.environ.get("WOZMOD_WO1_X360_ISO"))
    parser.add_argument("--wo2-x360-iso", type=Path, default=os.environ.get("WOZMOD_WO2_X360_ISO"))
    parser.add_argument("--font-python", default=lettering.FONT_PYTHON)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args(argv)
    lettering.FONT_PYTHON = args.font_python
    working = args.output_dir or args.preview_dir
    if (bool(args.entries) == bool(args.output_dir)) or (args.entries and not args.preview_dir):
        parser.error("give --output-dir, or --entries with --preview-dir (a partial build is never written)")
    if args.hires_only and not (args.hires and args.output_dir):
        parser.error("--hires-only needs --hires and --output-dir")
    if not args.hires_only and args.game is None:
        parser.error("give --game or WOZMOD_GAME")
    try:  # build_art checks the archives against their pins; a --hires-only run never calls it
        args.wo1_x360_donor = donor_stem(args.wo1_x360_donor, args.wo1_x360_iso, WO1_X360,
                                         working / "donors/wo1-x360", check=args.hires_only)
        args.wo2_x360_donor = donor_stem(args.wo2_x360_donor, args.wo2_x360_iso, WO2_X360,
                                         working / "donors/wo2-x360", check=args.hires_only)
    except ValueError as error:
        parser.error(str(error))
    lzp2_cache = args.cache_dir or working / "lzp2-cache"
    if args.hires_only:
        packing.LZP2_CACHE = lzp2_cache
        words.use(args.text_donors)
        write_hires(args.output_dir, args.wo1_x360_donor, args.wo2_x360_donor, not args.no_squish, args.jobs,
                    review=True, fresh=False)
    else:
        try:
            build_art(args.game, args.wo1_x360_donor, args.wo2_x360_donor, args.text_donors, args.output_dir,
                      hires=args.hires, squeeze=not args.no_squish, jobs=args.jobs, review=True,
                      lzp2_cache=lzp2_cache, latin_cells=args.latin_cells, preview_dir=args.preview_dir,
                      entries=args.entries)
        except ArtError as error:
            parser.error(str(error))
    if args.output_dir:
        write_manifest(args.output_dir)
    return 0

def write_manifest(directory: Path) -> None:
    """manifest.json: every other file under ``directory`` with its SHA-256, by relative path."""
    files = sorted(p for p in directory.rglob("*") if p.is_file() and p.name != "manifest.json")
    manifest = {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


def write_hires(output_dir: Path, wo1_x360: Path | None, wo2: Path | None, squeeze: bool, jobs: int = 1,
                review: bool = False, fresh: bool = True) -> None:
    """Write output_dir/wozmod/hires.bin over the built entries; review and fresh as for build_art, where fresh
    clears the plate sources a --hires-only run reuses."""
    mapping = words.load(MAPPING)
    if review and fresh:
        cache = output_dir / "vector-plate-sources"
        for old in cache.glob("*.g1t"):
            old.unlink()
    pieces = [p for e in mapping["textures"] if (e["entry"], e.get("item")) in {h[:2] for h in HIRES} | set(DARK)
              for row in e.get("rows") or [e.get("draw", [])] for p in row]
    donors = wo1_donors(wo1_x360, {p[WO1_X360.name][0] for p in pieces if "box" in p and WO1_X360.name in p} | {1, 16})
    donors |= {(WO2_X360.name, k): x360_members(v) for k, v in wo1_entries(
        wo2.with_suffix(".lnk"), wo2.with_suffix(".idx"),
        sorted(set(X360_PLATE_ENTRIES) | native_reference_entries(mapping))).items()}
    donors["menu_labels"] = prepare_menu_labels(mapping)
    donors["squish"] = squeeze
    (output_dir / "wozmod/hires.bin").write_bytes(hires_bin(
        output_dir / "wozmod/entries", (mapping, donors), squeeze, jobs, output_dir if review else None))
    print(f"{output_dir}: hires.bin")

if __name__ == "__main__":
    raise SystemExit(main())
