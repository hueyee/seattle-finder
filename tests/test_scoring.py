import unittest

from seattle_finder.demographics import selected_variables, slug, target_population, variables_for_age_range
from seattle_finder.google_maps import decode_polyline
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

    def test_slug_normalizes_place_names(self):
        self.assertEqual(slug("Union Hill-Novelty Hill, WA"), "union-hill-novelty-hill-wa")

    def test_normalize_inverts_commute_score(self):
        self.assertEqual(normalize(10, 10, 30, invert=True), 1)
        self.assertEqual(normalize(30, 10, 30, invert=True), 0)


if __name__ == "__main__":
    unittest.main()
