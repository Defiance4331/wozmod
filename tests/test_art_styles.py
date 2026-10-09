from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from wozmod.art import build


class Texture:
    pixel_format = 8

    def __init__(self, image: Image.Image, payload: bytes = b"fixture"):
        self.image = image
        self.width, self.height = image.size
        self.payload = payload

    def decode_rgba(self) -> bytes:
        return self.image.tobytes()

    def encode_rgba(self, data: bytes) -> Image.Image:
        return Image.frombytes("RGBA", self.image.size, data)


def test_sound_rows_do_not_drop_labels_missing_from_harvested_face(monkeypatch):
    records = {3: {"pc_text": "Orbit"}, 11: {"pc_text": "Signal"}}
    monkeypatch.setattr(build, "reference", lambda texture, row: records.get(row))
    mapping = {"faces": {"sound": {"rows": [
        {"base": [4069, 24, 0], "texts": [None] * 35},
    ]}}}
    assert build.sound_menu_rows(mapping, 24) == [
        {"row": 3, "text": "Orbit", "at": [3, 157]},
        {"row": 11, "text": "Signal", "at": [3, 493]},
    ]


def test_ability_colour_roles_cannot_reuse_direct_donor_payload(monkeypatch):
    colours = {
        "blue": (16, 64, 240, 255),
        "pink": (240, 48, 144, 255),
        "yellow": (240, 208, 32, 255),
    }
    families = [(item, role) for role, start in (("blue", 60), ("pink", 66), ("yellow", 72))
                for item in range(start, start + 6)]
    roles = {f"4079.{item}": role for item, role in families}
    monkeypatch.setattr(build, "reference", lambda texture, row: {"role_variant": roles[texture]})
    metrics = {"baseline": 3, "ink": [0, 0, 4, 4]}
    target = Texture(Image.new("RGBA", (128, 64)), b"fixture")
    donor_art = Image.new("RGBA", target.image.size)
    donor_art.paste((200, 0, 200, 255), (2, 4, 10, 26))
    source = Texture(donor_art, b"incorrect")
    donors = {
        507: {item - 1: SimpleNamespace(textures=[source]) for item, _ in families},
        "menu_native_texts": {(4079, item, 0): "Fixture" for item, _ in families},
        "menu_labels": {
            build.menu_label_key("rounded.ability", "Fixture", role=role):
                (Image.new("RGBA", (4, 4), colour), metrics)
            for role, colour in colours.items()
        },
    }
    failures = []
    for item, role in families:
        edit = {"entry": 4079, "item": item, "texture": 0, "donor": [507, item - 1, 0]}
        image = build.payload(target, edit, donors, None)
        if not isinstance(image, Image.Image):
            failures.append((item, role, "returned donor payload", image))
            continue
        actual = {colour: count for count, colour in image.getcolors(image.width * image.height)}
        expected = {(0, 0, 0, 0): 128 * 64 - 16, colours[role]: 16}
        if image.size != target.image.size or actual != expected:
            failures.append((item, role, image.size, actual))
    assert not failures, failures


def test_stage_placement_does_not_clip_left_or_top_halo():
    target = Texture(Image.new("RGBA", (32, 35)))
    failures = []
    for name, height, baseline, top in (("Tall", 35, 30, 0), ("Small", 10, 7, 22)):
        label = Image.new("RGBA", (10, height), (49, 32, 8, 32))
        label.paste((220, 180, 40, 255), (3, 2, 7, baseline))
        metrics = {"baseline": baseline, "ink": [3, 2, 7, baseline]}
        donors = {"menu_labels": {build.menu_label_key("stage.stage", name): (label, metrics)}}
        expected = Image.new("RGBA", target.image.size)
        expected.alpha_composite(label, (0, top))
        image = build.payload(target, {"stage_name": name}, donors, None)
        if image.size != expected.size or image.tobytes() != expected.tobytes():
            failures.append((name, image.getbbox()))
    assert not failures, failures


def test_double_scale_glow_stays_inside_sampled_height(monkeypatch):
    label = Image.new("RGBA", (32, 68))
    label.paste((214, 117, 107, 255), (3, 11, 28, 64))
    label.paste((16, 16, 16, 1), (6, 67, 25, 68))
    metrics = {"baseline": 59, "ink": [11, 19, 27, 60]}
    monkeypatch.setattr(build, "header_label", lambda *args: (label, metrics))
    mapping = {"textures": [{"entry": 4079, "item": 80}]}
    failures = []
    for height, expected_y in ((31, -6), (35, -5)):
        layout = {"style": "rounded.base", "role": None, "text": "Fixture",
                  "left": 4, "baseline": 27, "sampled_window": [0, 0, 140, height]}
        monkeypatch.setattr(build, "native_menu_layout", lambda *args, row=layout: row)
        art = build.native_menu_art(4079, 80, Image.new("RGBA", (256, 64)), mapping, {}, 2)
        expected = Image.new("RGBA", art.size)
        expected.alpha_composite(label, (-3, expected_y))
        if art.tobytes() != expected.tobytes() or art.getbbox()[3] > height * 2:
            failures.append((height, art.getbbox()))
    assert not failures, failures
