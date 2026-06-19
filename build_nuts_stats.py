#!/usr/bin/env python3

from config import *

import json
import time
from pathlib import Path
from collections import defaultdict
import geopandas as gpd

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

AGG_KEYS = [
    "CAT_OCU_SIGLA",
    "CLA_OCU_SIGLA",
    "GRA_COB_SIGLA",
    "OSA_ELE_LIN_PAI",
    "OSA_PRA_PER",
    "OSA_PRO_BIO",
    "OSA_SIE"
]

# ---------------------------------------------------------
# BUCKETS
# ---------------------------------------------------------

def create_bucket():
    return {
        "countries_count": 0,
        "nuts1_count": 0,
        "nuts2_count": 0,
        "nuts3_count": 0,
        "lau_count": 0,
        "osa_count": 0,
        "area_ha": 0.0,

        "parcel_ids": set(),
        "farm_ids": set(),

        **{
            k: defaultdict(lambda: {
                "parcel_ids": set(),
                "farm_ids": set(),
                "osa_count": 0,
                "area_ha": 0.0
            }) for k in AGG_KEYS
        }
    }

nuts3_buckets = defaultdict(create_bucket)
nuts2_buckets = defaultdict(create_bucket)
nuts1_buckets = defaultdict(create_bucket)
countries_buckets = defaultdict(create_bucket)

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def merge_stats_lau(bucket, lau):
    """numeric-only aggregation (fast path)"""

    bucket["lau_count"] += 1
    bucket["osa_count"] += lau.get("osa_count", 0)
    bucket["area_ha"] += lau.get("area_ha", 0.0)

    for agg_key in AGG_KEYS:
        for value_key, stats in lau.get(agg_key, {}).items():

            if value_key not in bucket[agg_key]:
                bucket[agg_key][value_key] = {
                    "parcel_ids": set(),
                    "farm_ids": set(),
                    "osa_count": 0,
                    "area_ha": 0.0
                }

            t = bucket[agg_key][value_key]

            t["osa_count"] += stats.get("osa_count", 0)
            t["area_ha"] += stats.get("area_ha", 0.0)


def merge_sets_lau(bucket, lau):
    """exact ID aggregation (truth source)"""

    parcel_ids = lau.get("parcel_ids", [])
    farm_ids = lau.get("farm_ids", [])

    bucket["parcel_ids"].update(parcel_ids)
    bucket["farm_ids"].update(farm_ids)

    for agg_key in AGG_KEYS:
        for value_key, stats in lau.get(agg_key, {}).items():

            t = bucket[agg_key][value_key]

            t["parcel_ids"].update(stats.get("parcel_ids", []))
            t["farm_ids"].update(stats.get("farm_ids", []))


def finalize(bucket):
    """convert sets → counts"""

    result = {
        "countries_count": bucket["countries_count"],
        "nuts1_count": bucket["nuts1_count"],
        "nuts2_count": bucket["nuts2_count"],
        "nuts3_count": bucket["nuts3_count"],
        "lau_count": bucket["lau_count"],

        "parcel_count": len(bucket["parcel_ids"]),
        "farm_count": len(bucket["farm_ids"]),

        "osa_count": bucket["osa_count"],
        "area_ha": round(bucket["area_ha"], 3)
    }

    for agg_key in AGG_KEYS:
        result[agg_key] = {}

        for value_key, stats in sorted(bucket[agg_key].items()):

            result[agg_key][value_key] = {
                "parcel_count": len(stats["parcel_ids"]),
                "farm_count": len(stats["farm_ids"]),
                "osa_count": stats["osa_count"],
                "area_ha": round(stats["area_ha"], 3)
            }

    return result

# ---------------------------------------------------------
# FILE DISCOVERY
# ---------------------------------------------------------

for country_folder in BASE_DIR.iterdir():
    if country_folder.is_dir():
        country_code = country_folder.name
        
        ### TESTING
        if country_code != "PT": continue

        FILE_DIR = BASE_DIR / country_code

        print(f"\n==============================")
        print(f"COUNTRY: {country_code}")

        stats_files = sorted(FILE_DIR.glob("*_lau.json"))
        set_files = sorted(FILE_DIR.glob("*_lau_sets.json"))

        print(f"Found {len(stats_files)} stats files")
        print(f"Found {len(set_files)} sets files")

        # ---------------------------------------------------------
        # PASS 1: NUMERIC AGGREGATION
        # ---------------------------------------------------------

        for i, f in enumerate(stats_files, 1):

            print(f"[STATS {i}/{len(stats_files)}] {f.name}")

            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)

            nuts3_id = data["nuts3_id"]
            country_code = nuts3_id[:2]
            nuts2_id = nuts3_id[:-1]
            nuts1_id = nuts3_id[:-2]

            nuts3_buckets[nuts3_id]["nuts3_count"] = 1
            nuts2_buckets[nuts2_id]["nuts3_count"] += 1
            nuts1_buckets[nuts1_id]["nuts3_count"] += 1
            countries_buckets[country_code]["nuts3_count"] += 1

            nuts2_buckets[nuts2_id]["nuts2_count"] = 1
            nuts1_buckets[nuts1_id]["nuts2_count"] += 1
            countries_buckets[country_code]["nuts2_count"] += 1

            nuts1_buckets[nuts1_id]["nuts1_count"] = 1
            countries_buckets[country_code]["nuts1_count"] += 1

            for lau in data.get("lau", {}).values():
                merge_stats_lau(nuts3_buckets[nuts3_id], lau)
                merge_stats_lau(nuts2_buckets[nuts2_id], lau)
                merge_stats_lau(nuts1_buckets[nuts1_id], lau)
                merge_stats_lau(countries_buckets[country_code], lau)

        # ---------------------------------------------------------
        # PASS 2: EXACT IDS
        # ---------------------------------------------------------

        for i, f in enumerate(set_files, 1):

            print(f"[SETS {i}/{len(set_files)}] {f.name}")

            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)

            nuts3_id = data["nuts3_id"]
            country_code = nuts3_id[:2]
            nuts2_id = nuts3_id[:-1]
            nuts1_id = nuts3_id[:-2]

            for lau in data.get("lau", {}).values():

                merge_sets_lau(nuts3_buckets[nuts3_id], lau)
                merge_sets_lau(nuts2_buckets[nuts2_id], lau)
                merge_sets_lau(nuts1_buckets[nuts1_id], lau)
                merge_sets_lau(countries_buckets[country_code], lau)

        # ---------------------------------------------------------
        # WRITE OUTPUTS
        # ---------------------------------------------------------

        for nuts3_id, bucket in nuts3_buckets.items():

            out = {
                "nuts3_id": nuts3_id,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                **finalize(bucket)
            }

            out_file = FILE_DIR / f"{nuts3_id}.json"

            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)

            print(f"Saved {out_file}")


        for nuts2_id, bucket in nuts2_buckets.items():

            out = {
                "nuts2_id": nuts2_id,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                **finalize(bucket)
            }

            out_file = FILE_DIR / f"{nuts2_id}.json"

            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)

            print(f"Saved {out_file}")


        for nuts1_id, bucket in nuts1_buckets.items():

            out = {
                "nuts1_id": nuts2_id,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                **finalize(bucket)
            }

            out_file = FILE_DIR / f"{nuts1_id}.json"

            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)

            print(f"Saved {out_file}")


        for country_id, bucket in countries_buckets.items():

            out = {
                "country_code": country_id,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                **finalize(bucket)
            }

            out_file = FILE_DIR / f"{country_id}.json"

            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)

            print(f"Saved {out_file}")

        print("DONE")