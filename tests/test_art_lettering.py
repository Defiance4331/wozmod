"""The lettering module through its builders: the requests they ask for, and what the font Python draws."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest
from PIL import Image

from wozmod.art import build, lettering


def _font_python() -> lettering.FontProcess:
    python = os.environ.get("WOZMOD_FONT_PYTHON", "/usr/bin/python3")
    if not Path(python).is_file():
        pytest.skip("font Python absent")
    probe = subprocess.run([python, "-c", "import importlib.util; print([m for m in ('fontforge', 'cairo', 'numpy') "
                                          "if importlib.util.find_spec(m) is None])"], capture_output=True, text=True)
    if probe.stdout.strip() != "[]":
        pytest.skip(f"font libraries absent: {probe.stdout.strip()}")
    return lettering.FontProcess(python)


def test_each_builder_asks_the_bundle_for_the_request_the_recipe_names():
    """Bug: a builder renames, adds or drops a request field, or passes a value of another type (a float scale), so its
    requests no longer hash to the recipe's and an install misses every layer of that kind."""
    rows = {}
    for request in json.loads((build.REPO / "data/art/lettering.json").read_text())["requests"].values():
        rows.setdefault(request["kind"], {name: "Alder" if isinstance(value, dict) else value  # hashed words
                                          for name, value in request.items()})
    asks = {
        "header": lambda r: lettering.header(r["text"], r["scale"], r["raster"], *r["options"]),
        "button": lambda r: lettering.button(r["text"], r["scale"], r["track"], r["style"]),
        "pause": lambda r: lettering.pause(r["text"], r["scale"], r["target"], r["counterpart"]),
        "native": lambda r: lettering.native([{name: value for name, value in r.items() if name != "kind"}]),
        "latin": lambda r: lettering.latin(Path("unread.png"), False),
        "squish": lambda r: lettering.squeezed(
            Image.new("RGBA", (1, 1)), {"ink": [0, 0, 10**6, 1], "_lettering": r["base"],
                                        **({"fill": r["fill"]} if r["fill"] else {})}, (0, r["width"], 0), 1, True),
    }
    assert set(asks) == set(rows)
    missed = []
    for kind, row in rows.items():
        with lettering.using({lettering.key(row): (Image.new("RGBA", (1, 1)), {})}, b""):
            try:
                asks[kind](row)
            except ValueError:
                missed.append(kind)
    assert missed == []


def test_the_buttonraster_command_line_draws_the_tracked_button_players_see(tmp_path):
    """Bug: the build letters buttons through a path the buttonraster command line cannot take (it once ran a patched
    copy of render), so a button checked with the command line is not the image players get."""
    font = _font_python()
    image, metrics = font.produce({"kind": "button", "text": "ALDER", "scale": 2, "track": 0.5, "style": "493.9"})
    out = tmp_path / "cli.png"
    printed = json.loads(font.run("wozmod.fonts.buttonraster", "ALDER", str(out), "--scale", "2", "--style", "493.9",
                                  "--track", "0.5"))
    with Image.open(out) as cli:
        assert (cli.convert("RGBA").tobytes(), cli.size, printed["baseline"]) == (
            image.tobytes(), image.size, metrics["baseline"])


def test_the_font_process_draws_the_release_lettering_layers(monkeypatch):
    """Bug: the font side draws a button or pause another way than the release did (the sub-pixel phase on the wrong
    branch, another cap, another fill rule), so a developer build no longer matches lettering.zip."""
    from wozmod.art import render, words
    from wozmod.art.bundle import MetricsOnly, read_bundle

    release, donors = (Path(os.environ.get(name, "")) for name in ("WOZMOD_LETTERING", "WOZMOD_DONORS"))
    if not release.is_file() or not (donors / "wo2-x360").is_dir():
        pytest.skip("set WOZMOD_LETTERING and WOZMOD_DONORS")
    font = _font_python()
    monkeypatch.setattr(words, "DONORS", words.DONORS)
    words.use(donors)
    requests, _, _ = render._load_requests()
    layers, _ = read_bundle(release)
    samples = {}
    for identity, request in sorted(requests.items()):
        if request["kind"] in {"button", "pause"} and identity in layers:
            samples.setdefault((request["kind"], request.get("track") == 0, request.get("counterpart") is None,
                                isinstance(layers[identity][0], MetricsOnly)), identity)
    assert len(samples) >= 4, sorted(samples)
    differ = []
    for identity in samples.values():  # a layer the release ships as metrics only is checked by its metrics
        image, metrics = font.produce(requests[identity])
        shipped, shipped_metrics = layers[identity]
        pixels = None if isinstance(shipped, MetricsOnly) else shipped.convert("RGBA").tobytes()
        if ({k: v for k, v in metrics.items() if k != "counterpart"}, pixels and image.tobytes()) != (
                shipped_metrics, pixels):
            differ.append(requests[identity]["kind"])
    assert differ == []
