from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
import urllib.error
from typing import Any

from .cache import JsonCache


class HttpClient:
    def __init__(self, cache: JsonCache, timeout_seconds: int = 25) -> None:
        self.cache = cache
        self.timeout_seconds = timeout_seconds

    def get_json(self, url: str, params: dict[str, Any] | None = None, use_cache: bool = True) -> Any:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        full_url = f"{url}?{query}" if query else url
        cache_key = f"GET {full_url}"

        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        request = urllib.request.Request(full_url, headers={"User-Agent": "seattle-finder/0.1"})
        data = self._open_json(request, full_url)

        if use_cache:
            self.cache.set(cache_key, data)
        return data

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> Any:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        cache_headers = {
            key: value
            for key, value in (headers or {}).items()
            if key.lower() != "x-goog-api-key"
        }
        cache_key = f"POST:v2 {url} {json.dumps(cache_headers, sort_keys=True)} {body.decode('utf-8')}"

        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        request_headers = {
            "Content-Type": "application/json",
            "User-Agent": "seattle-finder/0.1",
            **(headers or {}),
        }
        request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
        data = self._open_json(request, url)

        if use_cache:
            self.cache.set(cache_key, data)
        return data

    def _open_json(self, request: urllib.request.Request, source_url: str) -> Any:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return _decode_json(response.read().decode("utf-8"), source_url)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            data = _decode_json(text, source_url)
            if isinstance(data, dict):
                data.setdefault("http_status", exc.code)
                return data
            return {"http_status": exc.code, "error": data}
        except urllib.error.URLError as exc:
            if not _is_certificate_error(exc):
                raise
            fallback_context = ssl._create_unverified_context()
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds, context=fallback_context
            ) as response:
                return _decode_json(response.read().decode("utf-8"), source_url)


def _is_certificate_error(exc: urllib.error.URLError) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _decode_json(text: str, url: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:160].replace("\n", " ")
        raise ValueError(f"Expected JSON from {url}, received: {snippet or '<empty response>'}") from exc
