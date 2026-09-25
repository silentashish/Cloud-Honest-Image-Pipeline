# CHIP — Cloud-Honest Image Pipeline
## Presentation Document

**Track:** Track B — "Fix It"  
**Tagline:** *Not "search is slow" — "search is confidently incorrect."*

---

## 1. The Problem Nobody Talks About

Every remote-sensing workflow in the world starts the same way.

An analyst wants to look at a field — a 5 km × 5 km patch of farmland in Punjab, or a stretch of the Willamette Valley in Oregon. They open a STAC catalog, filter scenes by `eo:cloud_cover < 20`, download whatever comes back, run their index computation, and report a number.

The number is wrong. Not because of a code bug. Because the filter itself is broken.

### What `eo:cloud_cover` actually measures

Sentinel-2 tiles are 100 km × 110 km. The `eo:cloud_cover` value attached to each scene is the fraction of that entire tile covered by clouds. It is computed by the European Space Agency at scene ingestion time using the Scene Classification Layer (SCL), averaged over every pixel in the 110 km frame.

An analyst's area of interest is rarely more than 5–10 km wide.

The consequence is simple and severe: **the cloud number that drives every filter in every pipeline describes a fundamentally different geographic area than the area being analysed.**

A cloud system 80 km to the east of your field pushes your scene's `eo:cloud_cover` up. Clear sky directly over your field counts for almost nothing. The filter has no knowledge of where you are looking.

---

## 2. The Evidence — Two Real Scenes, Measured

These numbers are not illustrative. They were computed on 2026-09-17 from real Sentinel-2 L2A Cloud Optimized GeoTIFFs on AWS via the Earth Search catalog. Every value is reproducible.

### Scene 1 — Punjab, India (15 July 2024)

**Scene ID:** `S2A_43REQ_20240715_0_L2A`  
**AOI:** `[75.80°E, 30.88°N, 75.88°E, 30.94°N]` — ~8 km × 7 km, Ludhiana district

| Metric | Value |
|--------|-------|
| `eo:cloud_cover` (catalog claim) | **12.23%** |
| True cloud fraction over AOI | **82.65%** |
| Gap | **+70.4 percentage points** |

The catalog says 12% cloud. The scene passes every `cloud_cover < 20` filter ever written. In reality, 83% of the analyst's field is cloud. The image is solid cumulus. Mid-July is the height of Punjab's *kharif* (summer crop) season — the exact moment analysts need accurate NDVI to assess crop health. They download this image, compute NDVI, and publish a number derived from the reflectance of cloud tops, not crops.

**There is no error message. The pipeline completes successfully.**

### Scene 2 — Willamette Valley, Oregon (2 August 2024)

**Scene ID:** `S2B_10TDQ_20240802_0_L2A`  
**AOI:** `[-123.10°W, 44.52°N, -123.02°W, 44.58°N]` — ~7 km × 7 km, mixed agriculture

| Metric | Value |
|--------|-------|
| `eo:cloud_cover` (catalog claim) | **47.44%** |
| True cloud fraction over AOI | **1.89%** |
| Gap | **−45.5 percentage points** |

The catalog says 47% cloud. The scene fails the `cloud_cover < 20` filter and is discarded. The analyst waits another week for the next overpass. In reality, the AOI is virtually cloud-free — flawless farmland at peak summer greenness.

**A perfect image was thrown away. The analyst never knew it existed.**

### Scale of the problem

These are not cherry-picked edge cases. Scanning 4 AOIs × 12 cloudy scenes:

> The scene-level `eo:cloud_cover` was off by more than 15 percentage points in **over half** of all cases.

The filter is wrong more often than it is right in cloudy conditions — which are exactly the conditions when the filter matters most.

---

## 3. The Second Silent Bug — Reflectance Encoding

The cloud scoring problem is the headline. But there is a second error that has affected every Sentinel-2 analysis pipeline published since 2022 that hasn't been explicitly patched.

### The BOA_ADD_OFFSET

ESA introduced a change in Sentinel-2 processing **baseline 04.00** (January 2022). Prior to this baseline, reflectance was computed as:

```
ρ = DN / 10000
```

From baseline 04.00 onwards, the correct formula is:

```
ρ = (DN + BOA_ADD_OFFSET) / 10000
  = (DN − 1000) / 10000
```

The `BOA_ADD_OFFSET = −1000` is encoded in the scene metadata (`MTD_MSIL2A.xml`) and in the STAC item's `s2:processing_baseline` property. Both demo scenes are baseline `05.10` and `05.11` respectively — well past the threshold.

The change was documented in [ESA's Level-2A Product Format Specification](https://sentinel.esa.int/documents/247904/685211/Sentinel-2-Products-Specification-Document), but it requires reading a dense PDF, and no existing open-source pipeline raises a warning if you forget it.

### What this does to NDVI

NDVI (Normalized Difference Vegetation Index) is computed as:

```
NDVI = (NIR − Red) / (NIR + Red)
```

On the clear Willamette scene (`S2B_10TDQ_20240802_0_L2A`), both formulas produce a number. Neither crashes. Both look reasonable:

| Method | Mean NDVI over AOI |
|--------|---------------------|
| Naive: `DN / 10000` | **0.2989** |
| Correct: `(DN − 1000) / 10000` | **0.4974** |

**The naive value is 40% low.** An analyst publishing `NDVI = 0.30` for peak-summer Oregon farmland would not find it suspicious — it falls in a normal range. The correct value is `0.497`.

This is the "wrong but plausible" class of bug. It produces no crash, no warning, and an output that sits comfortably in the expected range. It is invisible unless you already know to look for it.

### The zero-denominator trap

Applying the `−1000` offset to dark pixels (bare soil, shadows, water) can drive `NIR + Red` through zero. Unguarded division then returns values on the order of `2.6 × 10⁵`. CHIP includes a mandatory guard:

```python
ok = np.abs(denom) > 1e-4
out[ok] = np.clip((nir_r[ok] - red_r[ok]) / denom[ok], -1, 1)
```

Pixels that fail the guard are returned as `NaN` and rendered grey in the output image. This happened during development on real data — it is not a theoretical edge case.

---

## 4. The Third Silent Failure — Cloud Contamination Poisons Statistics

When 83% of your field is cloud, what does mean NDVI tell you?

Punjab AOI, 15 July 2024, correctly offset:

| Pixel set | Mean NDVI |
|-----------|-----------|
| All pixels (82.7% cloud) | **−0.089** |
| Clear pixels only | **+0.034** |

**The sign flips.** Mid-July is peak *kharif* season in Punjab. Both numbers are wrong for different reasons:

- `-0.089` is driven down by cloud reflectance. Clouds have low NIR and high visible reflectance, which produces negative NDVI.
- `+0.034` is a valid measurement, but represents barely 17% of the AOI under heavy cloud shadow and haze — not a reliable crop health indicator.

The point: even a correctly-computed NDVI over a mostly-cloudy AOI is meaningless. A pipeline that doesn't report valid-pixel count alongside its statistics is hiding this from the analyst.

---

## 5. The Old Way

```python
# THE OLD WAY — 81 lines to get one NDVI chip
# Contains two silent bugs. Neither one crashes.

r = requests.post(
    "https://earth-search.aws.element84.com/v1/search",
    json={
        "collections": ["sentinel-2-l2a"],
        "bbox": BBOX,
        "datetime": f"{START}T00:00:00Z/{END}T23:59:59Z",
        # BUG: eo:cloud_cover describes a 110×110 km scene, not this 7 km AOI.
        # This line silently discards usable images AND admits unusable ones.
        "query": {"eo:cloud_cover": {"lt": MAX_CLOUD}},
    },
)

# ...30 more lines of asset lookup, window reading, CRS handling...

# BUG: baseline >= 04.00 requires BOA_ADD_OFFSET = -1000 before dividing.
# This is documented in a PDF. Omitting it moves mean NDVI from 0.497 to 0.299
# and raises no error of any kind.
red_r = red / 10000.0
nir_r = nir / 10000.0

ndvi = (nir_r - red_r) / (nir_r + red_r)
print("mean NDVI:", np.nanmean(ndvi))
```

This is not a strawman. It is the honest minimum to get one cloud-masked NDVI chip out of Sentinel-2. The two bugs marked are real, reproducible, and affect every pipeline written this way since January 2022. Neither one produces an error. Both produce an answer that looks completely reasonable.

And next week, the analyst writes it again — because it lived in a notebook.

---

## 6. CHIP — The Fix

CHIP replaces this with one sentence:

```
NDVI over willamette, August 2024, only clear days
```

Output:
- The parsed intent, shown before running (so the analyst can verify the system understood them)
- Every candidate scene scored honestly — `scene %` next to `AOI %`, gap highlighted
- The winner: the scene the `cloud_cover` filter would have thrown away
- Correct reflectance, correct NDVI
- A QA report that names what went wrong and what was fixed

### How it works

**Step 1 — Search without prejudice**

CHIP POSTs to the Earth Search STAC catalog with no cloud filter. The entire catalog is the candidate pool.

**Step 2 — Score each candidate honestly**

For each candidate (up to 8), CHIP reads the SCL (Scene Classification Layer) asset over the exact AOI window using a windowed COG read. It counts pixels classified as cloud shadow (class 3), medium-probability cloud (8), high-probability cloud (9), and thin cirrus (10), divided by total valid pixels in the window.

This takes a few seconds per scene because the SCL is 20 m resolution and the window is small. It is the only correct way to answer "how cloudy is my field."

```python
scl, _, _ = read_window(item["assets"]["scl"]["href"], bbox)
valid = scl != SCL_NODATA
cloud = np.isin(scl, SCL_CLOUD_CLASSES) & valid
cloud_pct = 100.0 * cloud.sum() / max(valid.sum(), 1)
```

**Step 3 — Pick the winner by the honest number**

Candidates are sorted by AOI cloud fraction, not by catalog claim. The comparison table shows both numbers for every candidate — the gap is the point, and hiding it would defeat the purpose.

**Step 4 — Read the data correctly**

Reflectance is computed as `(DN − 1000) / 10000` for processing baselines ≥ 04.00. The offset is checked from the STAC item metadata. NDVI includes the zero-denominator guard. Cloud pixels are excluded from statistics but rendered in the output so the analyst can see what was masked.

**Step 5 — The QA report**

Five checks run on every result:

| Check | What it catches |
|-------|-----------------|
| `reflectance_offset` | Whether the BOA offset was needed and what NDVI would have been without it |
| `aoi_cloud_vs_scene` | Whether the catalog cloud number disagrees with the AOI measurement by more than 15 points, and in which direction |
| `nodata_as_zero` | Whether the nodata sentinel (0) is indistinguishable from a valid DN, biasing statistics |
| `crs_note` | Confirms the output CRS and that no raster reprojection was performed |
| `valid_pixels` | Fails if less than 50% of the AOI has valid pixels — the AOI is off the swath edge |

Checks are ordered `fail → warn → pass`. The two that matter most for the demo are always shown first.

---

## 7. Technical Architecture

```
sentence
  → parse()              rules regex or watsonx/Granite (temperature 0)
  → QuerySpec
  → pipeline.run()
      → search_items()       STAC POST, no cloud filter, 60s timeout
      → rank_candidates()    aoi_cloud_fraction() × ≤8 scenes
          → read_window()    COG window via rasterio, densify_pts=21
      → read bands           red, green, blue, nir (10 m), scl (20 m)
      → product rendering    true_color / ndvi / ndwi PNGs
      → run_checks()         5 QA checks
  → ChipResult
  → CLI printout / FastAPI JSON / web UI
```

### Key technical decisions

**No raster reprojection.** Sentinel-2 scenes are in UTM CRS. Rather than reproject the raster to EPSG:4326, CHIP transforms the AOI bounding box into the scene's native CRS and reads that window. This is correct and produces ~100× less code. The `densify_pts=21` parameter in `transform_bounds` is non-obvious but mandatory — UTM zone boundaries curve, and a 4-corner transform clips the AOI on scenes near the edge of a zone.

**No mosaic.** One scene per answer. If the AOI spans two UTM zones, the best single scene is returned. Mosaicking introduces resampling artifacts and roughly doubles the complexity for a demo.

**COG windowed reads.** Cloud Optimized GeoTIFFs support HTTP range requests — only the bytes covering the AOI window are downloaded. A typical 10 m band read over a 7 km AOI window fetches a few hundred kilobytes from S3, not the full 1 GB scene file. The `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` environment variable prevents GDAL from issuing additional HTTP requests to enumerate the directory, roughly halving latency.

**SCL resolution mismatch.** The SCL band is 20 m; NIR and Red are 10 m. Upsampling with `np.kron` produces an off-by-one error on real data (670 vs 671 pixels, verified during development). CHIP uses PIL's `NEAREST` resize to the exact target shape.

**In-process result cache.** Once a query has been run, the `ChipResult` is cached by a key of `(aoi_name, bbox_hash, product, start, end, max_aoi_cloud)`. Re-running the same query during a demo is instant. The cache key includes `max_aoi_cloud` so "clear days" and "any cloud" queries for the same scene are stored separately.

**Offline mode.** `CHIP_OFFLINE=1` serves both hero cases from `demo/cache/` and `demo/assets/` with zero network calls. Conference wifi is the most reliable way to lose a hackathon.

### No unnecessary dependencies

```
rasterio>=1.4      COG reads and CRS transforms
numpy>=2.0         array arithmetic
pillow>=10.0       PNG output, SCL mask upsampling
requests>=2.32     STAC HTTP calls
fastapi>=0.115     web API
uvicorn>=0.30      ASGI server
pytest>=8.0        acceptance tests
```

No geopandas. No xarray. No odc-stac. No stackstac. No database. No Docker. No frontend build step.

---

## 8. The IBM / watsonx Integration

The sentence parser has two paths:

**Primary: deterministic rules** (`parse_rules`). Regex covering the six demo query patterns — month/year dates, "summer", "on N Month YYYY", bare year, product names, clear-sky intent phrases, and literal `bbox=[w,s,e,n]`. Runs with no API key. The demo cannot die from a token expiry.

**Optional: Granite LLM** (`parse_llm`). When `WATSONX_API_KEY` is set, CHIP calls the IBM watsonx endpoint with a Granite model at temperature 0, requesting strict JSON matching the `QuerySpec` schema. The entire function is wrapped in `try/except` — it returns `None` on any failure (network error, schema mismatch, timeout) and falls back to the rules parser transparently. The LLM path extends coverage to phrasing the rules don't handle; the rules path is the guarantee.

The `parse()` dispatcher:

```python
def parse(text: str) -> QuerySpec:
    aois = load_aois()
    if os.environ.get("WATSONX_API_KEY"):
        result = parse_llm(text, aois)
        if result is not None:
            return result
    return parse_rules(text)
```

Granite's contribution is robustness at the edges. The core scientific contribution — AOI-honest cloud scoring, offset-corrected NDVI, the QA report — is entirely deterministic and requires no model.

---

## 9. Acceptance Criteria — Golden Numbers

The test suite asserts the three findings against real data. These numbers don't change; they're facts about specific satellite scenes.

```python
# Finding 1: the catalog cloud number is wrong in both directions
("S2A_43REQ_20240715_0_L2A", PUNJAB,      scene=12.23, aoi=82.65)  # false-clear
("S2B_10TDQ_20240802_0_L2A", WILLAMETTE,  scene=47.44, aoi=1.89)   # wrongly rejected

# Finding 2: the reflectance offset moves NDVI by 40%
correct = nanmean(ndvi(nir, red, apply_offset=True))   # ≈ 0.4974
naive   = nanmean(ndvi(nir, red, apply_offset=False))  # ≈ 0.2989
assert correct - naive > 0.15

# Finding 3: the zero-denominator guard prevents explosion
nir = [[1000.0, 1200.0]]  # → 0.0 reflectance after offset
red = [[1000.0,  800.0]]
out = ndvi(nir, red, apply_offset=True)
assert all(isnan(out) | (abs(out) <= 1.0))
```

All 7 acceptance tests pass against live data.

---

## 10. What CHIP Is Not

**Not a library.** CHIP is a demo of a concept, not a production system. It handles one collection (Sentinel-2 L2A), one data source (Earth Search), one scene per query. It is not mosaicking, it is not time-series, it is not a generic EO ingestion framework.

**Not a replacement for existing tools.** pystac-client, stackstac, odc-stac, and similar tools fetch pixels correctly. They don't recompute cloud cover over your AOI, and they don't warn you about the offset. The plumbing is solved. The honesty is not.

**Not slow search made fast.** The latency on the first query is 10–30 seconds — it reads SCL windows from S3 for up to 8 candidates. The contribution is not speed. It is accuracy: getting the right answer about which scene to use, and being honest about the content of that scene before you compute anything with it.

---

## 11. Why This Wins

The hackathon track is "Fix It." Three things are broken in the standard remote-sensing workflow. CHIP fixes all three, demonstrates the fix with reproducible real numbers, and builds the demonstration in one sentence of user input.

**The broken thing is not the search.** The broken thing is the implicit contract: that a number called `cloud_cover` attached to an image describes the cloudiness of your area of interest. It doesn't. It never did. Every analyst who has used it to filter images has been filtering on the wrong thing.

CHIP doesn't just fix the workflow. It makes the break visible — the comparison table showing `scene = 12.2%` next to `AOI = 82.7%` is the argument, not just an output. A user who sees that table understands immediately why their past results were wrong, and why they shouldn't trust any catalog-level cloud filter without checking.

That is not a faster version of the existing system. That is a fundamentally different contract with the data.

---

## 12. Reproducibility

All numbers in this document are reproducible:

```bash
# Reproduce the cloud scoring findings
uv run python demo/reproduce_scan.py

# Reproduce the assets and evidence.json
uv run python demo/reproduce_assets.py

# Run the acceptance test suite against live data
uv run pytest tests/ -v
```

Raw STAC items: `demo/cache/punjab_false_clear_item.json`, `demo/cache/willamette_wrongly_rejected_item.json`  
Raw measurements: `demo/cache/evidence.json`  
Pre-generated chips: `demo/assets/`

---

## 13. One-Sentence Summary

**CHIP gives you analysis-ready satellite data from a single natural-language sentence, with cloud cover measured over your actual area of interest, and an honest account of every silent error the standard workflow would have made.**
