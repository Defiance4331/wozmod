"""Read and write the rendered lettering layer bundle."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from PIL import Image
from PIL._util import DeferredError

FORMAT_VERSION = 2
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MODES = {"L", "LA", "RGB", "RGBA"}
_INDEX_FIELDS = {"format_version", "recipe_sha256", "layers"}
_LAYER_FIELDS = {"key", "path", "sha256", "mode", "size", "metrics"}


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"invalid {name} SHA-256")
    return value


class MetricsOnly(Image.Image):
    """A layer shipped without pixels: size and mode only, any pixel read fails."""

    def __init__(self, key: str, mode: str, size: tuple[int, int]) -> None:
        super().__init__()
        self._mode, self._size = mode, size
        self._im = DeferredError(ValueError(f"lettering layer {key} was shipped without pixels"))
        self._key = key

    def load(self):
        raise ValueError(f"lettering layer {self._key} was shipped without pixels")

    def copy(self) -> "MetricsOnly":
        return self


def _layer_path(key: str) -> str:
    return f"layers/{hashlib.sha256(key.encode('utf-8')).hexdigest()}.png"


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as error:
        raise ValueError("bundle metadata must contain finite JSON values") from error


def _metrics(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("layer metrics must be an object")
    def check_keys(item: object) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("layer metrics keys must be strings")
            for child in item.values():
                check_keys(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check_keys(child)
    check_keys(value)
    return json.loads(_json_bytes(value))


def _zip_member(name: str) -> zipfile.ZipInfo:
    member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    member.compress_type = zipfile.ZIP_STORED
    member.create_system = 3
    member.external_attr = 0o100644 << 16
    return member


def write_bundle(path: str | Path, layers: dict[str, tuple[Image.Image, dict]],
                 recipe_sha256: str, metrics_only: frozenset[str] = frozenset()) -> None:
    """Write deterministic PNG layers and their checked index; metrics_only keys get no PNG."""
    _digest(recipe_sha256, "recipe")
    if not isinstance(layers, dict) or any(not isinstance(key, str) or not key for key in layers):
        raise ValueError("layer keys must be nonempty strings")
    rows = []
    payloads = []
    for key in sorted(layers):
        image, metrics = layers[key]
        if not isinstance(image, Image.Image) or image.mode not in _MODES:
            raise ValueError(f"unsupported layer image mode for {key}")
        if min(image.size) <= 0:
            raise ValueError(f"invalid layer image size for {key}")
        row = {"key": key, "path": None, "sha256": None, "mode": image.mode,
               "size": list(image.size), "metrics": _metrics(metrics)}
        if key not in metrics_only:
            clean = Image.frombytes(image.mode, image.size, image.tobytes())
            stream = io.BytesIO()
            clean.save(stream, format="PNG", optimize=False, compress_level=9)
            payload = stream.getvalue()
            row["path"], row["sha256"] = _layer_path(key), hashlib.sha256(payload).hexdigest()
            payloads.append((row["path"], payload))
        rows.append(row)
    index = _json_bytes({"format_version": FORMAT_VERSION,
                         "recipe_sha256": recipe_sha256, "layers": rows})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(_zip_member("index.json"), index)
        for name, payload in payloads:
            archive.writestr(_zip_member(name), payload)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_index(payload: bytes) -> dict:
    try:
        index = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid bundle index JSON") from error
    if not isinstance(index, dict) or set(index) != _INDEX_FIELDS:
        raise ValueError("invalid bundle index fields")
    if type(index["format_version"]) is not int or index["format_version"] != FORMAT_VERSION:
        raise ValueError("unsupported bundle format version")
    _digest(index["recipe_sha256"], "recipe")
    if not isinstance(index["layers"], list):
        raise ValueError("bundle layers must be an array")
    return index


def read_bundle(path: str | Path, expected_recipe_sha256: str | None = None
                ) -> dict[str, tuple[Image.Image, dict]]:
    """Read a bundle after validating all names, hashes and image declarations."""
    if expected_recipe_sha256 is not None:
        _digest(expected_recipe_sha256, "expected recipe")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("duplicate bundle member")
            if "index.json" not in names:
                raise ValueError("missing bundle index")
            index = _read_index(archive.read("index.json"))
            if (expected_recipe_sha256 is not None
                    and index["recipe_sha256"] != expected_recipe_sha256):
                raise ValueError("bundle recipe SHA-256 mismatch")
            declared = {"index.json"}
            rows = {}
            for row in index["layers"]:
                if not isinstance(row, dict) or set(row) != _LAYER_FIELDS:
                    raise ValueError("invalid layer index fields")
                key = row["key"]
                if not isinstance(key, str) or not key or key in rows:
                    raise ValueError("invalid or duplicate layer key")
                if row["path"] is None and row["sha256"] is None:
                    pass
                elif row["path"] != _layer_path(key):
                    raise ValueError(f"misnamed layer: {key}")
                else:
                    _digest(row["sha256"], "layer")
                if not isinstance(row["mode"], str) or row["mode"] not in _MODES:
                    raise ValueError(f"unsupported layer image mode: {key}")
                size = row["size"]
                if (not isinstance(size, list) or len(size) != 2
                        or any(type(value) is not int or value <= 0 for value in size)):
                    raise ValueError(f"invalid layer image size: {key}")
                row["metrics"] = _metrics(row["metrics"])
                rows[key] = row
                if row["path"] is not None:
                    declared.add(row["path"])
            if set(names) != declared:
                raise ValueError("bundle members do not match declared layers")
            result = {}
            for key, row in rows.items():
                if row["path"] is None:
                    result[key] = MetricsOnly(key, row["mode"], tuple(row["size"])), row["metrics"]
                    continue
                payload = archive.read(row["path"])
                if hashlib.sha256(payload).hexdigest() != row["sha256"]:
                    raise ValueError(f"layer SHA-256 mismatch: {key}")
                with Image.open(io.BytesIO(payload)) as opened:
                    if opened.format != "PNG":
                        raise ValueError(f"layer is not a PNG: {key}")
                    opened.load()
                    if opened.mode != row["mode"] or list(opened.size) != row["size"]:
                        raise ValueError(f"layer mode or size mismatch: {key}")
                    result[key] = opened.copy(), row["metrics"]
            return result
    except (zipfile.BadZipFile, RuntimeError, OSError) as error:
        raise ValueError("invalid lettering bundle") from error
