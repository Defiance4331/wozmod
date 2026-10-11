from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from wozmod.art import build


class Texture:
    def __init__(self, image):
        self.image = image
        self.width, self.height = image.size

    def decode_rgba(self):
        return self.image.tobytes()

    def encode_rgba(self, data):
        return Image.frombytes("RGBA", self.image.size, data)


def test_native_rounded_row_does_not_keep_overflowing_lettering_at_full_width(monkeypatch):
    monkeypatch.setattr(build, "reference", lambda texture, row: {
        "pc_text": "Fixture", "role_variant": "blue",
        "original": {"donor": "x360", "ens": [1, 0, 0]},
    })
    source = Image.new("RGBA", (64, 32))
    source.paste((240, 240, 240, 255), (3, 5, 15, 9))
    label = Image.new("RGBA", (40, 8))
    label.paste((16, 64, 240, 255), (1, 1, 35, 5))
    metrics = {"baseline": 5, "ink": [1, 1, 35, 5]}
    target = Texture(Image.new("RGBA", (64, 32)))
    donor = SimpleNamespace(textures=[Texture(source)])
    donors = {
        5: {0: donor}, ("wo2", 1): {0: donor},
        "menu_labels": {build.menu_label_key("rounded.ability", "Fixture", "blue"):
                        (label, metrics)},
    }
    edit = {"entry": 4079, "item": 60, "texture": 0, "donor": [5, 0, 0]}
    full = build.native_menu_payload(target, edit, dict(donors, squish=False))
    expected = Image.new("RGBA", (64, 32))
    expected.paste((16, 64, 240, 255), (3, 5, 37, 9))
    assert full.tobytes() == expected.tobytes()
    squished = build.native_menu_payload(target, edit, dict(donors, squish=True))
    fill = squished.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0)
    assert fill.getbbox() == (3, 5, 15, 9)


def test_stage_strip_name_condenses_only_where_it_overflows_its_own_strip(monkeypatch):
    """Strips keep the MOZ layout: a name that fits its 440 strip stays full width even where the X360
    strip is narrower; one that overflows the strip condenses into it."""
    source = Image.new("RGBA", (512, 35))
    source.paste((240, 240, 240, 255), (0, 5, 200, 25))  # X360 counterpart filling 200 px
    monkeypatch.setattr(build, "reference", lambda texture, row: {
        "original": {"donor": "x360", "ens": [1, 0, 0], "crop": [0, 0, 512, 35]}})
    right = {}
    for width in (400, 470):
        label = Image.new("RGBA", (480, 30))
        label.paste((240, 200, 40, 255), (0, 2, width, 26))
        metrics = {"baseline": 26, "ink": [0, 2, width, 26]}
        donors = {"squish": True, ("wo2", 1): {0: SimpleNamespace(textures=[Texture(source)])},
                  "menu_labels": {build.menu_label_key("stage.stage", "Fixture"): (label, metrics)}}
        target = Texture(Image.new("RGBA", (512, 64)))
        edit = {"entry": 4077, "item": 30, "texture": 0, "stage_name": "Fixture"}
        image = build.native_menu_payload(target, edit, donors)
        box = image.getchannel("A").point(lambda alpha: 255 if alpha > 16 else 0).getbbox()
        right[width] = box[2] - box[0]
    assert right[400] == 400 and right[470] <= 440
