from __future__ import annotations

import urllib.parse
from typing import Any

from .http_client import HttpClient
from .models import Commute


ROUTES_ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"
ROUTES_FIELD_MASK = "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"


def directions_url(origin: str, destination: str) -> str:
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(
        {"api": "1", "origin": origin, "destination": destination, "travelmode": "driving"}
    )


class GoogleMapsClient:
    def __init__(self, http: HttpClient, api_key: str | None) -> None:
        self.http = http
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def commute(self, origin: str, destination: str, office_name: str) -> tuple[Commute, dict[str, Any] | None]:
        if not self.api_key:
            return (
                Commute(
                    office_name=office_name,
                    office_address=destination,
                    duration_minutes=None,
                    distance_miles=None,
                    status="missing_google_maps_api_key",
                    route_url=directions_url(origin, destination),
                ),
                None,
            )

        try:
            payload = self.http.post_json(
                ROUTES_ENDPOINT,
                {
                    "origin": {"address": origin},
                    "destination": {"address": destination},
                    "travelMode": "DRIVE",
                    "routingPreference": "TRAFFIC_AWARE",
                    "computeAlternativeRoutes": False,
                    "routeModifiers": {
                        "avoidTolls": False,
                        "avoidHighways": False,
                        "avoidFerries": False,
                    },
                    "languageCode": "en-US",
                    "regionCode": "us",
                    "units": "IMPERIAL",
                },
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": ROUTES_FIELD_MASK,
                },
            )
        except Exception as exc:
            return (
                Commute(
                    office_name=office_name,
                    office_address=destination,
                    duration_minutes=None,
                    distance_miles=None,
                    status=f"routes_request_failed: {exc}",
                    route_url=directions_url(origin, destination),
                ),
                None,
            )
        status = _routes_status(payload)
        if status != "OK" or not payload.get("routes"):
            return (
                Commute(
                    office_name=office_name,
                    office_address=destination,
                    duration_minutes=None,
                    distance_miles=None,
                    status=status,
                    route_url=directions_url(origin, destination),
                ),
                None,
            )

        route = payload["routes"][0]
        duration_minutes = _duration_to_minutes(route.get("duration"))
        distance_miles = round(float(route.get("distanceMeters", 0)) / 1609.344, 1)
        location = _first_polyline_point(route.get("polyline", {}).get("encodedPolyline"))
        return (
            Commute(
                office_name=office_name,
                office_address=destination,
                duration_minutes=duration_minutes,
                distance_miles=distance_miles,
                status=status,
                route_url=directions_url(origin, destination),
            ),
            location,
        )


def _routes_status(payload: dict[str, Any]) -> str:
    if payload.get("routes"):
        return "OK"
    if "error" in payload and isinstance(payload["error"], dict):
        return payload["error"].get("status") or payload["error"].get("message") or "ROUTES_ERROR"
    return str(payload.get("status") or payload.get("http_status") or "ZERO_RESULTS")


def _duration_to_minutes(duration: str | None) -> float | None:
    if not duration or not duration.endswith("s"):
        return None
    try:
        return round(float(duration[:-1]) / 60, 1)
    except ValueError:
        return None


def _first_polyline_point(encoded: str | None) -> dict[str, float] | None:
    points = decode_polyline(encoded or "", limit=1)
    if not points:
        return None
    lat, lng = points[0]
    return {"lat": lat, "lng": lng}


def decode_polyline(encoded: str, limit: int | None = None) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lng = 0

    while index < len(encoded):
        lat_delta, index = _decode_polyline_value(encoded, index)
        lng_delta, index = _decode_polyline_value(encoded, index)
        lat += lat_delta
        lng += lng_delta
        points.append((lat / 100000.0, lng / 100000.0))
        if limit is not None and len(points) >= limit:
            break
    return points


def _decode_polyline_value(encoded: str, index: int) -> tuple[int, int]:
    result = 0
    shift = 0

    while True:
        byte = ord(encoded[index]) - 63
        index += 1
        result |= (byte & 0x1F) << shift
        shift += 5
        if byte < 0x20:
            break

    value = ~(result >> 1) if result & 1 else result >> 1
    return value, index
