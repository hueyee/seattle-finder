from __future__ import annotations

from dataclasses import asdict
from statistics import mean
from typing import Any

from .google_maps import GoogleMapsClient
from .models import Candidate, Commute


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


def analyze(candidates: list[Candidate], maps: GoogleMapsClient, config: dict[str, Any]) -> dict[str, Any]:
    offices = config.get("office_locations", [])
    weights = config.get("weights", {"demographics": 0.65, "commute": 0.35})
    demographic_weight = float(weights.get("demographics", 0.65))
    commute_weight = float(weights.get("commute", 0.35))
    total_weight = demographic_weight + commute_weight or 1

    candidates = sorted(candidates, key=lambda item: item.target_share, reverse=True)
    limit = int(config.get("directions_limit", 25))
    scored_pool = candidates[:limit]

    home = _home_reference(candidates, config.get("home", "Sammamish, WA"))
    target_shares = [candidate.target_share for candidate in scored_pool]
    share_min = min(target_shares) if target_shares else 0
    share_max = max(target_shares) if target_shares else 1

    enriched: list[dict[str, Any]] = []
    all_commute_minutes: list[float] = []

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
                "average_daily_cost": mean(
                    [commute.daily_total_cost for commute in commutes if commute.daily_total_cost is not None]
                )
                if any(commute.daily_total_cost is not None for commute in commutes)
                else None,
            }
        )

    commute_min = min(all_commute_minutes) if all_commute_minutes else 0
    commute_max = max(all_commute_minutes) if all_commute_minutes else 1

    results: list[dict[str, Any]] = []
    for row in enriched:
        candidate: Candidate = row["candidate"]
        demographic_score = normalize(candidate.target_share, share_min, share_max)
        commute_score = normalize(row["average_commute_minutes"], commute_min, commute_max, invert=True)
        if not maps.enabled:
            combined = demographic_score
        else:
            combined = ((demographic_score * demographic_weight) + (commute_score * commute_weight)) / total_weight

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
                "score": combined,
                "demographic_score": demographic_score,
                "commute_score": commute_score if maps.enabled else None,
                "location": row["location"],
                "metadata": candidate.metadata,
                "commutes": [asdict(commute) for commute in row["commutes"]],
            }
        )

    return {
        "results": sorted(results, key=lambda item: item["score"], reverse=True),
        "reference": asdict(home) if home else None,
        "status": {
            "candidate_count": len(candidates),
            "scored_count": len(results),
            "maps_enabled": maps.enabled,
            "directions_limit": limit,
            "sources": [
                "2024 ACS 5-year B01001 via Census API",
                "Seattle neighborhood ACS layer via Seattle City GIS ArcGIS",
                "Google Routes API" if maps.enabled else "Google Routes API not configured",
            ],
            "warnings": config.get("warnings", []),
        },
    }
