import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from seattle_finder.demographics import selected_variables, slug, target_population, variables_for_age_range
from seattle_finder.google_maps import decode_polyline
from seattle_finder.models import Candidate, Commute
from seattle_finder.rents import RentEstimate, load_rent_estimates
from seattle_finder.scoring import analyze
from seattle_finder.scoring import normalize


class ScoringTests(unittest.TestCase):
    def test_selected_age_variables_deduplicates_expected_bands(self):
        variables = selected_variables(["20-24", "25-29", "20-24"])

        self.assertIn("B01001_008E", variables)
        self.assertIn("B01001_035E", variables)
        self.assertEqual(len(variables), len(set(variables)))

    def test_age_range_variables_include_partial_bucket(self):
        variables = variables_for_age_range(24, 29)

        self.assertIn("B01001_010E", variables)
        self.assertIn("B01001_011E", variables)
        self.assertIn("B01001_034E", variables)
        self.assertIn("B01001_035E", variables)
        self.assertNotIn("B01001_008E", variables)

    def test_target_population_prorates_grouped_ages(self):
        record = {
            "B01001_010E": 300,
            "B01001_011E": 500,
            "B01001_034E": 600,
            "B01001_035E": 1000,
        }

        self.assertEqual(target_population(record, 24, 29), 1800)

    def test_decode_polyline(self):
        points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")

        self.assertEqual(points[0], (38.5, -120.2))
        self.assertEqual(points[-1], (43.252, -126.453))

    def test_analyze_keeps_all_office_commutes(self):
        candidate = Candidate(
            id="place-test",
            name="Test Place",
            source="test",
            search_text="Test Place, WA",
            total_population=1000,
            target_population=250,
            target_share=0.25,
        )
        maps = FakeMapsClient()

        result = analyze(
            [candidate],
            maps,
            {
                "home": "Sammamish, WA",
                "directions_limit": 1,
                "office_locations": [
                    {"name": "Office A", "address": "100 A St, Bellevue, WA"},
                    {"name": "Office B", "address": "200 B St, Seattle, WA"},
                    {"name": "Office C", "address": "300 C St, Redmond, WA"},
                ],
            },
        )

        self.assertEqual(len(result["results"][0]["commutes"]), 3)
        self.assertEqual(result["results"][0]["commutes"][1]["office_name"], "Office B")
        self.assertEqual(result["results"][0]["average_commute_minutes"], 20)
        self.assertEqual(result["results"][0]["average_daily_cost"], 2)

    def test_slug_normalizes_place_names(self):
        self.assertEqual(slug("Union Hill-Novelty Hill, WA"), "union-hill-novelty-hill-wa")

    def test_normalize_inverts_commute_score(self):
        self.assertEqual(normalize(10, 10, 30, invert=True), 1)
        self.assertEqual(normalize(30, 10, 30, invert=True), 0)

    def test_apartment_list_city_match_wins(self):
        candidate = Candidate(
            id="place-bellevue",
            name="Bellevue",
            source="test",
            search_text="Bellevue, WA",
            total_population=1000,
            target_population=250,
            target_share=0.25,
            metadata={"geography": "place"},
        )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "apartment_list.csv"
            path.write_text("location_name,location_type,state,1br_rent,month\nBellevue,city,WA,2345,2026-08\n")
            estimates = load_rent_estimates(FakeRentHttp({}), [candidate], {"rent": {"apartment_list_csv": str(path)}})

        self.assertEqual(estimates[candidate.id].monthly_rent_1br, 2345)
        self.assertEqual(estimates[candidate.id].source, "Apartment List median 1BR rent")

    def test_acs_place_rent_fallback(self):
        candidate = Candidate(
            id="place-redmond",
            name="Redmond",
            source="test",
            search_text="Redmond, WA",
            total_population=1000,
            target_population=250,
            target_share=0.25,
            metadata={"geography": "place"},
        )
        http = FakeRentHttp(
            {
                "acs": [
                    ["NAME", "B25031_003E", "state", "place"],
                    ["Redmond city, Washington", "2100", "53", "57535"],
                ]
            }
        )

        estimates = load_rent_estimates(
            http,
            [candidate],
            {"rent": {"apartment_list_csv": "/missing/apartment_list.csv"}, "warnings": []},
        )

        self.assertEqual(estimates[candidate.id].monthly_rent_1br, 2100)
        self.assertIn("ACS 2024", estimates[candidate.id].source)

    def test_seattle_typology_fallback_is_labeled_as_unit_median(self):
        candidate = Candidate(
            id="seattle-neighborhood-green-lake",
            name="Green Lake, Seattle",
            source="test",
            search_text="Green Lake, Seattle, WA",
            total_population=1000,
            target_population=250,
            target_share=0.25,
            metadata={"geography": "seattle_neighborhood"},
        )
        http = FakeRentHttp(
            {
                "acs": [["NAME", "B25031_003E", "state", "place"]],
                "seattle": {
                    "features": [
                        {"attributes": {"CRA_NAME": "Green Lake", "YEAR": 2025, "PRICE_NOM": 1800, "UNIT_COUNT": 10}},
                        {"attributes": {"CRA_NAME": "Green Lake", "YEAR": 2025, "PRICE_NOM": 2200, "UNIT_COUNT": 30}},
                    ]
                },
            }
        )

        estimates = load_rent_estimates(
            http,
            [candidate],
            {"rent": {"apartment_list_csv": "/missing/apartment_list.csv"}, "warnings": []},
        )

        self.assertEqual(estimates[candidate.id].monthly_rent_1br, 2100)
        self.assertEqual(estimates[candidate.id].estimate_kind, "market_multifamily_unit_median")

    def test_lower_total_monthly_cost_improves_affordability_score(self):
        candidates = [
            Candidate(
                id="place-cheap",
                name="Cheap",
                source="test",
                search_text="Cheap, WA",
                total_population=1000,
                target_population=250,
                target_share=0.25,
            ),
            Candidate(
                id="place-expensive",
                name="Expensive",
                source="test",
                search_text="Expensive, WA",
                total_population=1000,
                target_population=250,
                target_share=0.25,
            ),
        ]
        result = analyze(
            candidates,
            FakeMapsClient(),
            {
                "home": "Cheap, WA",
                "directions_limit": 2,
                "office_locations": [{"name": "Office A", "address": "100 A St, Bellevue, WA"}],
            },
            {
                "place-cheap": RentEstimate(1500, "test"),
                "place-expensive": RentEstimate(2500, "test"),
            },
        )

        self.assertEqual(result["results"][0]["id"], "place-cheap")
        self.assertGreater(result["results"][0]["affordability_score"], result["results"][1]["affordability_score"])

    def test_no_rent_data_preserves_existing_demographic_scoring_when_maps_disabled(self):
        candidates = [
            Candidate("place-a", "A", "test", "A, WA", 1000, 100, 0.10),
            Candidate("place-b", "B", "test", "B, WA", 1000, 300, 0.30),
        ]

        result = analyze(
            candidates,
            FakeDisabledMapsClient(),
            {"home": "A, WA", "directions_limit": 2, "office_locations": [{"name": "Office A", "address": "A"}]},
        )

        self.assertEqual(result["results"][0]["id"], "place-b")
        self.assertIsNone(result["results"][0]["affordability_score"])

    def test_maps_disabled_mode_renormalizes_demographics_and_affordability(self):
        candidates = [
            Candidate("place-high-demo", "High Demo", "test", "High Demo, WA", 1000, 300, 0.30),
            Candidate("place-low-cost", "Low Cost", "test", "Low Cost, WA", 1000, 100, 0.10),
        ]

        result = analyze(
            candidates,
            FakeDisabledMapsClient(),
            {
                "home": "High Demo, WA",
                "directions_limit": 2,
                "office_locations": [{"name": "Office A", "address": "A"}],
                "weights": {"demographics": 0.45, "commute": 0.25, "affordability": 0.30},
            },
            {
                "place-high-demo": RentEstimate(3000, "test"),
                "place-low-cost": RentEstimate(1000, "test"),
            },
        )

        scores = {row["id"]: row["score"] for row in result["results"]}
        self.assertAlmostEqual(scores["place-high-demo"], 0.6)
        self.assertAlmostEqual(scores["place-low-cost"], 0.4)


class FakeMapsClient:
    enabled = True

    def commute(self, origin, destination, office_name, config=None):
        durations = {"Office A": 10, "Office B": 20, "Office C": 30}
        return (
            Commute(
                office_name=office_name,
                office_address=destination,
                duration_minutes=durations[office_name],
                distance_miles=durations[office_name] / 2,
                status="OK",
                route_url=f"https://example.test/{office_name}",
                morning_duration_minutes=durations[office_name] - 1,
                evening_duration_minutes=durations[office_name] + 1,
                daily_total_cost=durations[office_name] / 10,
            ),
            {"lat": 47.6, "lng": -122.2},
        )


class FakeDisabledMapsClient:
    enabled = False

    def commute(self, origin, destination, office_name, config=None):
        return (
            Commute(
                office_name=office_name,
                office_address=destination,
                duration_minutes=None,
                distance_miles=None,
                status="missing_google_maps_api_key",
            ),
            None,
        )


class FakeRentHttp:
    def __init__(self, payloads):
        self.payloads = payloads

    def get_json(self, url, params=None, use_cache=True):
        if "acs" in url:
            return self.payloads.get("acs", [["NAME", "B25031_003E", "state", "place"]])
        if "RentTypology" in url:
            return self.payloads.get("seattle", {"features": []})
        return {}


if __name__ == "__main__":
    unittest.main()
