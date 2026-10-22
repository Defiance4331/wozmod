"""Render the authored lettering recipes into a release bundle."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time

import PIL
from PIL import Image

from wozmod import process
from . import build, faces, lettering, vector, words
from .bundle import write_bundle


RECIPE = build.REPO / "data/art/lettering.json"
RECIPE_VERSION = 2
TEXT_SOURCES = (build.REPO / "data/art/menu_textures.json",
                build.REPO / "data/art/menu_visuals.json")
# Everything that decides a drawn layer's pixels and metrics besides its request: the code and face sources the font
# Python runs (its imports never leave these), that Python and its drawing libraries, and the system libraries under
# them (the package database). A layer cache keyed on less serves layers an older renderer drew.
RENDERER_SOURCES = ("src/wozmod/fonts", "src/wozmod/art/lettering.py", "src/wozmod/runtime/latin_font.py",
                    "src/wozmod/process.py", "src/wozmod/resources.py", "data/art/faces")
FONT_SETUP = """import sys, cairo, fontforge, numpy, PIL
print(sys.version, fontforge.version(), cairo.version, cairo.cairo_version_string(), numpy.__version__, PIL.__version__)"""
SYSTEM_PACKAGES = (Path("/usr/lib/sysimage/rpm/rpmdb.sqlite"), Path("/usr/lib/sysimage/rpm/rpmdb.sqlite-wal"))
_RASTERS = {vector.HEADER_RASTER, vector.VALUE_RASTER, vector.MAIN_RASTER,
            vector.LABEL_RASTER, vector.MENU_RASTER}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item)


def _text_index() -> dict[str, str]:
    values = [words.load(path) for path in TEXT_SOURCES]
    # the words the build's own tables letter, wherever the art build keeps them
    values.extend(value for module in (build, faces, vector) for name, value in vars(module).items()
                  if name.isupper())
    return {hashlib.sha256(text.encode("utf-8")).hexdigest(): text
            for value in values for text in _strings(value)}


def _decode(value, texts: dict[str, str]):
    if isinstance(value, dict):
        if set(value) == {"text_sha256"}:
            digest = value["text_sha256"]
            if not isinstance(digest, str) or digest not in texts:
                raise ValueError(f"lettering recipe has unresolved text SHA-256: {digest}")
            return texts[digest]
        return {key: _decode(item, texts) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item, texts) for item in value]
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate lettering recipe key: {key}")
        result[key] = value
    return result


def _number(request: dict, name: str, positive: bool = False) -> None:
    value = request[name]
    if (type(value) not in (int, float) or not math.isfinite(value)
            or positive and value <= 0):
        raise ValueError(f"invalid lettering request {name}")


def _validate(request: dict) -> None:
    fields = {
        "header": {"kind", "text", "scale", "raster", "options"},
        "button": {"kind", "text", "scale", "track", "style"},
        "pause": {"kind", "text", "scale", "target", "counterpart"},
        "native": {"kind", "text", "style"},
        "latin": {"kind"},
        "squish": {"kind", "base", "width", "fill"},
    }
    kind = request.get("kind")
    if not isinstance(kind, str) or kind not in fields:
        raise ValueError("unsupported lettering request kind")
    expected = fields[kind] | ({"role"} if kind == "native" and "role" in request else set())
    if set(request) != expected:
        raise ValueError(f"invalid {kind} lettering request fields")
    if "text" in request and not isinstance(request["text"], str):
        raise ValueError("lettering request text must be a string")
    for name in ("scale", "target", "width"):
        if name in request:
            _number(request, name, positive=True)
    if "track" in request:
        _number(request, "track")
    if "style" in request and not isinstance(request["style"], str):
        raise ValueError("lettering request style must be a string")
    if kind == "header":
        if request["raster"] not in _RASTERS:
            raise ValueError("unsupported lettering rasterizer")
        if (not isinstance(request["options"], list)
                or any(not isinstance(item, str) for item in request["options"])):
            raise ValueError("lettering request options must be strings")
    if kind == "pause" and request["counterpart"] is not None and not isinstance(request["counterpart"], str):
        raise ValueError("lettering request counterpart must be a string or null")
    if kind == "native" and request.get("role") is not None and not isinstance(request["role"], str):
        raise ValueError("lettering request role must be a string or null")
    if kind == "squish" and not isinstance(request["base"], str):
        raise ValueError("lettering request base must be a request SHA-256")
    if kind == "squish" and request["fill"] is not None:
        fill = request["fill"]
        if (not isinstance(fill, list) or len(fill) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in fill)
                or fill[1] <= fill[0]):
            raise ValueError("lettering request fill must be two finite increasing endpoints or null")


def _load_requests() -> tuple[dict[str, dict], str, frozenset[str]]:
    payload = RECIPE.read_bytes()
    recipe = json.loads(payload, object_pairs_hook=_unique)
    if not isinstance(recipe, dict) or set(recipe) != {"format_version", "requests", "metrics_only"}:
        raise ValueError("invalid lettering recipe fields")
    if type(recipe["format_version"]) is not int or recipe["format_version"] != RECIPE_VERSION:
        raise ValueError("unsupported lettering recipe format version")
    if not isinstance(recipe["requests"], dict):
        raise ValueError("lettering recipe requests must be an object")
    metrics_only = recipe["metrics_only"]
    if not isinstance(metrics_only, list) or not set(metrics_only) <= set(recipe["requests"]):
        raise ValueError("lettering recipe metrics_only names an unknown request")
    texts = _text_index()
    requests = {}
    for identity, encoded in recipe["requests"].items():
        request = _decode(encoded, texts)
        if not isinstance(request, dict):
            raise ValueError("lettering recipe request must be an object")
        _validate(request)
        if lettering.key(request) != identity:
            raise ValueError(f"lettering recipe request SHA-256 mismatch: {identity}")
        requests[identity] = request
    for request in requests.values():
        if request["kind"] == "squish":
            base = requests.get(request["base"])
            if base is None or base["kind"] in {"squish", "latin"}:
                raise ValueError("squished lettering request has no full-width base")
    return requests, hashlib.sha256(payload).hexdigest(), frozenset(metrics_only)


def write_recipe(traces: list[Path]) -> int:
    """Write the recipe from the requests a traced build recorded (WOZMOD_ART_TRACE).

    A request whose pixels no traced build drew outside a squish ships metrics only.
    """
    texts = {text: digest for digest, text in _text_index().items()}
    requests, drawn = {}, set()
    for trace in traces:
        drawn |= {path.stem for path in Path(trace).glob("*.pixels")}
        for path in sorted(Path(trace).glob("*.json")):
            request = json.loads(path.read_text(encoding="utf-8"))
            _validate(request)
            if lettering.key(request) != path.stem:
                raise ValueError(f"traced request does not match its name: {path.name}")
            encoded = dict(request)
            for name in ("text", "counterpart"):
                if isinstance(request.get(name), str):
                    if request[name] not in texts:
                        raise ValueError(f"traced {name} is not in the art recipes: {path.stem}")
                    encoded[name] = {"text_sha256": texts[request[name]]}
            requests[path.stem] = encoded
    rows = ",\n".join(f"{json.dumps(key)}: {json.dumps(requests[key], sort_keys=True)}"
                      for key in sorted(requests))
    metrics_only = ",\n".join(json.dumps(key) for key in sorted(set(requests) - drawn))
    RECIPE.write_text(f'{{"format_version": {RECIPE_VERSION}, "metrics_only": [\n{metrics_only}\n], '
                      '"requests": {\n' + rows + "\n}}\n", encoding="utf-8")
    _load_requests()
    return len(requests)


def renderer_key(python: str) -> str:
    """SHA-256 over every input of the font Python's drawing besides the request (RENDERER_SOURCES)."""
    digest = hashlib.sha256()
    for root in RENDERER_SOURCES:
        path = build.REPO / root
        for each in [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()
                                                         and "__pycache__" not in p.parts):
            digest.update(repr((each.relative_to(build.REPO).as_posix(),
                                hashlib.sha256(each.read_bytes()).hexdigest())).encode())
    setup = process.run([python, "-c", FONT_SETUP], capture_output=True, text=True, check=True)
    digest.update(repr((setup.stdout, PIL.__version__)).encode())  # Pillow here reads the drawn PNGs
    for path in SYSTEM_PACKAGES:
        if path.exists():
            digest.update(repr((str(path), path.stat().st_size, path.stat().st_mtime_ns)).encode())
    return digest.hexdigest()


class LayerCache:
    """Drawn layers kept between developer renders: root/<renderer key>/<request key>.png and .json (its metrics),
    and the glyph outlines. A file is written whole under a temporary name and then renamed, the metrics last, so a
    stopped render leaves only entries the next one redraws."""

    OUTLINES = "glyph-outlines.json"

    def __init__(self, root: Path, python: str):
        self.folder = Path(root) / renderer_key(python)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _write(self, name: str, payload: bytes) -> None:
        temporary = self.folder / f".{name}.{os.getpid()}.tmp"
        temporary.write_bytes(payload)
        os.replace(temporary, self.folder / name)

    def get(self, identity: str) -> tuple[Image.Image, dict] | None:
        metrics = self.folder / f"{identity}.json"
        if not metrics.is_file():
            return None
        with Image.open(self.folder / f"{identity}.png") as image:
            image.load()
            return image.copy(), json.loads(metrics.read_text(encoding="utf-8"))

    def put(self, identity: str, layer: tuple[Image.Image, dict]) -> None:
        image, metrics = layer
        stream = io.BytesIO()
        image.save(stream, format="PNG", compress_level=1)
        self._write(f"{identity}.png", stream.getvalue())
        self._write(f"{identity}.json", json.dumps(metrics, ensure_ascii=False, sort_keys=True).encode())

    def outlines(self, draw) -> bytes:
        path = self.folder / self.OUTLINES
        if not path.is_file():
            self._write(self.OUTLINES, draw())
        return path.read_bytes()


def _render_full(producer, request: dict, folder: Path) -> tuple[Image.Image, dict]:
    if request["kind"] != "latin":
        return producer.produce(request)
    output = folder / "latin.png"
    producer.latin(output)
    with Image.open(output) as image:
        return image.convert("L"), {}


def _render_native(producer, requests: dict[str, dict]) -> dict[str, tuple]:
    ordered = [(identity, {name: value for name, value in request.items() if name != "kind"})
               for identity, request in sorted(requests.items()) if request["kind"] == "native"]
    drawn = producer.native([request for _, request in ordered])
    return {identity: layer for (identity, _), layer in zip(ordered, drawn, strict=True)}


def render(bundle: str | Path, jobs: int | None = None, font_python: str | None = None,
           full_width: bool = False, cache: Path | None = None) -> dict:
    """Render the recipe without loading stock or donor art.

    The release bundle leaves out the pixels of layers compose only measures; full_width
    keeps every layer for the developer squish=False oracle. cache (developers only; a release renders without one)
    keeps the drawn layers for the next render, which draws only the requests the cache does not hold. jobs is the
    number of layers drawn at once (default: CPU count).
    """
    jobs = os.process_cpu_count() if jobs is None else jobs
    if type(jobs) is not int or jobs < 1:
        raise ValueError("render jobs must be a positive integer")
    started = time.perf_counter()
    requests, recipe_sha256, metrics_only = _load_requests()
    output = Path(bundle)
    output.parent.mkdir(parents=True, exist_ok=True)
    producer = lettering.PRODUCER if font_python is None else lettering.FontProcess(font_python)
    store = cache and LayerCache(cache, font_python or lettering.FONT_PYTHON)
    with tempfile.TemporaryDirectory(prefix="lettering-render-", dir=output.parent) as temporary:
        folder, layers = Path(temporary), {}
        drawn = {identity: request for identity, request in requests.items() if request["kind"] != "squish"}
        if store:
            layers = {identity: layer for identity in drawn if (layer := store.get(identity)) is not None}
            drawn = {identity: request for identity, request in drawn.items() if identity not in layers}
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_render_full, producer, request, folder): identity
                       for identity, request in drawn.items() if request["kind"] != "native"}
            native = pool.submit(_render_native, producer, drawn)
            for future in as_completed(futures):
                layers[futures[future]] = future.result()
            layers.update(native.result())
        if store:
            for identity in drawn:
                store.put(identity, layers[identity])
        for identity, request in requests.items():
            if request["kind"] != "squish":
                continue
            image, metrics = layers[request["base"]]
            metrics = {name: value for name, value in metrics.items() if name != "_lettering"}
            if request["fill"] is not None:
                metrics["fill"] = request["fill"]
            layers[identity] = lettering.condense(image, metrics, request["width"])
        layers = {identity: (image, {name: value for name, value in metrics.items()
                                    if name not in {"_lettering", "counterpart"}})
                  for identity, (image, metrics) in layers.items()}
        if set(layers) != set(requests):
            raise ValueError("rendered lettering layers do not match the recipe")
        outlines = store.outlines(lambda: producer.outlines(folder)) if store else producer.outlines(folder)
        write_bundle(output, layers, recipe_sha256, frozenset() if full_width else metrics_only, outlines=outlines)
    return {"layers": len(requests), "drawn": len(drawn), "bytes": output.stat().st_size,
            "seconds": time.perf_counter() - started}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_bundle", type=Path, nargs="?")
    parser.add_argument("--jobs", type=int, help="layers drawn at once (default: CPU count)")
    parser.add_argument("--font-python", default=lettering.FONT_PYTHON)
    parser.add_argument("--full-width", action="store_true",
                        help="developer oracle bundle: keep every full-width layer (not for release)")
    parser.add_argument("--cache", type=Path, metavar="DIR",
                        help="developers: keep drawn layers in DIR (such as work/lettering-cache) and draw only "
                             "the requests it does not hold; a release renders without one")
    parser.add_argument("--recipe-from-trace", type=Path, nargs="+", metavar="TRACE",
                        help="rewrite data/art/lettering.json from traced builds instead of rendering")
    parser.add_argument("--text-donors", type=Path, default=os.environ.get("WOZMOD_DONORS"),
                        help="the installer's donors folder the menu words resolve from (see wozmod.art.words)")
    args = parser.parse_args(argv)
    words.use(args.text_donors)
    if args.recipe_from_trace:
        print(json.dumps({"requests": write_recipe(args.recipe_from_trace)}))
        return 0
    if args.output_bundle is None:
        parser.error("give the output bundle path")
    result = render(args.output_bundle, jobs=args.jobs, font_python=args.font_python,
                    full_width=args.full_width, cache=args.cache)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
