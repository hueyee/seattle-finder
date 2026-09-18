from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from .config import ROOT
from .demographics import _clean_census_place_name, _safe_int, slug
from .http_client import HttpClient
from .models import Candidate


CENSUS_ENDPOINT = "https://api.census.gov/data/2024/acs/acs5"
SEATTLE_RENT_TYPOLOGY_ENDPOINT = (
    "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/"
    "RentTypology/FeatureServer/0/query"
)

MISSING_RENT_SOURCE = "missing"


@dataclass(frozen=True)
class RentEstimate:
    monthly_rent_1br: float
    source: str
    period: str | None = None
    estimate_kind: str = "median_1br_rent"


def load_rent_estimates(
    http: HttpClient,
    candidates: Iterable[Candidate],
    config: dict[str, Any],
) -> dict[str, RentEstimate]:
    rent_config = config.get("rent", {})
    if not rent_config.get("enabled", True):
        return {}

    candidate_list = list(candidates)
    estimates: dict[str, RentEstimate] = {}

    apartment_list_path = _configured_path(
        rent_config.get("apartment_list_csv", "data/apartment_list_rent_estimates.csv")
    )
    if apartment_list_path.exists():
        estimates.update(_match_apartment_list_estimates(candidate_list, apartment_list_path))
    else:
        config.setdefault("warnings", []).append(
            f"Apartment List rent CSV not found at {apartment_list_path}; using public fallbacks."
        )

    missing_after_apartment_list = [candidate for candidate in candidate_list if candidate.id not in estimates]
    if missing_after_apartment_list:
        estimates.update(_fetch_acs_place_rents(http, missing_after_apartment_list, config))

    missing_after_acs = [candidate for candidate in candidate_list if candidate.id not in estimates]
    if missing_after_acs:
        estimates.update(_fetch_seattle_typology_rents(http, missing_after_acs, config))

    return estimates


def _configured_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _match_apartment_list_estimates(candidates: list[Candidate], path: Path) -> dict[str, RentEstimate]:
    rows = _read_csv_rows(path)
    by_city: dict[str, RentEstimate] = {}
    for row in rows:
        state = _row_value(row, "state", "state_name", "state_abbreviation")
        if state and state.strip().lower() not in {"wa", "washington"}:
            continue

        location_type = _row_value(row, "location_type", "type", "geo_type", "geography")
        if location_type and location_type.strip().lower() not in {"city", "place", "municipality"}:
            continue

        city = _row_value(row, "location_name", "location", "city", "name", "area_name", "region_name")
        rent, period = _apartment_list_1br_value(row)
        if not city or rent is None:
            continue

        by_city[_name_key(city)] = RentEstimate(
            monthly_rent_1br=rent,
            source="Apartment List median 1BR rent",
            period=period,
        )

    matches: dict[str, RentEstimate] = {}
    for candidate in candidates:
        estimate = by_city.get(_apartment_list_candidate_key(candidate))
        if estimate is not None:
            matches[candidate.id] = estimate
    return matches


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    except UnicodeDecodeError:
        with path.open(newline="", encoding="latin-1") as handle:
            return list(csv.DictReader(handle))


def _row_value(row: dict[str, str], *names: str) -> str | None:
    normalized = {_column_key(key): value for key, value in row.items() if key is not None}
    for name in names:
        value = normalized.get(_column_key(name))
        if value not in (None, ""):
            return value
    return None


def _column_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _apartment_list_1br_value(row: dict[str, str]) -> tuple[float | None, str | None]:
    bed_size = _row_value(row, "bed_size", "bedrooms", "bedroom_size", "unit_size")
    if bed_size and "1" not in bed_size and "one" not in bed_size.lower():
        return None, None

    direct_names = [
        "rent_1br",
        "one_bedroom_rent",
        "one_bedroom",
        "1br_rent",
        "1_bedroom_rent",
        "bedroom_1",
        "price_1br",
        "median_1br_rent",
        "1br",
    ]
    for name in direct_names:
        value = _to_money(_row_value(row, name))
        if value is not None:
            return value, _row_value(row, "month", "date", "period", "report_month", "year_month")

    candidates: list[tuple[str, float]] = []
    for key, value in row.items():
        column = _column_key(key)
        if _looks_like_1br_rent_column(column):
            rent = _to_money(value)
            if rent is not None:
                candidates.append((key, rent))

    if candidates:
        key, rent = _latest_named_value(candidates)
        return rent, _period_from_column(key) or _row_value(row, "month", "date", "period", "report_month", "year_month")

    if bed_size:
        monthly_values: list[tuple[str, float]] = []
        for key, value in row.items():
            if _looks_like_monthly_price_column(key):
                rent = _to_money(value)
                if rent is not None:
                    monthly_values.append((key, rent))
        if monthly_values:
            key, rent = _latest_named_value(monthly_values)
            return rent, _period_from_column(key)

    return None, None


def _looks_like_1br_rent_column(column: str) -> bool:
    if any(term in column for term in ("growth", "change", "mom", "yoy", "vacancy")):
        return False
    has_one = re.search(r"(^|_)1($|_)", column) or "1br" in column or "one_bed" in column
    has_bed = "br" in column or "bed" in column
    has_rent = "rent" in column or "price" in column or "bedroom" in column
    return bool(has_one and has_bed and has_rent)


def _looks_like_monthly_price_column(column: str) -> bool:
    normalized = _column_key(column)
    if any(term in normalized for term in ("growth", "change", "mom", "yoy", "vacancy")):
        return False
    return bool(re.search(r"(19|20)\d{2}[_-]?(0[1-9]|1[0-2])", normalized))


def _latest_named_value(values: list[tuple[str, float]]) -> tuple[str, float]:
    return max(values, key=lambda item: _period_sort_key(item[0]))


def _period_sort_key(value: str) -> tuple[int, int, str]:
    match = re.search(r"((?:19|20)\d{2})[^0-9]?(0[1-9]|1[0-2])?", value)
    if not match:
        return (0, 0, value)
    return (int(match.group(1)), int(match.group(2) or 0), value)


def _period_from_column(value: str) -> str | None:
    match = re.search(r"((?:19|20)\d{2})[^0-9]?(0[1-9]|1[0-2])?", value)
    if not match:
        return None
    if match.group(2):
        return f"{match.group(1)}-{match.group(2)}"
    return match.group(1)


def _fetch_acs_place_rents(
    http: HttpClient,
    candidates: list[Candidate],
    config: dict[str, Any],
) -> dict[str, RentEstimate]:
    params = {
        "get": "NAME,B25031_003E",
        "for": "place:*",
        "in": "state:53",
    }
    census_key = os.environ.get("CENSUS_API_KEY")
    if census_key:
        params["key"] = census_key

    try:
        payload = http.get_json(CENSUS_ENDPOINT, params)
    except Exception as exc:
        config.setdefault("warnings", []).append(f"ACS 1BR rent data skipped: {exc}")
        return {}

    if not payload or len(payload) < 2:
        return {}

    headers = payload[0]
    rows = [dict(zip(headers, row)) for row in payload[1:]]
    by_place = {
        _name_key(_clean_census_place_name(row.get("NAME", ""))): _safe_rent(row.get("B25031_003E"))
        for row in rows
    }

    estimates: dict[str, RentEstimate] = {}
    for candidate in candidates:
        if candidate.metadata.get("geography") != "place":
            continue
        rent = by_place.get(_candidate_place_key(candidate))
        if rent is None:
            continue
        estimates[candidate.id] = RentEstimate(
            monthly_rent_1br=rent,
            source="ACS 2024 5-year B25031 median gross rent 1BR",
            period="2024",
        )
    return estimates


def _fetch_seattle_typology_rents(
    http: HttpClient,
    candidates: list[Candidate],
    config: dict[str, Any],
) -> dict[str, RentEstimate]:
    seattle_candidates = [
        candidate for candidate in candidates if candidate.metadata.get("geography") == "seattle_neighborhood"
    ]
    if not seattle_candidates:
        return {}

    try:
        payload = http.get_json(
            SEATTLE_RENT_TYPOLOGY_ENDPOINT,
            {
                "where": "1=1",
                "outFields": "CRA_NAME,YEAR,PRICE_NOM,UNIT_COUNT",
                "returnGeometry": "false",
                "f": "json",
                "resultRecordCount": 32000,
                "orderByFields": "YEAR DESC",
            },
        )
    except Exception as exc:
        config.setdefault("warnings", []).append(f"Seattle apartment rent typology skipped: {exc}")
        return {}

    latest_year = _latest_year(payload.get("features", []))
    if latest_year is None:
        return {}

    rents_by_cra: dict[str, list[tuple[float, int]]] = {}
    for feature in payload.get("features", []):
        attrs = feature.get("attributes", {})
        if _safe_int(attrs.get("YEAR")) != latest_year:
            continue
        cra_name = attrs.get("CRA_NAME")
        rent = _safe_rent(attrs.get("PRICE_NOM"))
        if not cra_name or rent is None:
            continue
        unit_count = max(0, _safe_int(attrs.get("UNIT_COUNT")))
        rents_by_cra.setdefault(_name_key(cra_name), []).append((rent, unit_count))

    estimates_by_cra = {
        key: RentEstimate(
            monthly_rent_1br=round(_weighted_average(values)),
            source="Seattle RentTypology market_multifamily_unit_median",
            period=str(latest_year),
            estimate_kind="market_multifamily_unit_median",
        )
        for key, values in rents_by_cra.items()
    }

    estimates: dict[str, RentEstimate] = {}
    for candidate in seattle_candidates:
        estimate = estimates_by_cra.get(_seattle_neighborhood_key(candidate))
        if estimate is not None:
            estimates[candidate.id] = estimate
    return estimates


def _latest_year(features: list[dict[str, Any]]) -> int | None:
    years = [feature.get("attributes", {}).get("YEAR") for feature in features]
    numeric_years = [int(year) for year in years if isinstance(year, int) or str(year).isdigit()]
    return max(numeric_years) if numeric_years else None


def _weighted_average(values: list[tuple[float, int]]) -> float:
    weighted_total = sum(rent * count for rent, count in values if count > 0)
    total_weight = sum(count for _, count in values if count > 0)
    if total_weight > 0:
        return weighted_total / total_weight
    return mean([rent for rent, _ in values])


def _candidate_place_key(candidate: Candidate) -> str:
    return _name_key(candidate.name.split(",", 1)[0])


def _apartment_list_candidate_key(candidate: Candidate) -> str:
    if candidate.metadata.get("geography") == "seattle_neighborhood":
        return "seattle"
    return _candidate_place_key(candidate)


def _seattle_neighborhood_key(candidate: Candidate) -> str:
    return _name_key(candidate.name.replace(", Seattle", ""))


def _name_key(value: str) -> str:
    cleaned = value.replace(", WA", "").replace(", Washington", "")
    return slug(cleaned)


def _safe_rent(value: Any) -> float | None:
    rent = _to_money(value)
    if rent is None or rent <= 0 or rent <= -666666:
        return None
    return rent


def _to_money(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        cleaned = re.sub(r"[^0-9.\-]+", "", str(value))
        if cleaned in {"", "-", "."}:
            return None
        number = float(cleaned)
    except (TypeError, ValueError):
        return None
    return round(number, 2)
