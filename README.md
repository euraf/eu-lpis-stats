# EU LPIS Stats
## Land Parcel Identification System Statistics by Administrative Unit

Pre-computed LPIS land-use statistics aggregated by LAU, NUTS3, NUTS2 and country, for use in web mapping and data applications.

This repository provides lightweight JSON files organised according to the official European administrative hierarchy, allowing web applications to load only the statistics they need.

The source data is fetched from national LPIS WFS services and cross-referenced with the [eu-nuts-lau](https://github.com/euraf/eu-nuts-lau) administrative boundary dataset.

## Motivation

National LPIS datasets are large and require spatial infrastructure (WFS servers, PostGIS, tile servers) to query efficiently.

This repository pre-computes the aggregated statistics and stores them as static JSON files, so web applications can:

* Load parcel and farm counts per municipality without any backend
* Filter by land-use category, land cover class, and other LPIS attributes
* Navigate the administrative hierarchy progressively
* Serve data directly from GitHub Pages or any static host

## Data Sources

* **LPIS (Portugal):** [IFAP WFS](https://www.ifap.pt/isip/ows/isip.data/wfs) — `ocupacoes.solo.2024jun10` (land-use polygons) and `parcelas.2024jun10` (parcels)
* **Administrative boundaries:** [euraf/eu-nuts-lau](https://github.com/euraf/eu-nuts-lau)

## Generated Structure

```text
v1/

├── PT/
│   ├── PT.json                  ← country-level aggregated stats
│   ├── PT1.json                 ← NUTS1-level aggregated stats
│   ├── PT1C.json                ← NUTS2-level aggregated stats
│   ├── PT1C1.json               ← NUTS3-level aggregated stats
│   ├── PT1C1_lau.json           ← per-LAU stats for NUTS3 PT1C1
│
└── ...
```

## Aggregation Keys

Statistics are broken down by the LPIS attribute keys. Each aggregation entry contains `parcel_count`, `farm_count`, `osa_count` and `area_ha`.

## Stats File Format

`{NUTS3}_lau.json` — numeric stats per LAU:

```json
{
  "nuts3_id": "PT1C1",
  "generated_at": "2026-01-01T00:00:00",
  "missing_lau_matches": 0,
  "lau": {
    "PT_1234567": {
      "parcel_count": 120,
      "farm_count": 45,
      "osa_count": 310,
      "area_ha": 850.5,
      "poly_match": 308,
      "point_match": 2,
      "CAT_OCU_SIGLA": {
        "AF": { "parcel_count": 30, "osa_count": 80, "area_ha": 210.0 }
      }
    }
  }
}
```

`{NUTS3}.json` / `{NUTS2}.json` / `{NUTS1}.json` / `{COUNTRY}.json` — stats rolled up to the respective administrative level.

## Example URLs

Load country-level stats for Portugal:
```text
https://raw.githubusercontent.com/euraf/eu-lpis-stats/master/v1/PT/PT.json
```

Load NUTS2-level stats:
```text
https://raw.githubusercontent.com/euraf/eu-lpis-stats/master/v1/PT/PT1.json
```

Load NUTS2-level stats:
```text
https://raw.githubusercontent.com/euraf/eu-lpis-stats/master/v1/PT/PT1C.json
```

Load NUTS3-level stats:
```text
https://raw.githubusercontent.com/euraf/eu-lpis-stats/master/v1/PT/PT1C1.json
```

Load per-LAU stats for a NUTS3 region:
```text
https://raw.githubusercontent.com/euraf/eu-lpis-stats/master/v1/PT/PT1C1_lau.json
```

## Building the Dataset

### Requirements

```bash
pip install geopandas requests shapely
```

### Step 1 — Fetch LPIS data and compute per-LAU stats

Fetches LPIS features from the WFS for each NUTS3 region and spatially joins them to LAU boundaries.

```bash
python fetch_lpis_build_lau_stats.py
```

Output per NUTS3:
* `{NUTS3}_lau.json` — numeric stats per LAU
* `{NUTS3}_lau_sets.json` — exact ID sets per LAU
* `{NUTS3}_missing_lau.json` — unmatched features (if any)
* `{NUTS3}_invalid_features.json` — invalid geometries (if any)

### Step 2 — Roll up stats to NUTS3 / NUTS2 / NUTS1 / country

Reads the per-LAU files produced in Step 1 and aggregates them up the administrative hierarchy.

```bash
python build_nuts_stats.py
```

Output:
* `{NUTS3}.json`
* `{NUTS2}.json`
* `{NUTS1}.json`
* `{COUNTRY}.json`

## License

LPIS data is subject to national licensing conditions.
Administrative boundary data is subject to Eurostat / GISCO terms of use.

This repository only redistributes pre-computed aggregated statistics derived from those datasets.
