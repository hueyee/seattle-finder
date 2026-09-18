from __future__ import annotations

import os
import re
from typing import Any, Iterable

from .http_client import HttpClient
from .models import Candidate


CENSUS_ENDPOINT = "https://api.census.gov/data/2024/acs/acs5"
SEATTLE_ARCGIS_ENDPOINT = (
    "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/"
    "demographics_basic_age_sex_Neighborhoods/FeatureServer/0/query"
)

AGE_BUCKETS = [
    ("B01001_007E", 18, 19),
    ("B01001_008E", 20, 20),
    ("B01001_009E", 21, 21),
    ("B01001_010E", 22, 24),
    ("B01001_011E", 25, 29),
    ("B01001_012E", 30, 34),
    ("B01001_013E", 35, 39),
    ("B01001_031E", 18, 19),
    ("B01001_032E", 20, 20),
    ("B01001_033E", 21, 21),
    ("B01001_034E", 22, 24),
    ("B01001_035E", 25, 29),
    ("B01001_036E", 30, 34),
    ("B01001_037E", 35, 39),
]

LEGACY_TARGET_AGE_VARIABLES = {
    "18-19": ["B01001_007E", "B01001_031E"],
    "20-24": ["B01001_008E", "B01001_009E", "B01001_010E", "B01001_032E", "B01001_033E", "B01001_034E"],
    "25-29": ["B01001_011E", "B01001_035E"],
    "30-34": ["B01001_012E", "B01001_036E"],
    "35-39": ["B01001_013E", "B01001_037E"],
}


def slug(value: str) -> str:
    normalized = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return normalized or "unknown"


def selected_variables(age_bands: Iterable[str]) -> list[str]:
    variables: list[str] = []
    for band in age_bands:
        variables.extend(LEGACY_TARGET_AGE_VARIABLES.get(band, []))
    return sorted(set(variables))


def target_age_range(config: dict[str, Any]) -> tuple[int, int]:
    if "target_age_min" in config and "target_age_max" in config:
        age_min = int(config["target_age_min"])
        age_max = int(config["target_age_max"])
        return min(age_min, age_max), max(age_min, age_max)

    age_bands = config.get("age_bands", ["24-29"])
    bounds: list[int] = []
    for band in age_bands:
        for value in str(band).split("-", 1):
            if value.isdigit():
                bounds.append(int(value))
    return (min(bounds), max(bounds)) if bounds else (24, 29)


def variables_for_age_range(age_min: int, age_max: int) -> list[str]:
    return sorted(
        {
            variable
            for variable, bucket_min, bucket_max in AGE_BUCKETS
            if _overlap_years(age_min, age_max, bucket_min, bucket_max) > 0
        }
    )


def target_population(record: dict[str, Any], age_min: int, age_max: int) -> int:
    total = 0.0
    for variable, bucket_min, bucket_max in AGE_BUCKETS:
        overlap = _overlap_years(age_min, age_max, bucket_min, bucket_max)
        if overlap <= 0:
            continue
        bucket_years = bucket_max - bucket_min + 1
        total += _safe_int(record.get(variable)) * (overlap / bucket_years)
    return round(total)


def _overlap_years(age_min: int, age_max: int, bucket_min: int, bucket_max: int) -> int:
    start = max(age_min, bucket_min)
    end = min(age_max, bucket_max)
    return max(0, end - start + 1)


def _safe_int(value: Any) -> int:
    try:
        if value in (None, "", "-666666666"):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _share(target: int, total: int) -> float:
    return target / total if total > 0 else 0


def _clean_census_place_name(name: str) -> str:
    return name.replace(", Washington", "").replace(" city", "").replace(" CDP", "").strip()


def fetch_census_places(http: HttpClient, config: dict[str, Any]) -> list[Candidate]:
    age_min, age_max = target_age_range(config)
    age_vars = variables_for_age_range(age_min, age_max)
    fields = ["NAME", "B01001_001E", *age_vars]
    params = {
        "get": ",".join(fields),
        "for": "place:*",
        "in": "state:53",
    }
    census_key = os.environ.get("CENSUS_API_KEY")
    if census_key:
        params["key"] = census_key

    try:
        payload = http.get_json(CENSUS_ENDPOINT, params)
    except Exception as exc:
        config.setdefault("warnings", []).append(f"Census place data skipped: {exc}")
        return []

    if not payload or len(payload) < 2:
        return []

    headers = payload[0]
    records = [dict(zip(headers, row)) for row in payload[1:]]
    include_names = {name.lower() for name in config.get("candidate_places", [])}
    min_population = int(config.get("min_population", 0))

    candidates: list[Candidate] = []
    for record in records:
        display_name = _clean_census_place_name(record["NAME"])
        if include_names and display_name.lower() not in include_names:
            continue

        total = _safe_int(record.get("B01001_001E"))
        if total < min_population:
            continue

        target = target_population(record, age_min, age_max)
        candidates.append(
            Candidate(
                id=f"place-{slug(display_name)}",
                name=display_name,
                source="Census ACS 2024 place",
                search_text=f"{display_name}, WA",
                total_population=total,
                target_population=target,
                target_share=_share(target, total),
                metadata={"geography": "place"},
            )
        )
    return candidates


def fetch_seattle_neighborhoods(http: HttpClient, config: dict[str, Any]) -> list[Candidate]:
    if not config.get("include_seattle_neighborhoods", True):
        return []

    age_min, age_max = target_age_range(config)
    age_vars = variables_for_age_range(age_min, age_max)
    out_fields = ["NEIGH_NAME", "NEIGH_TYPE", "NEIGH_SUB_TYPE", "ACS_VINTAGE", "B01001_001E", *age_vars]
    where = f"ACS_VINTAGE = '{config.get('seattle_acs_vintage', '5Y24')}'"
    try:
        payload = http.get_json(
            SEATTLE_ARCGIS_ENDPOINT,
            {
                "where": where,
                "outFields": ",".join(out_fields),
                "returnGeometry": "false",
                "f": "json",
            },
        )
    except Exception as exc:
        config.setdefault("warnings", []).append(f"Seattle neighborhood data skipped: {exc}")
        return []

    candidates: list[Candidate] = []
    allowed_types = set(config.get("seattle_neighborhood_types", ["CRA", "CNTR"]))
    excluded_subtypes = set(config.get("excluded_seattle_neighborhood_subtypes", []))
    for feature in payload.get("features", []):
        attrs = feature.get("attributes", {})
        name = attrs.get("NEIGH_NAME")
        if not name:
            continue
        if allowed_types and attrs.get("NEIGH_TYPE") not in allowed_types:
            continue
        if attrs.get("NEIGH_SUB_TYPE") in excluded_subtypes:
            continue

        total = _safe_int(attrs.get("B01001_001E"))
        if total < int(config.get("min_neighborhood_population", 500)):
            continue

        target = target_population(attrs, age_min, age_max)
        candidates.append(
            Candidate(
                id=f"seattle-neighborhood-{slug(name)}",
                name=f"{name}, Seattle",
                source="Seattle neighborhood ACS 2024",
                search_text=f"{name}, Seattle, WA",
                total_population=total,
                target_population=target,
                target_share=_share(target, total),
                metadata={
                    "geography": "seattle_neighborhood",
                    "acs_vintage": attrs.get("ACS_VINTAGE"),
                    "neighborhood_type": attrs.get("NEIGH_TYPE"),
                    "neighborhood_sub_type": attrs.get("NEIGH_SUB_TYPE"),
                },
            )
        )
    return candidates


def load_candidates(http: HttpClient, config: dict[str, Any]) -> list[Candidate]:
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for candidate in [*fetch_census_places(http, config), *fetch_seattle_neighborhoods(http, config)]:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        candidates.append(candidate)
    return candidates
