from __future__ import annotations

from dataclasses import asdict
from statistics import mean
from typing import Any

from .google_maps import GoogleMapsClient
from .models import Candidate, Commute
from .rents import MISSING_RENT_SOURCE, RentEstimate


def normalize(value: float | None, minimum: float, maximum: float, invert: bool = False) -> float:
    if value is None:
        return 0
    if maximum <= minimum:
        score = 1
    else:
        score = (value - minimum) / (maximum - minimum)
    score = max(0, min(1, score))
    return 1 - score if invert else score


def _home_reference(candidates: list[Candidate], home_name: str) -> Candidate | None:
    home = home_name.split(",")[0].strip().lower()
    for candidate in candidates:
        if candidate.name.lower() == home or candidate.name.lower().startswith(f"{home},"):
            return candidate
    return None


def analyze(
    candidates: list[Candidate],
    maps: GoogleMapsClient,
    config: dict[str, Any],
    rent_estimates: dict[str, RentEstimate] | None = None,
) -> dict[str, Any]:
    offices = config.get("office_locations", [])
    weights = config.get("weights", {"demographics": 0.45, "commute": 0.25, "affordability": 0.30})
    demographic_weight = float(weights.get("demographics", 0.45))
    commute_weight = float(weights.get("commute", 0.25))
    affordability_weight = float(weights.get("affordability", 0.30))
    rent_config = config.get("rent", {})
    rent_enabled = rent_config.get("enabled", True)
    commute_days_per_month = float(rent_config.get("commute_days_per_month", 20))
    rent_estimates = rent_estimates or {}

    candidates = sorted(candidates, key=lambda item: item.target_share, reverse=True)
    limit = int(config.get("directions_limit", 25))
    scored_pool = candidates[:limit]

    home = _home_reference(candidates, config.get("home", "Sammamish, WA"))
    target_shares = [candidate.target_share for candidate in scored_pool]
    share_min = min(target_shares) if target_shares else 0
    share_max = max(target_shares) if target_shares else 1

    enriched: list[dict[str, Any]] = []
    all_commute_minutes: list[float] = []
    all_total_monthly_costs: list[float] = []

    for candidate in scored_pool:
        commutes: list[Commute] = []
        first_location: dict[str, float] | None = None
        for office in offices:
            commute, start_location = maps.commute(candidate.search_text, office["address"], office["name"], config)
            commutes.append(commute)
            if first_location is None and start_location:
                first_location = start_location
            if commute.duration_minutes is not None:
                all_commute_minutes.append(commute.duration_minutes)

        average_daily_cost = (
            mean([commute.daily_total_cost for commute in commutes if commute.daily_total_cost is not None])
            if any(commute.daily_total_cost is not None for commute in commutes)
            else None
        )
        rent_estimate = rent_estimates.get(candidate.id)
        monthly_commute_cost = _monthly_commute_cost(average_daily_cost, commute_days_per_month, maps.enabled)
        total_monthly_cost = _total_monthly_cost(rent_estimate, monthly_commute_cost, maps.enabled)
        if total_monthly_cost is not None:
            all_total_monthly_costs.append(total_monthly_cost)

        enriched.append(
            {
                "candidate": candidate,
                "commutes": commutes,
                "location": first_location,
                "average_commute_minutes": mean(
                    [commute.duration_minutes for commute in commutes if commute.duration_minutes is not None]
                )
                if any(commute.duration_minutes is not None for commute in commutes)
                else None,
                "average_daily_cost": average_daily_cost,
                "monthly_commute_cost": monthly_commute_cost,
                "total_monthly_cost": total_monthly_cost,
                "rent_estimate": rent_estimate,
            }
        )

    commute_min = min(all_commute_minutes) if all_commute_minutes else 0
    commute_max = max(all_commute_minutes) if all_commute_minutes else 1
    cost_min = min(all_total_monthly_costs) if all_total_monthly_costs else 0
    cost_max = max(all_total_monthly_costs) if all_total_monthly_costs else 1
    has_any_rent = bool(all_total_monthly_costs)
    if rent_enabled and not has_any_rent:
        config.setdefault("warnings", []).append("Rent data unavailable for scored candidates; affordability scoring skipped.")

    results: list[dict[str, Any]] = []
    for row in enriched:
        candidate: Candidate = row["candidate"]
        rent_estimate: RentEstimate | None = row["rent_estimate"]
        demographic_score = normalize(candidate.target_share, share_min, share_max)
        commute_score = normalize(row["average_commute_minutes"], commute_min, commute_max, invert=True)
        affordability_score = (
            normalize(row["total_monthly_cost"], cost_min, cost_max, invert=True)
            if rent_enabled and has_any_rent and row["total_monthly_cost"] is not None
            else 0
        )
        combined = _combined_score(
            demographic_score=demographic_score,
            commute_score=commute_score,
            affordability_score=affordability_score,
            maps_enabled=maps.enabled,
            rent_enabled=rent_enabled and has_any_rent,
            demographic_weight=demographic_weight,
            commute_weight=commute_weight,
            affordability_weight=affordability_weight,
        )

        home_delta = None
        if home is not None:
            home_delta = candidate.target_share - home.target_share

        results.append(
            {
                "id": candidate.id,
                "name": candidate.name,
                "source": candidate.source,
                "search_text": candidate.search_text,
                "total_population": candidate.total_population,
                "target_population": candidate.target_population,
                "target_share": candidate.target_share,
                "home_delta": home_delta,
                "average_commute_minutes": row["average_commute_minutes"],
                "average_daily_cost": row["average_daily_cost"],
                "monthly_rent_1br": rent_estimate.monthly_rent_1br if rent_estimate else None,
                "rent_source": rent_estimate.source if rent_estimate else MISSING_RENT_SOURCE,
                "rent_year_or_month": rent_estimate.period if rent_estimate else None,
                "rent_estimate_kind": rent_estimate.estimate_kind if rent_estimate else None,
                "monthly_commute_cost": row["monthly_commute_cost"],
                "total_monthly_cost": row["total_monthly_cost"],
                "score": combined,
                "demographic_score": demographic_score,
                "commute_score": commute_score if maps.enabled else None,
                "affordability_score": affordability_score if rent_enabled and has_any_rent else None,
                "location": row["location"],
                "metadata": candidate.metadata,
                "commutes": [asdict(commute) for commute in row["commutes"]],
            }
        )

    return {
        "results": sorted(results, key=lambda item: item["score"], reverse=True),
        "reference": asdict(home) if home else None,
        "status": {
            "api_version": 3,
            "candidate_count": len(candidates),
            "scored_count": len(results),
            "maps_enabled": maps.enabled,
            "rent_enabled": rent_enabled,
            "rent_scored_count": len(all_total_monthly_costs),
            "commute_days_per_month": commute_days_per_month,
            "directions_limit": limit,
            "office_count": len(offices),
            "estimated_route_requests": limit * len(offices) * 3 if maps.enabled else 0,
            "sources": [
                "2024 ACS 5-year B01001 via Census API",
                "Apartment List rent estimates CSV when available",
                "2024 ACS 5-year B25031 1BR rent fallback",
                "Seattle RentTypology market multifamily rent fallback",
                "Seattle neighborhood ACS layer via Seattle City GIS ArcGIS",
                "Google Routes API" if maps.enabled else "Google Routes API not configured",
            ],
            "warnings": config.get("warnings", []),
        },
    }


def _monthly_commute_cost(
    average_daily_cost: float | None,
    commute_days_per_month: float,
    maps_enabled: bool,
) -> float | None:
    if average_daily_cost is None:
        return 0 if not maps_enabled else None
    return round(average_daily_cost * commute_days_per_month, 2)


def _total_monthly_cost(
    rent_estimate: RentEstimate | None,
    monthly_commute_cost: float | None,
    maps_enabled: bool,
) -> float | None:
    if rent_estimate is None:
        return None
    if monthly_commute_cost is None:
        return None if maps_enabled else rent_estimate.monthly_rent_1br
    return round(rent_estimate.monthly_rent_1br + monthly_commute_cost, 2)


def _combined_score(
    *,
    demographic_score: float,
    commute_score: float,
    affordability_score: float,
    maps_enabled: bool,
    rent_enabled: bool,
    demographic_weight: float,
    commute_weight: float,
    affordability_weight: float,
) -> float:
    weighted_scores = [(demographic_score, demographic_weight)]
    if maps_enabled:
        weighted_scores.append((commute_score, commute_weight))
    if rent_enabled:
        weighted_scores.append((affordability_score, affordability_weight))

    total_weight = sum(weight for _, weight in weighted_scores) or 1
    return sum(score * weight for score, weight in weighted_scores) / total_weight
