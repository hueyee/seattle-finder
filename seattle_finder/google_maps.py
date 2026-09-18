from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timedelta
import urllib.parse
from zoneinfo import ZoneInfo
from typing import Any

from .http_client import HttpClient
from .models import Commute


ROUTES_ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"
ROUTES_FIELD_MASK = (
    "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,"
    "routes.travelAdvisory.tollInfo"
)


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

    def commute(
        self,
        origin: str,
        destination: str,
        office_name: str,
        config: dict[str, Any] | None = None,
    ) -> tuple[Commute, dict[str, Any] | None]:
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

        options = CommuteOptions.from_config(config or {})
        morning = self._route_for_arrival(origin, destination, options)
        evening = self._route(destination, origin, options.evening_departure, options)
        statuses = [leg.status for leg in (morning, evening) if leg.status != "OK"]
        status = "OK" if not statuses else "; ".join(statuses)

        if not morning.ok and not evening.ok:
            return (
                Commute(
                    office_name=office_name,
                    office_address=destination,
                    duration_minutes=None,
                    distance_miles=None,
                    status=status,
                    route_url=directions_url(origin, destination),
                    morning_status=morning.status,
                    evening_status=evening.status,
                ),
                None,
            )

        one_way_durations = [leg.duration_minutes for leg in (morning, evening) if leg.duration_minutes is not None]
        one_way_distances = [leg.distance_miles for leg in (morning, evening) if leg.distance_miles is not None]
        duration_minutes = round(sum(one_way_durations) / len(one_way_durations), 1) if one_way_durations else None
        distance_miles = round(sum(one_way_distances) / len(one_way_distances), 1) if one_way_distances else None
        daily_distance_miles = _sum_present(morning.distance_miles, evening.distance_miles)
        daily_toll_cost = _sum_present(morning.toll_cost, evening.toll_cost)
        daily_fuel_cost = _sum_present(morning.fuel_cost, evening.fuel_cost)
        daily_total_cost = _sum_present(morning.total_cost, evening.total_cost)
        location = morning.location or evening.location
        return (
            Commute(
                office_name=office_name,
                office_address=destination,
                duration_minutes=duration_minutes,
                distance_miles=distance_miles,
                status=status,
                route_url=directions_url(origin, destination),
                morning_duration_minutes=morning.duration_minutes,
                morning_distance_miles=morning.distance_miles,
                morning_toll_cost=morning.toll_cost,
                morning_fuel_cost=morning.fuel_cost,
                morning_total_cost=morning.total_cost,
                morning_departure_time=_iso_or_none(morning.departure_time),
                morning_arrival_time=_iso_or_none(options.morning_arrival),
                morning_status=morning.status,
                evening_duration_minutes=evening.duration_minutes,
                evening_distance_miles=evening.distance_miles,
                evening_toll_cost=evening.toll_cost,
                evening_fuel_cost=evening.fuel_cost,
                evening_total_cost=evening.total_cost,
                evening_departure_time=_iso_or_none(options.evening_departure),
                evening_status=evening.status,
                daily_distance_miles=daily_distance_miles,
                daily_toll_cost=daily_toll_cost,
                daily_fuel_cost=daily_fuel_cost,
                daily_total_cost=daily_total_cost,
            ),
            location,
        )

    def _route_for_arrival(self, origin: str, destination: str, options: "CommuteOptions") -> "RouteLeg":
        departure = options.morning_arrival - timedelta(minutes=30)
        leg = self._route(origin, destination, departure, options)
        for _ in range(2):
            if leg.duration_minutes is None:
                return leg
            next_departure = options.morning_arrival - timedelta(minutes=leg.duration_minutes)
            if abs((next_departure - departure).total_seconds()) < 60:
                return leg
            departure = next_departure
            leg = self._route(origin, destination, departure, options)
        return leg

    def _route(
        self,
        origin: str,
        destination: str,
        departure_time: datetime,
        options: "CommuteOptions",
    ) -> "RouteLeg":
        try:
            payload = self.http.post_json(
                ROUTES_ENDPOINT,
                {
                    "origin": {"address": origin},
                    "destination": {"address": destination},
                    "travelMode": "DRIVE",
                    "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
                    "trafficModel": "BEST_GUESS",
                    "departureTime": departure_time.isoformat(),
                    "computeAlternativeRoutes": False,
                    "extraComputations": ["TOLLS"],
                    "routeModifiers": {
                        "avoidTolls": False,
                        "avoidHighways": False,
                        "avoidFerries": False,
                        "vehicleInfo": {"emissionType": options.emission_type},
                        "tollPasses": options.toll_passes,
                    },
                    "languageCode": "en-US",
                    "regionCode": "us",
                    "units": "IMPERIAL",
                },
                headers={
                    "X-Goog-Api-Key": self.api_key or "",
                    "X-Goog-FieldMask": ROUTES_FIELD_MASK,
                },
            )
        except Exception as exc:
            return RouteLeg(status=f"routes_request_failed: {exc}", departure_time=departure_time)

        status = _routes_status(payload)
        if status != "OK" or not payload.get("routes"):
            return RouteLeg(status=status, departure_time=departure_time)

        route = payload["routes"][0]
        distance_miles = round(float(route.get("distanceMeters", 0)) / 1609.344, 1)
        toll_cost = _toll_cost(route)
        fuel_cost = round((distance_miles / options.miles_per_gallon) * options.fuel_price_per_gallon, 2)
        total_cost = round(fuel_cost + (toll_cost or 0), 2)
        return RouteLeg(
            status="OK",
            duration_minutes=_duration_to_minutes(route.get("duration")),
            distance_miles=distance_miles,
            toll_cost=toll_cost,
            fuel_cost=fuel_cost,
            total_cost=total_cost,
            location=_first_polyline_point(route.get("polyline", {}).get("encodedPolyline")),
            departure_time=departure_time,
        )


@dataclass
class CommuteOptions:
    morning_arrival: datetime
    evening_departure: datetime
    miles_per_gallon: float
    fuel_price_per_gallon: float
    toll_passes: list[str]
    emission_type: str

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CommuteOptions":
        schedule = config.get("commute_schedule", {})
        vehicle = config.get("vehicle", {})
        timezone = ZoneInfo(schedule.get("timezone", "America/Los_Angeles"))
        commute_date = _next_commute_date(timezone)
        return cls(
            morning_arrival=_combine_local(commute_date, schedule.get("morning_arrival_time", "09:00"), timezone),
            evening_departure=_combine_local(commute_date, schedule.get("evening_departure_time", "17:00"), timezone),
            miles_per_gallon=max(1, float(vehicle.get("miles_per_gallon", 15))),
            fuel_price_per_gallon=float(vehicle.get("fuel_price_per_gallon", cls.default_fuel_price())),
            toll_passes=_list_value(vehicle.get("toll_passes", ["US_WA_GOOD_TO_GO"])),
            emission_type=str(vehicle.get("emission_type", "GASOLINE")),
        )

    @staticmethod
    def default_fuel_price() -> float:
        return 5.0


@dataclass
class RouteLeg:
    status: str
    duration_minutes: float | None = None
    distance_miles: float | None = None
    toll_cost: float | None = None
    fuel_cost: float | None = None
    total_cost: float | None = None
    location: dict[str, float] | None = None
    departure_time: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def _routes_status(payload: dict[str, Any]) -> str:
    if payload.get("routes"):
        return "OK"
    if "error" in payload and isinstance(payload["error"], dict):
        return payload["error"].get("status") or payload["error"].get("message") or "ROUTES_ERROR"
    return str(payload.get("status") or payload.get("http_status") or "ZERO_RESULTS")


def _next_commute_date(timezone: ZoneInfo) -> date:
    now = datetime.now(timezone)
    candidate = now.date() + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _combine_local(commute_date: date, text_time: str, timezone: ZoneInfo) -> datetime:
    hour_text, minute_text = text_time.split(":", 1)
    return datetime(
        commute_date.year,
        commute_date.month,
        commute_date.day,
        int(hour_text),
        int(minute_text),
        tzinfo=timezone,
    )


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _toll_cost(route: dict[str, Any]) -> float | None:
    estimated_prices = (
        route.get("travelAdvisory", {})
        .get("tollInfo", {})
        .get("estimatedPrice", [])
    )
    usd_prices = [price for price in estimated_prices if price.get("currencyCode") == "USD"]
    if not usd_prices:
        return None
    return round(sum(_money_to_float(price) for price in usd_prices), 2)


def _money_to_float(price: dict[str, Any]) -> float:
    units = float(price.get("units", 0))
    nanos = float(price.get("nanos", 0)) / 1_000_000_000
    return units + nanos


def _sum_present(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(sum(present), 2)


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


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
