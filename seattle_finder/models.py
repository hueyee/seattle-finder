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

