"""The paths the setup window and the terminal ask for, remembered in the player's config folder."""

from __future__ import annotations

import json
import os
from pathlib import Path


def settings_path() -> Path:
    base = os.environ.get("APPDATA") if os.name == "nt" else None
    base = Path(base) if base else Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "wozmod" / "setup.json"


def load_settings() -> dict[str, str]:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_settings(values: dict[str, str]) -> None:
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, indent=1), encoding="utf-8")
    except OSError:
        pass  # a settings file that cannot be written only means the paths are not remembered
