# CHIP — Implementation Plan

**Project:** Cloud-Honest Image Pipeline  
**Track:** Track B — "Fix It"  
**Budget:** 90 minutes  
**Critical path:** ENV → CHIP CORE → HONEST CLOUDS → QA REPORT → PARSER → API+PAGE → HARDENING  

> Pre-made material lives in `problem-data/`. First action of the build: copy it to `demo/`.

---

## Pre-work (before clocking 90 min)

Copy `problem-data/` → `demo/` so all scripts reference the right path:

```bash
cp -r problem-data demo
```

---

## TICKET-01 · Environment + Project Scaffolding
**Phase 0 | ~5 min | Cuttable: NO**

### Goal
Repo structure exists, Python 3.12 venv installs cleanly, smoke import works.

### Deliverables
- `app/__init__.py` — exact contents from §5 of BOB.md (env vars, constants, `BOA_ADD_OFFSET`, `SCL_CLOUD_CLASSES`)
- `app/models.py` — exact contents from §6 of BOB.md (`QuerySpec`, `Candidate`, `Check`, `ChipResult` dataclasses)
- `requirements.txt` — exact contents from §5 (`rasterio>=1.4`, `numpy>=2.0`, `pillow>=10.0`, `requests>=2.32`, `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `pytest>=8.0`)
- `.python-version` — contains `3.12`
- `.gitignore` — `.venv/`, `outputs/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.DS_Store`

### Implementation notes
- `app/__init__.py` sets `AWS_NO_SIGN_REQUEST`, `GDAL_DISABLE_READDIR_ON_OPEN`, `GDAL_HTTP_MULTIPLEX` via `os.environ.setdefault` — must run before any rasterio I/O.
- All constants in `__init__.py` are frozen — never change them during the build.
- Dataclasses in `models.py` are the contract between all modules — never add methods or logic here except the existing `disagreement` property.

### Acceptance check
```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run python -c "import rasterio, fastapi; print('ok')"
```
Prints `ok`. No import errors.

---

## TICKET-02 · Chip Core — STAC Search + COG Window Read + True-Colour PNG
**Phase 1 | ~20 min | Cuttable: NO**

### Goal
Given a sentence (via `--smoke`), download a COG window, stretch bands, write a real true-colour PNG.

### Deliverables

#### `app/search.py::search_items(bbox, start, end, limit=20) -> list[dict]`
- POST to `{STAC_API}/search` with `collections=[COLLECTION]`, `bbox`, `datetime=f"{start}T00:00:00Z/{end}T23:59:59Z"`, `limit`
- Sort by datetime descending
- Return `response.json()["features"]`
- Timeout 60 s
- **Do NOT filter on `eo:cloud_cover`** — that filter is the bug being fixed

#### `app/chip.py::read_window(href, bbox_4326) -> (array, crs, nodata)`
Exact implementation (verbatim):
```python
with rasterio.open(href) as src:
    b = transform_bounds("EPSG:4326", src.crs, *bbox_4326, densify_pts=21)
    win = from_bounds(*b, transform=src.transform)
    arr = src.read(1, window=win, boundless=True, fill_value=0)
return arr, str(src.crs), src.nodata
```
Imports: `from rasterio.warp import transform_bounds`, `from rasterio.windows import from_bounds`  
**`densify_pts=21` is mandatory** — UTM edges curve, 4-corner transform clips the AOI.

#### `app/chip.py::stretch(band, lo=2, hi=98) -> uint8 array`
Exact implementation (verbatim):
```python
v = band[band > 0]
if v.size == 0:
    return np.zeros_like(band, dtype="uint8")
p1, p2 = np.percentile(v, [lo, hi])
return np.clip((band - p1) / max(p2 - p1, 1e-6) * 255, 0, 255).astype("uint8")
```
Zeros excluded from percentile computation — they are nodata/fill, not real black pixels.

#### `app/chip.py::true_color_png(item, bbox, out_path) -> str`
- Read `red`, `green`, `blue` assets via `read_window`
- Apply `stretch` to each band
- Stack with `np.dstack`
- Save with PIL (`Image.fromarray`)
- Return `out_path`

#### `app/cli.py --smoke`
- Hard-coded: Willamette AOI `[-123.10, 44.52, -123.02, 44.58]`, date range `2024-08-01` → `2024-08-31`
- Calls `search_items`, takes first result, calls `true_color_png`, writes to `outputs/smoke_test.png`
- Print the item id and output path

### Acceptance check
```bash
mkdir -p outputs
uv run python -m app.cli --smoke
# → outputs/smoke_test.png exists and shows green farmland
```

---

## TICKET-03 · Honest Cloud Scoring — AOI Cloud Fraction + Candidate Ranking
**Phase 2 | ~15 min | Cuttable: NO — this is the product**

### Goal
For any scene, measure actual cloud cover over the user's AOI using the SCL band, not the catalog's scene-level `eo:cloud_cover`. This is the core differentiator.

### Deliverables

#### `app/search.py::aoi_cloud_fraction(item, bbox) -> (cloud_pct, valid_pct)`
Exact implementation (verbatim):
```python
scl, _, _ = read_window(item["assets"]["scl"]["href"], bbox)
valid = scl != SCL_NODATA
cloud = np.isin(scl, SCL_CLOUD_CLASSES) & valid
cloud_pct = 100.0 * cloud.sum() / max(valid.sum(), 1)
valid_pct = 100.0 * valid.sum() / valid.size
```
Returns `(cloud_pct, valid_pct)` as floats.

#### `app/search.py::rank_candidates(items, bbox, max_aoi_cloud=100.0, min_valid_pct=50.0) -> list[Candidate]`
- Probe **at most 8 items** (hard cap — keeps runtime under a few seconds)
- For each item, call `aoi_cloud_fraction` and build a `Candidate` dataclass
- Drop candidates where `valid_pct < min_valid_pct` (AOI straddles swath edge — stats meaningless)
- Sort remaining by `aoi_cloud_pct` ascending
- Mark winner (index 0) `selected=True`
- **Keep all losers** — the comparison table scene% vs aoi% is the demo's best visual
- Filter out candidates exceeding `max_aoi_cloud` AFTER building the comparison (never filter before measuring)

### Testing
Create `tests/test_acceptance.py` with exact contents from §13 of BOB.md (golden-number tests).

### Acceptance check
```bash
uv run pytest -k test_aoi_cloud_disagrees_with_scene -x -s
```
Output must show Punjab: `scene=12.2  aoi=82.7` and Willamette: `scene=47.4  aoi=1.9`.

---

## TICKET-04 · QA Report — Reflectance Offset, NDVI, Cloud-Mask Overlay
**Phase 3 | ~15 min | Cuttable: keep checks 1+2 only if short on time**

### Goal
Compute correct NDVI with the Sentinel-2 processing-baseline offset, generate product PNGs, and run five QA checks that name every silent error.

### Deliverables

#### `app/chip.py::to_reflectance(dn, apply_offset=True)`
```python
return (dn + BOA_ADD_OFFSET) / REFLECTANCE_SCALE if apply_offset else dn / REFLECTANCE_SCALE
```

#### `app/chip.py::ndvi(nir_dn, red_dn, apply_offset=True)` — guard is mandatory
```python
nir_r = to_reflectance(nir_dn, apply_offset)
red_r = to_reflectance(red_dn, apply_offset)
denom = nir_r + red_r
ok = np.abs(denom) > 1e-4      # applying the offset CAN drive this through zero
out = np.full_like(nir_r, np.nan)
out[ok] = np.clip((nir_r[ok] - red_r[ok]) / denom[ok], -1, 1)
return out
```
**Without the `ok` guard this returns ~2.6e5 on real data.** (Happened in prep — documented trap.)

#### `app/chip.py::upsample_mask(mask, shape)` — use verbatim
SCL is 20 m resolution; bands are 10 m. `np.kron` gives off-by-one (670 vs 671 pixels). Use PIL NEAREST resize instead:
```python
im = Image.fromarray(mask.astype("uint8") * 255).resize((shape[1], shape[0]), Image.NEAREST)
return np.array(im) > 127
```

#### `app/chip.py::ndvi_png(arr, out_path)`
Brown→yellow→green ramp over −0.2…0.9. NaN pixels render grey. No matplotlib — pure numpy interp:
```python
t = np.clip((ndvi + 0.2) / 1.1, 0, 1)
r = np.interp(t, [0,.5,1], [140,240,30])
g = np.interp(t, [0,.5,1], [100,230,120])
b = np.interp(t, [0,.5,1], [60,140,40])
# NaN → grey [128,128,128]
rgb = np.dstack([r,g,b]).astype("uint8")
```

#### `app/chip.py::cloudmask_png(rgb, cloud_mask, out_path)`
- Start from true-colour RGB array
- Tint cloud pixels `[255, 60, 60]` 
- Writes PNG showing what was excluded and why

#### `app/chip.py::write_geotiff(arr, src_profile, out_path)`
- Preserve CRS + transform from the window read
- No reprojection

#### `app/qa.py::run_checks(...) -> list[Check]`
Five checks, ordered `fail → warn → pass` in output:

1. **`reflectance_offset`** — if `s2:processing_baseline >= "04.00"` (both demo scenes are 05.10/05.11): compute mean NDVI with and without offset, report delta as evidence. Status always `warn` when offset applies. Message: "CHIP applied BOA_ADD_OFFSET=-1000; naive value would have been X."

2. **`aoi_cloud_vs_scene`** — warn when `abs(disagreement) > 15`. Name the direction explicitly:
   - positive gap: "catalog understates cloud over your AOI by N points; a `cloud_cover` filter would have let this through"
   - negative gap: "catalog overstates cloud by N points; a `cloud_cover` filter would have thrown this usable image away"

3. **`nodata_as_zero`** — `nodata == 0` while 0 is also a valid DN. Report % exact-zero pixels. Warn that mean-over-all-pixels is biased low.

4. **`crs_note`** — informational: state output CRS, confirm no resampling was performed (AOI bounds were transformed, not the raster).

5. **`valid_pixels`** — `fail` if `valid_pct < 50%`; AOI is off the swath edge and all statistics are meaningless.

### Testing
```bash
uv run pytest -k "offset or guarded" -x -s
```
Must show: correct NDVI ≈ 0.497, naive ≈ 0.299, delta > 0.15.

### Acceptance check
- Punjab: 3 warnings (reflectance_offset + aoi_cloud_vs_scene + nodata_as_zero)
- Willamette: reflectance_offset reports delta 0.299 → 0.497

---

## TICKET-05 · Sentence Parser — Rules Engine + Optional LLM
**Phase 4 | ~15 min | LLM path cuttable; rules path never cut**

### Goal
Convert a natural-language sentence into a `QuerySpec`. Rules parser covers all six demo queries without any API key. LLM is an optional enhancement only.

### Deliverables

#### `app/parse.py::load_aois(path="demo/aois.geojson") -> dict[str, list[float]]`
- Parse GeoJSON, extract each feature's polygon coordinates
- Derive bbox `[w, s, e, n]` from coordinate min/max
- Return `{name.lower(): bbox}` dict
- Match against sentence via case-insensitive substring

#### `app/parse.py::parse_rules(text) -> QuerySpec`
~60 lines of regex. Must cover all six queries in `demo/queries.txt`:

Helper constant (include at top of file):
```python
MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july",
     "august","september","october","november","december"], start=1)}
```

Rules to implement:
- **AOI:** case-insensitive substring match on names from `aois.geojson`, or `bbox=[w,s,e,n]` literal
- **Dates:**
  - `"<month> <year>"` → first to last day of that month
  - `"last <month>"` → most recent occurrence of that month
  - `"summer 2024"` → 2024-06-01 to 2024-08-31
  - `"on 15 July 2024"` or `"on <day> <month> <year>"` → single day (start=end=date)
  - bare `"<year>"` → Jan 1 to Dec 31 of that year
  - default (no date found) → last 90 days
- **Product:** `"ndvi"` → `ndvi`; `"ndwi"` → `ndwi`; else → `"true_color"`
- **Clear intent:** any of `"only clear"`, `"clear day"`, `"cloud-free"`, `"cloud free"`, `"clear sky"`, `"no cloud"` → `max_aoi_cloud = 10`

#### `app/parse.py::parse_llm(text, aois) -> QuerySpec | None`
- Only runs when `WATSONX_API_KEY` env var is set
- Calls watsonx / Granite, temperature 0, requests strict JSON matching `QuerySpec` fields
- **Entire function body wrapped in `try/except Exception`** — returns `None` on ANY error or schema mismatch
- Never raises into the request path

#### `app/parse.py::parse(text) -> QuerySpec`
- Try LLM if `WATSONX_API_KEY` set and result is valid
- Fall back to `parse_rules` always
- Always returns a usable spec

### Testing
```bash
uv run pytest -k test_rules_parser -x -s
```
Must pass with `WATSONX_API_KEY` unset.

### Acceptance check
All 6 queries in `demo/queries.txt` parse correctly with LLM disabled.

---

## TICKET-06 · Pipeline + FastAPI + Web UI
**Phase 5 | ~15 min | Web page cuttable → fall back to CLI; pipeline+CLI never cut**

### Goal
Wire all phases into a single `run()` function. Expose via FastAPI. Build a single-file dark web UI with the comparison table as the hero element.

### Deliverables

#### `app/pipeline.py::run(spec, out_dir="outputs") -> ChipResult`
Sequence: `search_items → rank_candidates → read bands → build product → run QA → ChipResult`

Cache key: `f"{item_id}_{bbox_hash}_{product}"` — re-run during demo must be instant.

Offline mode: if `CHIP_OFFLINE=1` env var set, serve hero cases from:
- `demo/cache/*_item.json` — real STAC item JSON
- `demo/assets/*.png` — pre-generated chips
- **No network calls at all** — entire function works air-gapped

#### `app/main.py` — FastAPI, three endpoints
| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/aois` | — | `[{name, bbox, label}]` for dropdown |
| `POST /api/parse` | `{text}` | `QuerySpec` as JSON, so UI can show before committing |
| `POST /api/chip` | `{spec}` | `ChipResult` as JSON |

Mount `outputs/` at `/outputs` and `web/` at `/` with `StaticFiles`. Serve `web/index.html` at `/`.

#### `web/index.html` — single file, plain JS, no framework, no build step
Layout (top to bottom):
1. Text input + **Run** button + 6 clickable example query chips from `demo/queries.txt`
2. "Here's what I understood" — parsed `QuerySpec` JSON shown before running
3. **THE COMPARISON TABLE** — every candidate with `scene %` next to `AOI %`, gap highlighted in red/green. **Most visual weight on page.** This is the screenshot for the deck.
4. Image previews: true colour | NDVI/product | cloud-mask overlay (tab or side-by-side)
5. QA checks table, colour-coded: red=fail, orange=warn, green=pass
6. Download GeoTIFF link

Style: dark background, monospace font, single accent colour (teal or amber). Looks like a tool, not a landing page. No map component.

#### `app/cli.py` — full terminal entry point (extends `--smoke` from TICKET-02)
```bash
uv run python -m app.cli "NDVI over willamette, August 2024"
```
Prints:
- Parsed spec
- Candidate table: `id | scene% | aoi% | gap | selected`
- QA checks: `id | status | message`
- Output file paths

**This CLI printout alone is a credible demo if the web page never happens.**

### Acceptance check
```bash
uv run uvicorn app.main:app --reload
# Open http://localhost:8000
# Type: "NDVI over willamette, August 2024, only clear days"
# → comparison table shows scene=47.4 / aoi=1.9, NDVI PNG appears
```

---

## TICKET-07 · Demo Hardening + Offline Mode
**Phase 6 | ~5 min | Cuttable: NO — conference wifi kills demos**

### Goal
Both hero queries run instantly from cache. App works with wifi physically off.

### Deliverables

1. **Cache warm** — run both hero queries before the demo so `outputs/` already has the results:
   ```bash
   uv run python -m app.cli "true color over punjab on 15 July 2024"
   uv run python -m app.cli "NDVI over willamette, August 2024, only clear days"
   ```

2. **Offline mode verification** — set `CHIP_OFFLINE=1`, disable wifi, both hero queries return cached assets with zero network calls:
   ```bash
   CHIP_OFFLINE=1 uv run python -m app.cli "true color over punjab on 15 July 2024"
   CHIP_OFFLINE=1 uv run python -m app.cli "NDVI over willamette, August 2024, only clear days"
   ```

3. **Full test suite**:
   ```bash
   uv run pytest tests/ -v
   # All 6 tests must pass (2 cloud disagreement + 1 offset + 1 zero-denominator + 3 parser)
   ```

4. **Dry run** — run `demo/script.md` out loud once end to end before presenting.

### Acceptance check
With laptop wifi turned off: both hero queries complete, show correct numbers, show pre-generated PNGs.

---

## Dependency map

```
TICKET-01 (env + models)
    └── TICKET-02 (chip core)
            └── TICKET-03 (honest clouds)
                    └── TICKET-04 (QA report)
                            └── TICKET-05 (parser)
                                    └── TICKET-06 (pipeline + API + UI)
                                            └── TICKET-07 (hardening)
```

Strictly sequential. Never debug two phases simultaneously.

---

## Cut order (if behind)

1. `parse_llm` — rules-only parser; nobody can tell on stage
2. NDWI product — keep NDVI only
3. GeoTIFF download — show PNG only
4. Comparison *table* — keep the two numbers as text in the CLI
5. Web page entirely → demo the CLI; it's a developer tool

**Never cut:** AOI-vs-scene cloud comparison (TICKET-03) and reflectance-offset check (TICKET-04, check #1). Those two are the reason this wins.

---

## Known traps (pre-solved, from §17 of BOB.md)

| Trap | Solution |
|---|---|
| rasterio won't install | Python **3.12**, not 3.14 |
| COG opens are slow | `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` — already in `__init__.py` |
| 403 from S3 | `AWS_NO_SIGN_REQUEST=YES` — already in `__init__.py` |
| SCL is 20 m, bands are 10 m | PIL NEAREST resize to exact band shape. `np.kron` gives off-by-one. |
| NDVI explodes to 2.6e5 | `abs(denom) > 1e-4` guard — mandatory in TICKET-04 |
| AOI clipped at the edges | `densify_pts=21` in `transform_bounds` — mandatory in TICKET-02 |
| Asset names differ per catalog | Earth Search uses `red`/`nir`/`scl` — only use Earth Search |
| Nothing returned from STAC | Widen date range before debugging anything else |
| Demo query runs twice slowly | Cache key in `pipeline.py::run` — second run must be instant |
| Conference wifi down | `CHIP_OFFLINE=1` + pre-warmed cache — TICKET-07 |
