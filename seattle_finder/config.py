from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "data" / "default_config.json"
ENV_PATH = ROOT / ".env"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def default_config() -> dict[str, Any]:
    load_env_file()
    return load_json(DEFAULT_CONFIG_PATH)


def google_server_key() -> str | None:
    load_env_file()
    return os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
