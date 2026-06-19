#! python3

from config import *

from pathlib import Path
import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

import geopandas as gpd
from shapely.geometry import shape
from shapely.errors import GEOSException
import time
import random
import logging
import requests

# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("ifap-engine")


# ------------------------------------------------------------
# SAFE REQUEST WITH RETRY + BACKOFF
# ------------------------------------------------------------

def safe_get(url, params, max_retries=5):

    import urllib.parse

    delay = 2

    for attempt in range(max_retries):

        try:
            r = requests.get(url, params=params, timeout=120)

            # 👉 LOG FULL REQUEST URL (AFTER params encoding)
            full_url = r.url
            log.info(f"Request URL: {full_url}")

            if r.status_code in (429, 500, 502, 503, 504):
                raise Exception(f"HTTP {r.status_code}")

            r.raise_for_status()
            return r.json()

        except Exception as e:

            wait = delay * (2 ** attempt)

            # 👉 ALSO log URL on failure
            full_url = requests.Request("GET", url, params=params).prepare().url

            log.warning(
                f"Request failed (attempt {attempt+1})\n"
                f"URL: {full_url}\n"
                f"Error: {e}\n"
                f"Retrying in {wait:.1f}s"
            )

            time.sleep(wait)

    raise RuntimeError("Max retries exceeded")
# ------------------------------------------------------------
# THROTTLE (IFAP FRIENDLY)
# ------------------------------------------------------------

LAST_REQUEST_TIME = 0

def throttle(min_interval=0.4):
    global LAST_REQUEST_TIME
    now = time.time()
    delta = now - LAST_REQUEST_TIME

    if delta < min_interval:
        time.sleep(min_interval - delta)

    LAST_REQUEST_TIME = time.time()
    
# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://www.ifap.pt/isip/ows/isip.data/wfs"

OSA_TYPENAME = "isip.data:ocupacoes.solo.2024jun10"
PAR_TYPENAME = "isip.data:parcelas.2024jun10"

GITHUB_BASE = "https://raw.githubusercontent.com/euraf/eu-nuts-lau/master/"

MAX_FEATURES = 2000

AGG_KEYS = [
    "CAT_OCU_SIGLA",
    "CLA_OCU_SIGLA",
    "GRA_COB_SIGLA",
    "OSA_ELE_LIN_PAI",
    "OSA_PRA_PER",
    "OSA_PRO_BIO",
    "OSA_SIE",
]


# ============================================================
# WFS FETCH (PAGED)
# ============================================================
def fetch_stream(typename, bbox):

    start = 0

    while True:

        throttle(0.4)  # IMPORTANT for IFAP

        params = {
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typename": typename,
            "outputFormat": "application/json",
            "srsname": "EPSG:4326",
            "bbox": ",".join(map(str, bbox)) + ",EPSG:4326",
            "startIndex": start,
            "maxFeatures": 2000
        }

        data = safe_get(BASE_URL, params)

        feats = data.get("features", [])

        if not feats:
            break

        for f in feats:
            yield f

        start += 2000

def fetch_wfs_all(typename, bbox):

    start = 0
    results = []

    while True:

        params = {
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typename": typename,
            "outputFormat": "application/json",
            "srsname": "EPSG:4326",
            "bbox": ",".join(map(str, bbox)) + ",EPSG:4326",
            "startIndex": start,
            "maxFeatures": MAX_FEATURES
        }

        r = requests.get(BASE_URL, params=params, timeout=120)
        r.raise_for_status()

        data = r.json()
        feats = data.get("features", [])

        if not feats:
            break

        results.extend(feats)
        start += MAX_FEATURES

    return results


# ============================================================
# SAFE GEOMETRY
# ============================================================

def safe_shape(g):
    try:
        return shape(g)
    except GEOSException:
        return None

def detect_coord_order(coords):
    """
    Returns:
        "lonlat" (correct for shapely)
        "latlon" (swapped, common IFAP issue)
        "unknown"
    """

    try:
        x, y = coords[0][0], coords[0][1]
    except Exception:
        return "unknown"

    lon, lat = x, y

    # Portugal bounds heuristic
    if -10 < lon < -5 and 35 < lat < 43:
        return "lonlat"

    if -10 < lat < -5 and 35 < lon < 43:
        return "latlon"

    return "unknown"

def validate_geometry(feature, name="feature"):

    geom = feature.get("geometry")

    if not geom:
        return False, "missing_geometry"

    coords = geom.get("coordinates")

    if not coords:
        return False, "empty_coordinates"

    # crude bbox check
    try:
        flat = coords[0][0]
        x, y = flat[0], flat[1]

        if abs(x) > 180 or abs(y) > 90:
            return False, "invalid_world_range"

    except Exception:
        return False, "parse_error"

    return True, "ok"

def validate_ifap_feature(feature):

    geom = feature.get("geometry")
    if not geom:
        return False, "no_geometry"

    coords = geom.get("coordinates")

    try:
        sample = coords[0][0]
    except Exception:
        return False, "bad_structure"

    order = detect_coord_order([sample])

    if order == "latlon":
        return False, "swapped_axis_detected"

    if order == "unknown":
        return False, "unknown_crs_pattern"

    return True, "ok"

def bbox_overlap_check(geom, bbox):

    minx, miny, maxx, maxy = bbox
    gminx, gminy, gmaxx, gmaxy = geom.bounds

    # no overlap
    if gmaxx < minx or gminx > maxx:
        return False

    if gmaxy < miny or gminy > maxy:
        return False

    return True

def validate_ifap_stream(features, bbox, out_file=None):

    good = 0
    bad = 0
    issues = {}
    invalid_records = []

    for f in features:

        ok, reason = validate_geometry(f)

        if not ok:
            bad += 1
            issues[reason] = issues.get(reason, 0) + 1
            invalid_records.append({"reason": reason, "feature": f})
            continue

        ok2, reason2 = validate_ifap_feature(f)

        if not ok2:
            bad += 1
            issues[reason2] = issues.get(reason2, 0) + 1
            invalid_records.append({"reason": reason2, "feature": f})
            continue

        geom = shape(f["geometry"])

        if not bbox_overlap_check(geom, bbox):
            bad += 1
            issues["bbox_mismatch"] = issues.get("bbox_mismatch", 0) + 1
            invalid_records.append({"reason": "bbox_mismatch", "feature": f})
            continue

        good += 1

    print("\n=== IFAP VALIDATION REPORT ===")
    print("Valid:", good)
    print("Invalid:", bad)
    print("Issues:")
    for k, v in issues.items():
        print(f"  {k}: {v}")

    if out_file and invalid_records:
        out_file = Path(out_file)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(invalid_records, fh, indent=2, ensure_ascii=False)
        print(f"Invalid features written to: {out_file}")

    return good, bad, issues

# ============================================================
# PROCESS ONE NUTS3
# ============================================================

def process_nuts3(nuts3_id, lau, bbox, nuts3_geom, out_dir):

    import time
    start_time = time.time()

    log.info(f"START NUTS3 {nuts3_id}")

    out_dir.mkdir(parents=True, exist_ok=True)

    download_dir = out_dir / "download"
    download_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================
    # STEP 1 — PAR FIRST (FAST + SMALL)
    # ========================================================

    t0 = time.time()
    log.info("STEP 1/5 - Fetching PAR parcels...")

    par_to_ent = {}
    par_features = []

    par_count = 0

    for f in fetch_stream(PAR_TYPENAME, bbox):

        geom = f.get("geometry")
        if not geom:
            continue

        poly = safe_shape(geom)
        if poly is None or not nuts3_geom.contains(poly.representative_point()):
            continue

        p = f.get("properties", {})
        par_id = p.get("PAR_ID")
        ent_id = p.get("ENT_ID")

        if par_id is not None:
            par_to_ent[par_id] = ent_id

        par_features.append(f)
        par_count += 1

        if par_count % 5000 == 0:
            log.info(f"PAR processed: {par_count:,}")

    par_dl_file = download_dir / f"{nuts3_id}_par.geojson"
    with open(par_dl_file, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": par_features}, fh, ensure_ascii=False)
    log.info(f"PAR DOWNLOADED: {par_count:,} features → {par_dl_file} in {time.time() - t0:.1f}s")

    # ========================================================
    # STEP 2 — LOAD LAU
    # ========================================================

    t0 = time.time()
    log.info("STEP 2/5 - Loading LAU...")

    lau["geometry"] = lau.geometry.buffer(0)
    lau_sindex = lau.sindex

    log.info(f"LAU loaded: {len(lau):,} in {time.time() - t0:.1f}s")

    # ========================================================
    # STEP 3 — INIT STORAGE
    # ========================================================

    log.info("STEP 3/5 - Initializing buckets")

    lau_stats = {}

    def bucket():
        return {
            "parcel_ids": set(),
            "farm_ids": set(),
            "osa_count": 0,
            "area_ha": 0.0,
            "poly_match": 0,
            "point_match": 0,
            "aggregates": {
                key: defaultdict(
                    lambda: {
                        "parcel_ids": set(),
                        "farm_ids": set(),
                        "osa_count": 0,
                        "area_ha": 0.0,
                    }
                )
                for key in AGG_KEYS
            },
        }

    missing = 0
    missing_details = []
    osa_count = 0

    # ========================================================
    # STEP 4 — STREAM OSA
    # ========================================================

    t0 = time.time()
    log.info("STEP 4/5 - Processing OSA stream...")

    osa_features = list(fetch_stream(OSA_TYPENAME, bbox))

    osa_dl_file = download_dir / f"{nuts3_id}_osa.geojson"
    with open(osa_dl_file, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": osa_features}, fh, ensure_ascii=False)
    log.info(f"OSA DOWNLOADED: {len(osa_features):,} features → {osa_dl_file}")

    osa_features_in_nuts3 = []
    for f in osa_features:
        geom = f.get("geometry")
        if not geom:
            continue
        poly = safe_shape(geom)
        if poly is None:
            continue
        if nuts3_geom.contains(poly.representative_point()):
            osa_features_in_nuts3.append(f)

    log.info(
        f"OSA fetched={len(osa_features):,} | inside_nuts3={len(osa_features_in_nuts3):,}"
    )

    validate_ifap_stream(osa_features_in_nuts3, bbox, out_file=out_dir / f"{nuts3_id}_invalid_features.json")

    for f in osa_features_in_nuts3:

        osa_count += 1

        if osa_count % 2000 == 0:
            rate = osa_count / (time.time() - t0)
            log.info(f"OSA: {osa_count:,} ({rate:.1f}/s) missing={missing}")

        p = f.get("properties", {})
        geom = f.get("geometry")

        if not geom:
            continue

        poly = safe_shape(geom)
        if poly is None:
            continue

        par_id = p.get("PAR_ID")
        area_ha = (p.get("OSA_AREA") or 0) / 10000.0

        lau_match = None
        mode = None

        # ---- polygon match
        try:
            candidate_scores = []

            for idx in lau_sindex.intersection(poly.bounds):

                lau_geom = lau.geometry.iloc[idx]

                if lau_geom.intersects(poly):

                    score = lau_geom.intersection(poly).area

                    candidate_scores.append((score, idx))

            if candidate_scores:
                best_idx = max(candidate_scores)[1]
                lau_match = lau.iloc[best_idx].get("GISCO_ID")
                mode = "poly"
        except:
            pass

        # ---- fallback point
        if not lau_match:
            pt = poly.representative_point()
            try:
                for idx in lau_sindex.intersection(pt.bounds):
                    if lau.geometry.iloc[idx].contains(pt):
                        lau_match = lau.iloc[idx].get("GISCO_ID")
                        mode = "point"
                        break
            except:
                pass

        if not lau_match:
            missing += 1

            pt = poly.representative_point()
            missing_details.append(f)
            continue

        if lau_match not in lau_stats:
            lau_stats[lau_match] = bucket()

        b = lau_stats[lau_match]

        b["parcel_ids"].add(par_id)
        b["osa_count"] += 1
        b["area_ha"] += area_ha

        ent = par_to_ent.get(par_id)

        if ent:
            b["farm_ids"].add(ent)

        if mode == "poly":
            b["poly_match"] += 1
        else:
            b["point_match"] += 1
            
        for key in AGG_KEYS:
            value = p.get(key)
            if value is None or value == "":
                continue

            value_key = str(value)
            agg = b["aggregates"][key][value_key]
            if par_id is not None:
                agg["parcel_ids"].add(par_id)
            if ent:
                agg["farm_ids"].add(ent)
            agg["osa_count"] += 1
            agg["area_ha"] += area_ha

    log.info(f"OSA DONE: {osa_count:,} in {time.time() - t0:.1f}s")

    # ========================================================
    # STEP 5 — OUTPUT
    # ========================================================

    t0 = time.time()
    log.info("STEP 5/5 - Writing output...")

    def finalize_stats(b):

        result = {
            "parcel_count": len(b["parcel_ids"]),
            "farm_count": len(b["farm_ids"]),
            "osa_count": b["osa_count"],
            "area_ha": round(b["area_ha"], 3),
            "poly_match": b["poly_match"],
            "point_match": b["point_match"],
        }

        for key in AGG_KEYS:

            result[key] = {}

            for value_key, agg in b["aggregates"][key].items():

                result[key][value_key] = {
                    "parcel_count": len(agg["parcel_ids"]),
                    "osa_count": agg["osa_count"],
                    "area_ha": round(agg["area_ha"], 3),
                }

        return result


    def finalize_sets(b):

        result = {
            "parcel_ids": sorted(
                int(x)
                for x in b["parcel_ids"]
                if x is not None
            ),

            "farm_ids": sorted(
                int(x)
                for x in b["farm_ids"]
                if x is not None
            ),

            "osa_count": b["osa_count"],
            "area_ha": round(b["area_ha"], 3)
        }

        for agg_key in AGG_KEYS:

            result[agg_key] = {}

            for value_key, agg in b["aggregates"][agg_key].items():

                result[agg_key][value_key] = {

                    "parcel_ids": sorted(
                        int(x)
                        for x in agg["parcel_ids"]
                        if x is not None
                    ),

                    "farm_ids": sorted(
                        int(x)
                        for x in agg["farm_ids"]
                        if x is not None
                    ),

                    "osa_count": agg["osa_count"],

                    "area_ha": round(
                        agg["area_ha"],
                        3
                    )
                }

        return result

    output = {
        "nuts3_id": nuts3_id,
        "nuts3_label": nuts3_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "lau": {
            k: finalize_stats(v)
            for k, v in lau_stats.items()
        }
    }

    all_parcel_ids = set()
    all_farm_ids = set()

    for b in lau_stats.values():
        all_parcel_ids.update(b["parcel_ids"])
        all_farm_ids.update(b["farm_ids"])

    output_sets = {
        "nuts3_id": nuts3_id,
        "nuts3_label": nuts3_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "lau": {
            k: finalize_sets(v)
            for k, v in lau_stats.items()
        }
    }

    stats_file = out_dir / f"{nuts3_id}_lau.json"

    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    sets_file = out_dir / f"{nuts3_id}_lau_sets.json"

    with open(sets_file, "w", encoding="utf-8") as f:
        json.dump(output_sets, f, ensure_ascii=False)

    log.info(f"SAVED {stats_file}")
    log.info(f"SAVED {sets_file}")

    if missing_details:
        missing_file = out_dir / f"{nuts3_id}_missing_lau.json"
        with open(missing_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "nuts3_id": nuts3_id,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "missing_lau_matches": missing,
                    "missing_lau_matches_details": missing_details,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        log.info(f"SAVED {missing_file}")
    
    log.info(f"TOTAL TIME {time.time() - start_time:.1f}s")


# ============================================================
# MAIN DRIVER (ALL NUTS3)
# ============================================================

if __name__ == "__main__":
    
    manifest = safe_get(GITHUB_BASE + 'nuts_lau_hierarchy.json', {})

    # --------------------------------------------------------
    # LOOP COUNTRIES → NUTS3 FILES
    # --------------------------------------------------------
    print("looping coutries...")
    for country_code in manifest["nuts0"]:
        if country_code == 'file': continue

        ### TESTING
        if country_code != "PT": continue

        OUTPUT_DIR = BASE_DIR / country_code

        country = manifest["nuts0"][country_code]

        print(f"\n==============================")
        print(f"Country: {country_code}")
        print(f"==============================")

        for nuts1_code in country["nuts1_ids"]:
            nuts1 = country["nuts1"][nuts1_code]
            
            ### TESTING
            # if nuts1_code != "PT1": continue

            print(f"\n==============================")
            print(f"NUTS1: {nuts1_code}")

            for nuts2_code in nuts1["nuts2_ids"]:
                nuts2 = nuts1["nuts2"][nuts2_code]
                
                ### TESTING
                # if nuts2_code != "PT1D": continue

                print(f"\n==============================")
                print(f"NUTS2: {nuts2_code}")

                #nuts3_file = safe_get(GITHUB_BASE + nuts2["file"])
                nuts3_gdf = gpd.read_file(GITHUB_BASE + nuts2["file"])

                for _, row in nuts3_gdf.iterrows():

                    nuts3_id = row["NUTS_ID"]
                    nuts3_label = row["NUTS_NAME"]

                    ### TESTING
                    # if nuts3_id != "PT1D2" and nuts3_id != "PT1D3" : continue

                    print(f"\n==============================")
                    print(f"NUTS3: {nuts3_id}")

                    nuts3 = nuts2["nuts3"][nuts3_id]
                    
                    #lau_file = safe_get(GITHUB_BASE + nuts3["file"])
                    lau_gdf = gpd.read_file(GITHUB_BASE + nuts3["file"])

                    bbox = row.geometry.bounds

                    process_nuts3(
                        nuts3_id=nuts3_id,
                        lau=lau_gdf,
                        bbox=bbox,
                        nuts3_geom=row.geometry,
                        out_dir=OUTPUT_DIR
                    )

