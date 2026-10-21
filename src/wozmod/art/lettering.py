"""The lettering the art build draws: its requests, who draws them, and the bundle that serves them.

Each layer is named by a request, a dict whose SHA-256 (`key`) is its name in lettering.zip and in
data/art/lettering.json, so its fields and values must not change. The builders (`header`, `button`, `pause`,
`native`, `latin`, `squeezed`) return (image, metrics), from the active bundle (`using`) when there is one, else
from `PRODUCER`: a `FontProcess` (the font Python with FontForge and Cairo), or a stand-in in tests. A producer has
`produce(request)` for header, button and pause requests, `native(requests, keep)`, `latin(path)` and
`outlines(folder)`.

Under the font Python, `python -m wozmod.art.lettering REQUEST_JSON OUT.png` draws a button or pause request.
This module imports nothing beyond Pillow and wozmod.process at the top, so both Pythons load it.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from PIL import Image

from wozmod import process

FONT_PYTHON = os.environ.get("WOZMOD_FONT_PYTHON", "/usr/bin/python3")
MENU_BATCH = "wozmod.fonts.menu_batch"
BUTTON_CAP = 25.0
ACTIVE = None
OUTLINES = None  # the active bundle's glyph outlines
TRACE = os.environ.get("WOZMOD_ART_TRACE")
_UNTRACKED = 0


def key(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def font_environment() -> dict[str, str]:
    environment = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (package_root, environment.get("PYTHONPATH"))))
    return environment


class FontProcess:
    """The production producer: each request drawn by the font Python in its own process."""

    def __init__(self, python: str | None = None):
        self.python = python  # None: FONT_PYTHON when it runs

    def run(self, module: str, *args: str) -> str:
        """`python -m module args` under the font Python; its stdout."""
        run = process.run([self.python or FONT_PYTHON, "-m", module, *args], capture_output=True,
                          text=True, encoding="utf-8", env=font_environment())
        if run.returncode:
            sys.stderr.write(run.stderr)
            run.check_returncode()
        return run.stdout

    def produce(self, request: dict) -> tuple[Image.Image, dict]:
        kind = request["kind"]
        with tempfile.TemporaryDirectory(prefix="lettering-") as temporary:
            out = Path(temporary) / "label.png"
            if kind == "header":
                stdout = self.run(request["raster"], request["text"], str(out), "--scale", str(request["scale"]),
                                  *request["options"])
            elif kind in {"button", "pause"}:
                stdout = self.run("wozmod.art.lettering", json.dumps(request), str(out))
            else:
                raise ValueError(f"unsupported full-width lettering request: {kind}")
            with Image.open(out) as image:
                return image.convert("RGBA"), json.loads(stdout.splitlines()[-1])

    def native(self, requests: list[dict], keep: Path | None = None) -> list[tuple[Image.Image, dict]]:
        """The menu faces' labels (wozmod.fonts.menu_batch), in request order; keep holds the batch's files."""
        if not requests:
            return []
        with tempfile.TemporaryDirectory(prefix="lettering-native-") as temporary:
            path, output = Path(temporary) / "requests.json", keep or Path(temporary) / "labels"
            path.write_text(json.dumps(requests, ensure_ascii=False), encoding="utf-8")
            self.run(MENU_BATCH, str(path), str(output))
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            if len(manifest) != len(requests):
                raise ValueError("menu batch dropped a requested native label")
            layers = []
            for index, (record, request) in enumerate(zip(manifest, requests, strict=True)):
                if record["index"] != index or record["request"] != request:
                    raise ValueError(f"menu batch changed native label {index}")
                with Image.open(output / record["file"]) as image:
                    layers.append((image.convert("RGBA"), record["metrics"]))
            return layers

    def latin(self, path: Path) -> None:
        """Draw the 24-pixel Latin cells to path."""
        self.run("wozmod.fonts.raster", str(path), "--px", "24", "--spacing", "legacy")

    def outlines(self, folder: Path) -> bytes:
        """The runtime's glyph outlines as JSON (wozmod.fonts.glyph_outlines), made with FontForge in folder."""
        path = folder / "glyph-outlines.json"
        self.run("wozmod.fonts.glyph_outlines", str(path))
        return path.read_bytes()


PRODUCER = FontProcess()


class _Traced(Image.Image):
    """Marks its layer as drawn when the build reads its pixels outside a squish."""

    _marker: Path

    def load(self):
        if not _UNTRACKED:
            self._marker.touch()
        return super().load()


def _traced(image: Image.Image, folder: Path, identity: str) -> Image.Image:
    image.__class__ = _Traced
    image._marker = folder / f"{identity}.pixels"
    return image


def _get(request: dict, draw) -> tuple[Image.Image, dict]:
    identity = key(request)
    if ACTIVE is not None:
        try:
            image, metrics = ACTIVE[identity]
        except KeyError:
            raise ValueError(f"lettering bundle is missing request {identity}") from None
        return image.copy(), dict(metrics, _lettering=identity)
    if TRACE:
        folder = Path(TRACE)
        folder.mkdir(parents=True, exist_ok=True)
        record = folder / f"{identity}.json"
        record.write_text(json.dumps(request, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        cached = folder / f"{identity}.png"
        metadata = folder / f"{identity}.metrics"
        if cached.exists() and metadata.exists():
            return (_traced(Image.open(cached).copy(), folder, identity),
                    dict(json.loads(metadata.read_text(encoding="utf-8")), _lettering=identity))
    if request.get("kind") == "squish":
        with untracked():
            image, metrics = draw()
    else:
        image, metrics = draw()
    metrics = dict(metrics)
    if TRACE:
        image.save(cached)
        metadata.write_text(json.dumps(metrics, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        image = _traced(image, folder, identity)
    return image, dict(metrics, _lettering=identity)


def _produced(request: dict) -> tuple[Image.Image, dict]:
    return _get(request, lambda: PRODUCER.produce(request))


def header(text: str, scale: int, raster: str, *options: str) -> tuple[Image.Image, dict]:
    """A label drawn by a raster module's command line (`python -m raster text out --scale scale *options`)."""
    return _produced(dict(kind="header", text=text, scale=scale, raster=raster, options=list(options)))


def button(text: str, scale: int, track: float = 0.0, style: str = "501.3") -> tuple[Image.Image, dict]:
    """A buttonraster label, `track` wider after each character; its metrics add the fill's sub-pixel ends and
    the box of everything drawn (outline and glow too)."""
    return _produced(dict(kind="button", text=text, scale=scale, track=track, style=style))


def pause(text: str, scale: int, target: float, counterpart: str | None) -> tuple[Image.Image, dict]:
    """An X360 pause-menu button: `target` wide, or with no counterpart scaled as the counterpart's
    natural width is to `target`."""
    return _produced(dict(kind="pause", text=text, scale=scale, target=target, counterpart=counterpart))


def native(requests: list[dict], keep: Path | None = None) -> list[tuple[Image.Image, dict]]:
    """The menu faces' labels for requests ({style, text[, role]}), in order; keep holds the drawn files."""
    if ACTIVE is not None:
        return [_get(dict(kind="native", **request), None) for request in requests]
    drawn = PRODUCER.native(requests, keep)
    return [_get(dict(kind="native", **request), lambda layer=layer: layer)
            for request, layer in zip(requests, drawn, strict=True)]


def latin(cells: Path, draw: bool) -> Image.Image:
    """The 24-pixel Latin cells: the bundle's, else the PNG at cells, which the producer draws first when draw."""
    if ACTIVE is None:
        if draw:
            cells.parent.mkdir(parents=True, exist_ok=True)
            PRODUCER.latin(cells)
        if not cells.is_file():
            raise ValueError(f"missing Latin cells: {cells}")
    return _get(dict(kind="latin"), lambda: (Image.open(cells).convert("L"), {}))[0]


def outlines(folder: Path) -> bytes:
    """The runtime's glyph outlines: the bundle's, else made by the producer in folder."""
    return OUTLINES if ACTIVE is not None else PRODUCER.outlines(folder)


def fill_edges(profile: list[float]) -> tuple[float, float]:
    """The sub-pixel ends of a fill, from its per-column coverage."""
    on = [x for x, v in enumerate(profile) if v >= 0.5]
    i, j = on[0], on[-1]
    return i - (profile[i - 1] if i else 0), j + 1 + (profile[j + 1] if j + 1 < len(profile) else 0)


def condense(label: Image.Image, metrics: dict, width: float) -> tuple[Image.Image, dict]:
    """Condense a label horizontally until its fill (else its ink) is `width` pixels wide."""
    left, right = metrics.get("fill") or metrics["ink"][::2]
    natural = right - left
    if natural <= width or width <= 0:
        return label, metrics
    factor = width / natural

    horizontal = lambda x: left + (x - left) * factor
    image = label.transform((math.ceil(horizontal(label.width)), label.height),
                            Image.Transform.AFFINE, (1 / factor, 0, left * (1 - 1 / factor), 0, 1, 0),
                            Image.Resampling.BICUBIC)
    changed = dict(metrics, ink=[horizontal(v) if k % 2 == 0 else v
                                 for k, v in enumerate(metrics["ink"])])
    if "fill" in metrics:
        changed["fill"] = [horizontal(v) for v in metrics["fill"]]
    if "drawn" in metrics:
        changed["drawn"] = [horizontal(v) if k % 2 == 0 else v for k, v in enumerate(metrics["drawn"])]
    if "pen" in metrics:
        changed["pen"] = horizontal(metrics["pen"])
    if "advance" in metrics:
        changed["advance"] = metrics["advance"] * factor
    if "size" in metrics:
        changed["size"] = list(image.size)
    return image, changed


def squeezed(label: Image.Image, metrics: dict, geometry, scale: int,
             squeeze: bool = False) -> tuple[Image.Image, dict]:
    """With squeeze, a label too wide for its official row (geometry: left, right, baseline at 1x) condensed
    to fit; a label from a request becomes its own squish request."""
    if not squeeze or geometry is None:
        return label, metrics
    left, right = metrics.get("fill") or metrics["ink"][::2]
    width = (geometry[1] - geometry[0]) * scale
    if right - left <= width or width <= 0:
        return label, metrics
    if "_lettering" not in metrics:
        return condense(label, metrics, width)
    request = dict(kind="squish", base=metrics["_lettering"], width=width, fill=metrics.get("fill"))
    return _get(request, lambda: condense(label, metrics, width))


@contextmanager
def untracked():
    """Pixel reads here (squish sources, developer reports) do not ship a layer."""
    global _UNTRACKED
    _UNTRACKED += 1
    try:
        yield
    finally:
        _UNTRACKED -= 1


@contextmanager
def using(layers, outlines):
    """Serve every request from a lettering bundle's layers and glyph outlines."""
    global ACTIVE, OUTLINES
    previous = ACTIVE, OUTLINES
    ACTIVE, OUTLINES = layers, outlines
    try:
        yield
    finally:
        ACTIVE, OUTLINES = previous


def _draw(request: dict, out: Path) -> dict:
    """The font Python's side of FontProcess for a button or pause request: draw it to out, return its metrics."""
    from wozmod.fonts import buttonraster

    text, scale = request["text"], float(request["scale"])
    if request["kind"] == "button":
        label = buttonraster.render(text, scale, BUTTON_CAP, request["style"], track=float(request["track"]))
        drawn = label.image.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()  # outline and glow too
        metrics = dict(baseline=label.baseline, ink=list(label.ink), fill=list(fill_edges(label.fill_profile)),
                       drawn=list(drawn))
    elif request["kind"] == "pause":
        target, counterpart, hscale = float(request["target"]), request["counterpart"], 1.0
        if counterpart is not None:
            reference = buttonraster.render_x360_pause(counterpart, 1.0)
            hscale, target = target / (reference.ink[2] - reference.ink[0]), None
        label = buttonraster.render_x360_pause(text, scale, target_width=target, hscale=hscale)
        metrics = dict(baseline=label.baseline, ink=list(label.ink), fill=list(fill_edges(label.fill_profile)),
                       counterpart=counterpart, hscale=hscale)
    else:
        raise ValueError(f"the font side draws buttons and pauses, not {request['kind']}")
    label.image.save(out)
    return metrics


if __name__ == "__main__":
    print(json.dumps(_draw(json.loads(sys.argv[1]), Path(sys.argv[2]))))
