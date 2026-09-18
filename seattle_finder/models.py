from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    id: str
    name: str
    source: str
    search_text: str
    total_population: int
    target_population: int
    target_share: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Commute:
    office_name: str
    office_address: str
    duration_minutes: float | None
    distance_miles: float | None
    status: str
    route_url: str | None = None
    morning_duration_minutes: float | None = None
    morning_distance_miles: float | None = None
    morning_toll_cost: float | None = None
    morning_fuel_cost: float | None = None
    morning_total_cost: float | None = None
    morning_departure_time: str | None = None
    morning_arrival_time: str | None = None
    morning_status: str | None = None
    evening_duration_minutes: float | None = None
    evening_distance_miles: float | None = None
    evening_toll_cost: float | None = None
    evening_fuel_cost: float | None = None
    evening_total_cost: float | None = None
    evening_departure_time: str | None = None
    evening_status: str | None = None
    daily_distance_miles: float | None = None
    daily_toll_cost: float | None = None
    daily_fuel_cost: float | None = None
    daily_total_cost: float | None = None
