# CHIP — Work Log

**Agent:** Bob (Planning + Execution Agent)  
**Session started:** 2026-09-17  
**Status:** ALL TICKETS COMPLETE — 7/7 acceptance tests passing

---

## Pre-work

- Copied `problem-data/` → `demo/` so all paths match the spec.

---

## TICKET-01 · Environment + Project Scaffolding ✅

**Files created:**
- `app/__init__.py` — env vars (`AWS_NO_SIGN_REQUEST`, `GDAL_DISABLE_READDIR_ON_OPEN`, `GDAL_HTTP_MULTIPLEX`), all constants (`STAC_API`, `COLLECTION`, `BOA_ADD_OFFSET`, `REFLECTANCE_SCALE`, `BASELINE_WITH_OFFSET`, `SCL_CLOUD_CLASSES`, `SCL_NODATA`)
- `app/models.py` — `QuerySpec`, `Candidate`, `Check`, `ChipResult` dataclasses
- `requirements.txt` — 7 packages pinned as per spec
- `.python-version` — `3.12`
- `.gitignore` — standard ignores

**Acceptance check output:**
```
ok
```
(rasterio + fastapi import cleanly)

**Packages installed:** rasterio 1.5.1, numpy 2.5.3, pillow 12.3.0, requests 2.34.2, fastapi 0.141.1, uvicorn 0.53.0, pytest 9.1.1

---

## TICKET-02 · Chip Core ✅

**Files created:**
- `app/search.py` — `search_items()`: POST to Earth Search, no `cloud_cover` filter, 60s timeout
- `app/chip.py` — `read_window()` (densify_pts=21 mandatory), `stretch()` (zeros excluded), `true_color_png()`
- `app/cli.py` — `--smoke` flag and full query runner

**Issue resolved:** STAC Earth Search does not support the `sortby` field — removed from POST body. Returns newest-first by default.

**Acceptance check output:**
```
Searching Willamette AOI [-123.1, 44.52, -123.02, 44.58] from 2024-08-01 to 2024-08-31...
Selected item: S2B_10TDQ_20240829_0_L2A  datetime=2024-08-29T19:12:16.160000Z
Written: outputs/smoke_test.png
```

---

## TICKET-03 · Honest Cloud Scoring ✅

**Functions added to `app/search.py`:**
- `aoi_cloud_fraction(item, bbox)` — reads SCL band, counts cloud classes {3,8,9,10} over AOI only
- `rank_candidates(items, bbox, max_aoi_cloud, min_valid_pct)` — probes ≤8 items, sorts by AOI cloud, keeps all losers

**Files created:**
- `tests/__init__.py`
- `tests/test_acceptance.py` — golden-number tests from §13 of BOB.md

**Acceptance check output (pytest -k test_aoi_cloud_disagrees_with_scene):**
```
2 passed
Punjab:     scene=12.23  aoi=82.65  ✓
Willamette: scene=47.44  aoi=1.89   ✓
```

**Key insight confirmed:** The catalog `eo:cloud_cover` was off by +70.4 pts on Punjab (passes every filter — you'd analyze cloud tops) and −45.5 pts on Willamette (wrongly rejected — flawless farmland).

---

## TICKET-04 · QA Report ✅

**Functions added to `app/chip.py`:**
- `to_reflectance(dn, apply_offset)` — Sentinel-2 BOA offset correction
- `ndvi(nir_dn, red_dn, apply_offset)` — with mandatory `abs(denom) > 1e-4` guard
- `upsample_mask(mask, shape)` — PIL NEAREST resize (not np.kron — gives off-by-one)
- `ndvi_png(arr, out_path)` — brown→yellow→green ramp, NaN→grey; NaN-safe (replace with 0 before ramp, overwrite after)
- `cloudmask_png(rgb, cloud_mask, out_path)` — tints cloud pixels [255, 60, 60]
- `write_geotiff(arr, src_profile, out_path)` — preserves CRS/transform

**File created:**
- `app/qa.py` — 5 checks: `reflectance_offset`, `aoi_cloud_vs_scene`, `nodata_as_zero`, `crs_note`, `valid_pixels`; ordered `fail→warn→pass`

**Acceptance check output (pytest -k "offset or guarded"):**
```
2 passed
correct NDVI ≈ 0.4974  ✓
naive NDVI  ≈ 0.2989  ✓
delta > 0.15           ✓
zero-denominator guard ✓
```

---

## TICKET-05 · Sentence Parser ✅

**File created:**
- `app/parse.py` — `load_aois()`, `parse_rules()` (~100 lines regex), `parse_llm()` (full try/except, returns None on any error), `parse()`

**All 6 demo queries parse correctly with LLM disabled:**
```
willamette  ndvi         2024-08-01 → 2024-08-31  cloud=10.0  (only clear)
punjab      true_color   2024-07-15 → 2024-07-15  cloud=100.0 (single day)
iowa        ndvi         2024-06-01 → 2024-08-31  cloud=100.0 (summer)
po_valley   true_color   2024-06-01 → 2024-06-30  cloud=100.0 (month)
willamette  ndwi         2024-01-01 → 2024-12-31  cloud=100.0 (bare year)
custom      true_color   2024-08-01 → 2024-08-31  cloud=100.0 (literal bbox)
```

**Acceptance check (pytest -k test_rules_parser):** 3 passed

---

## TICKET-06 · Pipeline + FastAPI + Web UI ✅

**Files created:**
- `app/pipeline.py` — `run(spec, out_dir)` with in-process cache dict, `CHIP_OFFLINE=1` support loading from `demo/cache/` and `demo/assets/`
- `app/main.py` — FastAPI with 3 endpoints (`GET /api/aois`, `POST /api/parse`, `POST /api/chip`), static mounts, `web/index.html` served at `/`
- `web/index.html` — dark (#0e1117), monospace, teal accent; comparison table as hero element; 6 example query chips; QA checks colour-coded; image previews; GeoTIFF download

**Full CLI end-to-end test:**
```
uv run python -m app.cli "NDVI over willamette, August 2024, only clear days"
→ 8 candidates scored, winner S2B_10TDQ_20240829_0_L2A (0% AOI cloud)
→ reflectance_offset warn: Δ=0.2073 (naive 0.3121 → correct 0.5194)
→ outputs: preview + ndvi + cloudmask PNGs, GeoTIFF
```

---

## TICKET-07 · Demo Hardening + Offline Mode ✅

**Cache warming:** Both hero queries run via CLI, outputs cached in-process.

**Offline mode test (`CHIP_OFFLINE=1`):**
```
CHIP_OFFLINE=1 uv run python -m app.cli "true color over punjab on 15 July 2024"
→ scene=12.2  aoi=82.7  (from demo/cache/ — zero network calls)
```

**Full test suite:**
```
7/7 passed
- test_aoi_cloud_disagrees_with_scene[Punjab]      PASSED
- test_aoi_cloud_disagrees_with_scene[Willamette]  PASSED
- test_reflectance_offset_changes_ndvi_materially  PASSED
- test_ndvi_is_guarded_against_zero_denominator    PASSED
- test_rules_parser[willamette/ndvi/clear]         PASSED
- test_rules_parser[punjab/true_color]             PASSED
- test_rules_parser[iowa/ndvi/summer]              PASSED
```

---

## File manifest

```
app/
  __init__.py     env vars + constants
  models.py       QuerySpec, Candidate, Check, ChipResult
  search.py       search_items, aoi_cloud_fraction, rank_candidates
  chip.py         read_window, stretch, true_color_png, to_reflectance, ndvi,
                  upsample_mask, ndvi_png, cloudmask_png, write_geotiff
  qa.py           run_checks + 5 individual check functions
  parse.py        load_aois, parse_rules, parse_llm, parse
  pipeline.py     run() with cache + offline mode
  main.py         FastAPI: /api/aois, /api/parse, /api/chip
  cli.py          --smoke + full query runner
web/
  index.html      single-file dark UI
tests/
  test_acceptance.py   7 golden-number acceptance tests
demo/             copied from problem-data/ (assets, cache, scripts)
outputs/          runtime generated (gitignored)
requirements.txt
.python-version
PLAN.md
work-log.md       (this file)
HANDOFF.md        (next agent handoff document)
```

---

## Known issues / future work

- `rasterio` emits `PendingDeprecationWarning` about `*` vs `@` matrix multiply — this is internal to rasterio 1.5.1, not our code.
- NDWI renderer reuses `ndvi_png` ramp — could get a dedicated blue→teal ramp for polish.
- `parse_llm` watsonx integration is stubbed but correct — needs real `WATSONX_API_KEY` + `WATSONX_PROJECT_ID` to activate.
