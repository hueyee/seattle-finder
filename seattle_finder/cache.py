from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, cache_dir: Path, ttl_seconds: int = 60 * 60 * 24 * 14) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        if time.time() - payload.get("stored_at", 0) > self.ttl_seconds:
            return None
        return payload.get("value")

    def set(self, key: str, value: Any) -> None:
        path = self._path(key)
        payload = {"stored_at": time.time(), "value": value}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))

