# Seattle Living Finder

A local v1 web app for comparing Greater Seattle places by young-adult demographics and drive time to office locations.

## What it does

- Pulls ACS 2024 `B01001: Sex by Age` place data from the Census API.
- Pulls Seattle neighborhood age data from Seattle City GIS ArcGIS.
- Uses Google Routes API for drive commute time and distance when `GOOGLE_MAPS_API_KEY` is set.
- Estimates commute timing for arrival by 9:00 AM and departure at 5:00 PM by default.
- Estimates daily driving cost from fuel plus Routes API toll estimates, including SR-520 when the route uses it.
- Estimates 1-bedroom rent from an imported Apartment List CSV when available, then falls back to ACS and Seattle rent typology data.
- Scores places by target-age share, commute time, and total monthly cost, then renders rankings and a map.

## Run

```bash
cp .env.example .env
# Edit .env and add GOOGLE_MAPS_API_KEY.
# Add CENSUS_API_KEY if the Census API requires one from your network.
set -a
source .env
set +a
python3 server.py
```

Open `http://127.0.0.1:8000`.

The app will still run without a Google key, but commute scoring and mapped candidate locations require Google Routes responses. If the Census API asks for a key, Seattle neighborhood data still works while Census place data is skipped.

## Configuration

The UI sends configuration to `/api/analyze`. Defaults live in `data/default_config.json`.

Useful knobs:

- `target_age_min` / `target_age_max`: defaults to `24` through `29`.
- `office_locations`: add one or more office names and exact street addresses.
- `directions_limit`: controls how many high-demographic candidates get Google Routes calls.
- `weights`: tune demographic, commute, and affordability importance.
- `rent`: controls rent scoring, including the local Apartment List CSV path and commute days per month.
- `commute_schedule`: defaults to arrival by `09:00` and departure at `17:00`.
- `vehicle`: defaults to `15` MPG, `$5.00` per gallon, gasoline, and `US_WA_GOOD_TO_GO` toll pass pricing.
- `candidate_places`: the Census place allowlist for the Greater Seattle search area.

## Data Sources

- Seattle City GIS ArcGIS layer: `demographics_basic_age_sex_Neighborhoods`
- Census ACS 2024 5-year API: `B01001`
- Local Apartment List rent estimates CSV, when placed at `data/apartment_list_rent_estimates.csv`
- Census ACS 2024 5-year API: `B25031`
- Seattle City GIS ArcGIS layer: `RentTypology`
- Google Routes API
