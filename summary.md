# CHIP — Cloud-Honest Image Pipeline · Summary

**Track B — "Fix It" · Tagline:** *Not "search is slow" — "search is confidently incorrect."*

---

## The Problem

Every remote-sensing pipeline filters scenes by `eo:cloud_cover`. That number describes a **110 km × 110 km** Sentinel-2 tile. An analyst's AOI is rarely more than 5–10 km wide. The filter has no idea where you are actually looking.

**Two real scenes, measured on real data:**

| Scene | AOI | Catalog `eo:cloud_cover` | True AOI cloud | Gap |
|-------|-----|--------------------------|----------------|-----|
| Punjab, 15 Jul 2024 | ~8 km × 7 km | **12.2%** — passes every filter | **82.7%** — solid cumulus | +70 pts |
| Willamette, 2 Aug 2024 | ~7 km × 7 km | **47.4%** — discarded by every filter | **1.9%** — virtually clear | −46 pts |

Punjab: an analyst publishes NDVI derived from cloud tops, not crops. **No error is raised.**  
Willamette: a perfect image is thrown away. **The analyst never knew it existed.**

A second silent bug compounds this: since ESA processing baseline 04.00 (Jan 2022), the correct reflectance formula is `(DN − 1000) / 10000`, not `DN / 10000`. On the clear Willamette scene, omitting the offset produces mean NDVI **0.299** instead of the correct **0.497** — a 40% underestimate that produces no crash and no warning.

---

## The Fix

CHIP takes one sentence and fixes both bugs:

```
NDVI over willamette, August 2024, only clear days
```

1. **Search without prejudice** — no `cloud_cover` filter sent to the catalog.  
2. **Score honestly** — read the SCL band over the exact AOI window for each candidate; compute true cloud fraction from pixel counts.  
3. **Pick the winner by the honest number** — sort by AOI cloud, not catalog claim.  
4. **Read the data correctly** — apply `BOA_ADD_OFFSET = −1000` for baselines ≥ 04.00; guard against zero-denominator explosions (`abs(denom) > 1e-4`).  
5. **QA report** — five named checks: reflectance offset impact, AOI vs catalog cloud gap, nodata/zero ambiguity, CRS confirmation, swath coverage.

---

## Architecture

```
sentence → parse() [rules regex or watsonx/Granite]
         → QuerySpec
         → pipeline.run()
               → STAC search (no cloud filter)
               → SCL window reads × ≤8 candidates
               → band reads (red/green/blue/nir 10m, scl 20m)
               → product PNG (true_color / ndvi / ndwi)
               → 5 QA checks
         → ChipResult → CLI / FastAPI / web UI
```

Stack: `rasterio`, `numpy`, `pillow`, `requests`, `fastapi`, `uvicorn`. No geopandas, no xarray, no database, no Docker, no build step.

Parser has two paths: deterministic regex (no API key required, demo-safe) and optional watsonx/Granite at temperature 0 — fully wrapped in `try/except`, falls back transparently.

---

## The Argument in One Table

The comparison table — `scene %` next to `AOI %`, gap in red or green — is the demo. A user who sees `scene = 12.2% / AOI = 82.7%` understands immediately why their past results were wrong. That is not a faster version of the existing system. That is a fundamentally different contract with the data.
