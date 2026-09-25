# CHIP — Cloud-Honest Image Pipeline
## Single-file build spec. Everything Bob needs is in this document.

**Hackathon track:** Track B — "Fix It" (take something broken, slow or annoying and reinvent it).
**Budget:** 90 minutes.
**Pre-made material that already exists in `demo/` — do not regenerate it:** real satellite
chips, cached STAC items, verified numbers, the pitch script, and the "before" script.

---

# 1. What you are building and why

To get **one** usable satellite image, a remote-sensing analyst writes ~80 lines of the
same script they wrote last month: search a STAC catalog, filter on cloud cover, download,
clip, reproject, find the right bands, apply a scale factor, build a cloud mask. Then they
do it again next week, because it lived in a notebook.

But the real problem is worse than tedium.

**Every satellite image carries a cloud-cover number (`eo:cloud_cover`) that describes a
110 km × 110 km scene. Analysts use it to decide whether their 5 km field is visible.**
It is the wrong measurement, and it fails in both directions — it hides usable images and
admits unusable ones, silently.

CHIP replaces the whole thing with one sentence in, analysis-ready data out, and — the
actual contribution — **a cloud number measured over the user's real area of interest**,
plus a QA report naming what would otherwise have been silently wrong.

**One-line pitch:** *not "search is slow" — "search is confidently incorrect."*

---

# 2. Verified facts (measured against real data on 2026-09-17 — do not change these numbers)

These are real, reproducible, and are the acceptance criteria for the build.

### Fact 1 — the catalog cloud number is wrong in both directions

| AOI | Item ID | `eo:cloud_cover` says | True over AOI | Consequence |
|---|---|---|---|---|
| Punjab | `S2A_43REQ_20240715_0_L2A` | **12.23%** | **82.65%** | Passes every `cloud_cover < 20` filter. You analyze cloud tops. |
| Willamette | `S2B_10TDQ_20240802_0_L2A` | **47.44%** | **1.89%** | Rejected by that filter. A perfect image thrown away. |

Not cherry-picked: across 4 AOIs × 12 cloudy scenes, the scene-level number was off by
more than 15 points in **over half** of them.

### Fact 2 — the reflectance offset silently moves NDVI by 40%

Sentinel-2 processing baseline ≥ 04.00 introduced `BOA_ADD_OFFSET = -1000`. Reflectance is
`(DN - 1000) / 10000`, **not** `DN / 10000`. Both demo scenes are baseline 05.10 / 05.11.

Mean NDVI over the clear Willamette AOI: **naive 0.2989 → correct 0.4974.**
A 0.20 absolute error; the naive number is 40% low, raises no warning, and looks reasonable.

### Fact 3 — cloud contamination flips the sign of the statistic

Punjab AOI mean NDVI (correctly offset): all pixels **−0.089**, clear pixels only **+0.034**.

---

# 3. Ground rules — these are what make 90 minutes possible

- **No database. No auth. No Docker. No frontend build step. No async workers. No new dependencies.**
- **No raster reprojection anywhere.** Transform the *AOI bounds* into the scene's CRS and
  read that window. Output stays in the scene's UTM CRS. Correct, and 100× less code.
- **No mosaicking.** One scene per answer. Pick the best one.
- **One data source:** Sentinel-2 L2A on Earth Search, `https://earth-search.aws.element84.com/v1`.
  No keys, no signing, no account.
- **AOIs come from a fixed list.** No map drawing, no geocoding.
- If a decision takes more than 60 seconds, take the dumber option and write `# TODO(polish)`.
- Implement the contracts in this document. Do not redesign them, do not add abstraction
  layers, do not add a config system. This is a demo, not a library.

---

# 4. Architecture

```
app/
  __init__.py   env vars + constants          (full contents in §5)
  models.py     dataclasses = the contract    (full contents in §6)
  search.py     STAC search + honest cloud scoring
  chip.py       windowed COG reads, NDVI, PNGs
  qa.py         the five QA checks
  parse.py      sentence -> QuerySpec
  pipeline.py   the one function tying it together
  cli.py        terminal entry point
  main.py       FastAPI, 3 endpoints
web/index.html  one page, no framework
tests/test_acceptance.py   golden numbers    (full contents in §13)
demo/           ALREADY EXISTS — do not regenerate
outputs/        generated at runtime, gitignored
```

Flow: `sentence → parse → QuerySpec → search → ranked candidates → chip → arrays → qa → ChipResult`

---

# 5. `app/__init__.py` — create this file exactly

```python
"""CHIP — Cloud-Honest Image Pipeline.

GDAL/AWS env must be set before rasterio performs any I/O, so it lives here.
"""
import os

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")

STAC_API = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"

# Sentinel-2 L2A: DN -> reflectance is (DN + BOA_ADD_OFFSET) / 10000 for
# processing baseline >= 04.00. Skipping the offset is the single most common
# silent error in Sentinel-2 NDVI. Verified impact: mean NDVI 0.299 -> 0.497.
BOA_ADD_OFFSET = -1000.0
REFLECTANCE_SCALE = 10000.0
BASELINE_WITH_OFFSET = "04.00"

# SCL scene classification classes counted as "cloud" for AOI cloud fraction.
# 3 = cloud shadow, 8 = cloud medium prob, 9 = cloud high prob, 10 = thin cirrus.
SCL_CLOUD_CLASSES = (3, 8, 9, 10)
SCL_NODATA = 0
```

Also create `.python-version` containing `3.12` (**not 3.14 — rasterio has no wheels for it**)
and `requirements.txt`:

```
rasterio>=1.4
numpy>=2.0
pillow>=10.0
requests>=2.32
fastapi>=0.115
uvicorn[standard]>=0.30
pytest>=8.0
```

and `.gitignore` containing `.venv/`, `outputs/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.DS_Store`.

---

# 6. `app/models.py` — create this file exactly

```python
"""The contract between modules. Keep these dataclasses boring and stable."""
from dataclasses import dataclass, field
from typing import Literal, Optional

Product = Literal["true_color", "ndvi", "ndwi"]
Status = Literal["pass", "warn", "fail"]


@dataclass
class QuerySpec:
    """The parsed intent of a sentence. Output of parse.py."""
    aoi_name: str
    bbox: list[float]                 # [w, s, e, n] in EPSG:4326
    start: str                        # ISO date, inclusive
    end: str                          # ISO date, inclusive
    product: Product = "true_color"
    max_aoi_cloud: float = 100.0      # the HONEST threshold, applied over the AOI
    source_text: str = ""
    parsed_by: Literal["rules", "llm"] = "rules"


@dataclass
class Candidate:
    """One scene considered for the answer. Both cloud numbers, always."""
    item_id: str
    datetime: str
    scene_cloud_pct: float            # what the catalog claims (eo:cloud_cover)
    aoi_cloud_pct: float              # what is actually true over the AOI
    valid_pct: float                  # AOI coverage; < 50 means swath edge
    selected: bool = False

    @property
    def disagreement(self) -> float:
        """Signed gap. Positive = catalog understates cloud over the AOI."""
        return self.aoi_cloud_pct - self.scene_cloud_pct


@dataclass
class Check:
    """One QA finding. Plain data — do not build a rules engine."""
    id: str
    status: Status
    message: str
    evidence: dict = field(default_factory=dict)


@dataclass
class ChipResult:
    spec: QuerySpec
    selected: Optional[Candidate]
    candidates: list[Candidate]
    checks: list[Check]
    crs: str
    shape: list[int]
    preview_png: str = ""
    product_png: str = ""
    cloudmask_png: str = ""
    geotiff: str = ""
    stats: dict = field(default_factory=dict)
```

---

# 7. PHASE 0 — Environment (5 min)

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run python -c "import rasterio, fastapi; print('ok')"
```

**Done when:** imports work.

---

# 8. PHASE 1 — The chip core (20 min) — *must not be cut*

## `app/search.py::search_items(bbox, start, end, limit=20) -> list[dict]`

POST to `{STAC_API}/search` with `collections=[COLLECTION]`, `bbox`,
`datetime=f"{start}T00:00:00Z/{end}T23:59:59Z"`, `limit`, sorted by datetime descending.
Return `response.json()["features"]`. Timeout 60s.

**Do NOT filter on `eo:cloud_cover`.** That filter is the bug being fixed. Keep the search
wide open and rank honestly in Phase 2.

## `app/chip.py::read_window(href, bbox_4326) -> (array, crs, nodata)`

This is the core trick. Use it verbatim:

```python
with rasterio.open(href) as src:
    b = transform_bounds("EPSG:4326", src.crs, *bbox_4326, densify_pts=21)
    win = from_bounds(*b, transform=src.transform)
    arr = src.read(1, window=win, boundless=True, fill_value=0)
return arr, str(src.crs), src.nodata
```

`densify_pts=21` matters — UTM edges curve, and a 4-corner transform clips the AOI.
Imports: `from rasterio.warp import transform_bounds`, `from rasterio.windows import from_bounds`.

## `app/chip.py::stretch(band, lo=2, hi=98) -> uint8 array`

Percentile stretch for display, **ignoring zeros** when computing percentiles:

```python
v = band[band > 0]
if v.size == 0:
    return np.zeros_like(band, dtype="uint8")
p1, p2 = np.percentile(v, [lo, hi])
return np.clip((band - p1) / max(p2 - p1, 1e-6) * 255, 0, 255).astype("uint8")
```

## `app/chip.py::true_color_png(item, bbox, out_path) -> str`

Read the `red`, `green`, `blue` assets, `stretch` each, `np.dstack`, save with PIL.

## `app/cli.py --smoke`

Runs the willamette AOI for August 2024 end to end and writes a true-colour PNG to `outputs/`.

**Done when:** `uv run python -m app.cli --smoke` writes a real PNG of farmland.
**If you demo only this, you still have "one line replaces the notebook."**

---

# 9. PHASE 2 — Honest clouds (15 min) — *the differentiator, never cut*

## `app/search.py::aoi_cloud_fraction(item, bbox) -> (cloud_pct, valid_pct)`

THE function. Everything else is plumbing around it.

```python
scl, _, _ = read_window(item["assets"]["scl"]["href"], bbox)
valid = scl != SCL_NODATA
cloud = np.isin(scl, SCL_CLOUD_CLASSES) & valid
cloud_pct = 100.0 * cloud.sum() / max(valid.sum(), 1)
valid_pct = 100.0 * valid.sum() / valid.size
```

## `app/search.py::rank_candidates(items, bbox, max_aoi_cloud=100.0, min_valid_pct=50.0) -> list[Candidate]`

- Probe **at most 8** items so this stays under a few seconds.
- Drop items with `valid_pct < min_valid_pct` (AOI straddles the swath edge).
- Sort by `aoi_cloud_pct` ascending; mark the winner `selected=True`.
- **Keep the losers.** The comparison table of scene% vs aoi% is the demo's best screenshot.
- **Always emit both numbers.** The gap between them is the entire pitch — never hide it.

**Done when:** Punjab on 2024-07-15 prints `scene=12.2 aoi=82.7`.

---

# 10. PHASE 3 — The QA report (15 min)

## `app/chip.py::to_reflectance(dn, apply_offset=True)`

`(dn + BOA_ADD_OFFSET) / REFLECTANCE_SCALE` when `apply_offset`, else `dn / REFLECTANCE_SCALE`.

## `app/chip.py::ndvi(nir_dn, red_dn, apply_offset=True)` — the guard is mandatory

```python
nir_r = to_reflectance(nir_dn, apply_offset)
red_r = to_reflectance(red_dn, apply_offset)
denom = nir_r + red_r
ok = np.abs(denom) > 1e-4      # applying the offset CAN drive this through zero
out = np.full_like(nir_r, np.nan)
out[ok] = np.clip((nir_r[ok] - red_r[ok]) / denom[ok], -1, 1)
return out
```

**Without the `ok` guard this returns ~2.6e5 on real data.** It happened during prep.

Mask clouds out of NDVI *statistics* using SCL, but still render the full chip so the
viewer can see what was excluded.

## `app/chip.py::upsample_mask(mask, shape)` — use verbatim

SCL is 20 m, bands are 10 m. `np.kron` gives an off-by-one (670 vs 671 in prep).

```python
im = Image.fromarray(mask.astype("uint8") * 255).resize((shape[1], shape[0]), Image.NEAREST)
return np.array(im) > 127
```

## `app/chip.py` — remaining renderers

- `ndvi_png(arr, out_path)` — brown→yellow→green ramp over −0.2…0.9, NaN renders grey.
  Ramp without matplotlib: `t = np.clip((ndvi + 0.2) / 1.1, 0, 1)`, then
  `r = np.interp(t, [0,.5,1], [140,240,30])`, `g = np.interp(t, [0,.5,1], [100,230,120])`,
  `b = np.interp(t, [0,.5,1], [60,140,40])`, `np.dstack([r,g,b]).astype("uint8")`.
- `cloudmask_png(rgb, cloud_mask, out_path)` — true colour with cloud pixels tinted
  `[255, 60, 60]`. Shows *what* was excluded and why.
- `write_geotiff(arr, src_profile, out_path)` — preserve CRS + transform from the window.

## `app/qa.py` — five checks, in value order

Each returns a `Check(id, status, message, evidence)`. `run_checks(...)` runs all five and
orders the result `fail → warn → pass`. Keep them plain functions.

1. **`reflectance_offset`** — *the money check.* If `s2:processing_baseline >= "04.00"`,
   compute mean NDVI both ways and report the delta as evidence. Status `warn` whenever the
   offset applies, so it always shows on stage. Message states CHIP applied it and what the
   value would otherwise have been.
2. **`aoi_cloud_vs_scene`** — warn when `abs(disagreement) > 15`, and **name the direction**:
   - positive → "catalog understates cloud over your AOI by N points; a `cloud_cover` filter would have let this through"
   - negative → "catalog overstates cloud by N points; a `cloud_cover` filter would have thrown this usable image away"
3. **`nodata_as_zero`** — `nodata == 0` while 0 is also a legal DN. Report % exact-zero
   pixels and warn that any mean-over-all-pixels is biased low.
4. **`crs_note`** — informational: state the output CRS and that **no resampling was performed**.
5. **`valid_pixels`** — `fail` under 50%; the AOI is off the swath edge and stats are meaningless.

**Done when:** Punjab reports 3 warnings; Willamette reports the offset delta 0.299 → 0.497.

---

# 11. PHASE 4 — Sentence → QuerySpec (15 min)

**Write the rules parser first and make the LLM optional.** A demo that dies because a
token expired is a lost hackathon.

## `app/parse.py::load_aois(path="demo/aois.geojson") -> dict[str, list[float]]`

Returns `name -> bbox`. Derive the bbox from each polygon's coordinates. Matching against
the sentence is case-insensitive substring.

## `app/parse.py::parse_rules(text) -> QuerySpec`

~60 lines of regex. Must cover exactly the six lines in §12:

- **AOI:** substring match on names from `aois.geojson`, or a literal `bbox=[w,s,e,n]`
- **Dates:** `"<month> <year>"` | `"last <month>"` | `"summer 2024"` (Jun 1–Aug 31) |
  `"on 15 July 2024"` (single day) | bare `"<year>"`. Default: the last 90 days.
- **Product:** `"ndvi"` | `"ndwi"` | else `"true_color"`
- **Clear intent:** any of `"only clear"`, `"clear day"`, `"cloud-free"`, `"cloud free"`,
  `"clear sky"`, `"no cloud"` → `max_aoi_cloud = 10`

Helper constant:
```python
MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july",
     "august","september","october","november","december"], start=1)}
```

## `app/parse.py::parse_llm(text, aois) -> QuerySpec | None`

Only attempted when `WATSONX_API_KEY` is set. Ask watsonx / Granite for strict JSON
matching `QuerySpec`, temperature 0. **Must return `None` on ANY exception or schema
mismatch — never raise into the request path.** Wrap the entire body in `try/except`.

## `app/parse.py::parse(text) -> QuerySpec`

LLM if available and valid, else rules. Always returns a usable spec.
**Always show the parsed spec in the UI before running** — good UX, and your safety net
when the parse is wrong on stage.

**Done when:** all six queries parse correctly with the LLM disabled.

---

# 12. Data files to create

## `demo/queries.txt` (already exists — these are the six the parser must handle)

```
NDVI over willamette, August 2024, only clear days
true color over punjab on 15 July 2024
NDVI over iowa, summer 2024
show me po_valley in June 2024
NDWI over willamette, 2024
true color over bbox=[-123.10,44.52,-123.02,44.58], August 2024
```

## `demo/aois.geojson` (already exists — four AOIs)

| name | bbox `[w, s, e, n]` | note |
|---|---|---|
| `willamette` | `[-123.10, 44.52, -123.02, 44.58]` | HERO: 2024-08-02 catalog 47.4%, AOI 1.9% |
| `punjab` | `[75.80, 30.88, 75.88, 30.94]` | HERO: 2024-07-15 catalog 12.2%, AOI 82.7% |
| `iowa` | `[-93.72, 41.98, -93.66, 42.03]` | clean fallback, reliably clear Aug–Sep |
| `po_valley` | `[9.10, 45.30, 9.18, 45.36]` | good for the date-range demo |

---

# 13. `tests/test_acceptance.py` — create this file exactly

Golden numbers verified against real data. If these pass, the demo works. Run after
Phase 2 and Phase 3. Tolerances are loose on purpose — we assert "the concept holds",
not bit-exactness.

```python
import pytest

from app.models import QuerySpec

WILLAMETTE = [-123.10, 44.52, -123.02, 44.58]
PUNJAB = [75.80, 30.88, 75.88, 30.94]


@pytest.mark.parametrize("item_id,bbox,scene,aoi", [
    ("S2A_43REQ_20240715_0_L2A", PUNJAB, 12.23, 82.65),      # false-clear
    ("S2B_10TDQ_20240802_0_L2A", WILLAMETTE, 47.44, 1.89),   # wrongly rejected
])
def test_aoi_cloud_disagrees_with_scene(item_id, bbox, scene, aoi):
    """PHASE 2 done-check. The gap between these two numbers is the whole product."""
    from app.search import aoi_cloud_fraction, search_items
    d = item_id.split("_")[2]
    date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    items = search_items(bbox, date, date, limit=20)
    item = next(i for i in items if i["id"] == item_id)
    assert item["properties"]["eo:cloud_cover"] == pytest.approx(scene, abs=0.5)
    cloud_pct, valid_pct = aoi_cloud_fraction(item, bbox)
    assert cloud_pct == pytest.approx(aoi, abs=3.0)
    assert valid_pct > 99


def test_reflectance_offset_changes_ndvi_materially():
    """PHASE 3 done-check. Naive 0.2989 vs correct 0.4974 on the clear Willamette scene."""
    import numpy as np
    from app.chip import ndvi, read_window
    from app.search import search_items

    items = search_items(WILLAMETTE, "2024-08-02", "2024-08-02", limit=20)
    item = next(i for i in items if i["id"] == "S2B_10TDQ_20240802_0_L2A")
    red, _, _ = read_window(item["assets"]["red"]["href"], WILLAMETTE)
    nir, _, _ = read_window(item["assets"]["nir"]["href"], WILLAMETTE)

    correct = np.nanmean(ndvi(nir.astype("float32"), red.astype("float32"), apply_offset=True))
    naive = np.nanmean(ndvi(nir.astype("float32"), red.astype("float32"), apply_offset=False))

    assert correct == pytest.approx(0.4974, abs=0.02)
    assert naive == pytest.approx(0.2989, abs=0.02)
    assert correct - naive > 0.15, "the offset must move NDVI materially -- that's the pitch"


def test_ndvi_is_guarded_against_zero_denominator():
    """Regression: unguarded, this returned 2.6e5 during prep."""
    import numpy as np
    from app.chip import ndvi
    nir = np.array([[1000.0, 1200.0]], dtype="float32")   # -> 0.0 reflectance after offset
    red = np.array([[1000.0, 800.0]], dtype="float32")
    out = ndvi(nir, red, apply_offset=True)
    assert np.all(np.isnan(out) | (np.abs(out) <= 1.0)), f"NDVI out of range: {out}"


@pytest.mark.parametrize("text,aoi,product,clear", [
    ("NDVI over willamette, August 2024, only clear days", "willamette", "ndvi", True),
    ("true color over punjab on 15 July 2024", "punjab", "true_color", False),
    ("NDVI over iowa, summer 2024", "iowa", "ndvi", False),
])
def test_rules_parser(text, aoi, product, clear):
    """PHASE 4 done-check. Must pass with the LLM disabled."""
    from app.parse import parse_rules
    spec = parse_rules(text)
    assert isinstance(spec, QuerySpec)
    assert spec.aoi_name == aoi
    assert spec.product == product
    assert (spec.max_aoi_cloud <= 10) == clear
```

---

# 14. PHASE 5 — API + page (15 min)

## `app/pipeline.py::run(spec, out_dir="outputs") -> ChipResult`

`search → rank honestly → read bands → build product → QA → ChipResult`.

Cache key: `f"{item_id}_{bbox_hash}_{product}"`. A re-run during the demo must be instant —
**you will run the same query twice on stage.**

If `CHIP_OFFLINE=1`, serve the two hero cases straight from `demo/cache/*_item.json` and
`demo/assets/*.png` with **no network calls at all**.

## `app/main.py` — FastAPI, three endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/aois` | `[{name, bbox, label}]` for the dropdown |
| `POST /api/parse` `{text}` | `QuerySpec`, so the UI can show it before committing |
| `POST /api/chip` `{spec}` | `ChipResult` |

Mount `outputs/` and `web/` with `StaticFiles`. Serve `web/index.html` at `/`.
No auth, no database, no background workers.

## `web/index.html` — one file, plain JS, no framework, no build step

Top to bottom:
1. Text box + **Run** + clickable example chips (the six queries from §12)
2. "Here's what I understood" — the parsed spec, shown before running
3. **THE COMPARISON TABLE** — every candidate, `scene %` next to `AOI %`, gap highlighted.
   This is the screenshot that goes in the deck. Give it the most visual weight.
4. Previews: true colour | product (NDVI) | cloud-mask overlay
5. QA checks table, colour-coded by status
6. Download GeoTIFF

Dark, monospace, one accent colour. It should look like a tool, not a landing page.
**Do not add a map** — AOIs come from the dropdown.

## `app/cli.py` — full terminal entry point

`uv run python -m app.cli "NDVI over willamette, August 2024"`

Print at minimum: the parsed spec, the candidate table (`id | scene% | aoi% | gap`), the QA
checks, and output paths. **That printout alone is a credible demo if the web page never happens.**

**Done when:** you can type a sentence and see a picture.

---

# 15. PHASE 6 — Demo hardening (5 min) — do not skip

1. **Warm the cache** — run both hero queries so the demo path is instant.
2. **Offline mode** — verify `CHIP_OFFLINE=1` works with wifi physically off.
   Conference wifi is the most reliable way to lose a hackathon.
3. Run the full test suite.
4. One full dry run out loud with `demo/script.md`.

---

# 16. Time budget

| Phase | Min | Cumulative | Cuttable? |
|---|---|---|---|
| 0 Env | 5 | 5 | no |
| 1 Chip core | 20 | 25 | **no** |
| 2 Honest clouds | 15 | 40 | **no — this is the product** |
| 3 QA report | 15 | 55 | keep checks 1 and 2 only, if short |
| 4 Parser | 15 | 70 | drop LLM, keep rules |
| 5 API + page | 15 | 85 | fall back to CLI + the prepared chips |
| 6 Hardening | 5 | 90 | no |

## If you fall behind, cut in this order

1. The LLM parse (rules only — nobody can tell from the stage)
2. NDWI, keep NDVI
3. The GeoTIFF download (show the PNG)
4. The candidate comparison *table* (keep the two numbers as text)
5. The web page entirely → demo the CLI; it's a developer tool anyway

**Never cut:** the AOI-vs-scene cloud comparison, or the reflectance-offset check.
Those two are the reason this project wins rather than places.

---

# 17. Traps, pre-solved

| Trap | Answer |
|---|---|
| rasterio won't install | Python **3.12**, not 3.14 |
| COG opens are slow | `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` |
| 403 from S3 | `AWS_NO_SIGN_REQUEST=YES` |
| SCL is 20 m, bands are 10 m | PIL NEAREST resize to the band's exact shape. `np.kron` gives off-by-one (670 vs 671 in prep). |
| NDVI explodes to 2.6e5 | The `abs(denom) > 1e-4` guard in §10 |
| AOI clipped at the edges | `densify_pts=21` in `transform_bounds` |
| AOI spans two UTM zones | Pick the single best item; don't mosaic |
| Asset names differ per catalog | Earth Search uses `red`/`nir`/`scl`; Planetary Computer uses `B04`/`B08`. We only use Earth Search. |
| Nothing returned | Widen the date range before debugging anything else |

---

# 18. Build order for Bob — run these one at a time

**Never paste two phases at once.** The whole plan depends on never debugging two layers
simultaneously. Run the check after each phase before moving on.

**Phase 1** — "Read §5, §6, §8 of BOB.md. Create `app/__init__.py`, `app/models.py`,
`requirements.txt`, `.python-version` and `.gitignore` exactly as written there. Then
implement `search_items`, `read_window`, `stretch`, `true_color_png`, and `app/cli.py --smoke`.
Use the code snippets verbatim, including `densify_pts=21`. Do not filter on `eo:cloud_cover`."
→ **Check:** `uv run python -m app.cli --smoke` writes a PNG of farmland.

**Phase 2** — "Implement §9: `aoi_cloud_fraction` and `rank_candidates` in `app/search.py`.
Then create `tests/test_acceptance.py` from §13 and run `pytest -k aoi_cloud -x`."
→ **Check:** Punjab reports scene 12.2 / AOI 82.7.

**Phase 3** — "Implement §10: `to_reflectance`, `ndvi`, `upsample_mask`, `ndvi_png`,
`cloudmask_png`, `write_geotiff`, then all five checks in `app/qa.py`. The
`abs(denom) > 1e-4` guard is mandatory. Run `pytest -k 'offset or guarded' -x`."
→ **Check:** correct NDVI ≈ 0.497, naive ≈ 0.299.

**Phase 4** — "Implement §11: `load_aois` and `parse_rules`. Rules only — leave `parse_llm`
returning None. Run `pytest -k rules_parser -x`."
→ **Check:** that test passes.

**Phase 5** — "Implement §14: `app/pipeline.py::run`, then `app/main.py`, then
`web/index.html`. Single file, plain JS, dark and monospace. The scene-vs-AOI comparison
table gets the most visual weight."
→ **Check:** `uv run uvicorn app.main:app` → type a query → see a picture.

**Phase 6** — "Implement §15: add `CHIP_OFFLINE=1` support to `app/pipeline.py::run`,
serving the hero cases from `demo/cache/` and `demo/assets/` with no network calls."
→ **Check:** wifi off, both hero queries still work.

## If Bob goes sideways

- **Invents new modules or a config layer** → "Revert. Implement only what BOB.md specifies. No new files."
- **Adds geopandas / xarray / odc-stac / stackstac** → "Remove it. `requirements.txt` is final. rasterio + numpy + PIL only."
- **Reprojects rasters** → "No raster reprojection. Transform the AOI bounds into the scene CRS and read that window, as §8 shows."
- **A phase check fails twice** → stop and read §17; it is probably already listed there.

---

# 19. What already exists in `demo/` — do not regenerate

| File | What it is |
|---|---|
| `assets/punjab_false_clear_rgb.png` | Real chip: solid cumulus. Catalog claimed 12.2%. |
| `assets/punjab_false_clear_cloudmask.png` | SCL cloud pixels tinted red — 82.7% of the frame |
| `assets/willamette_wrongly_rejected_rgb.png` | Real chip: flawless farmland. Catalog claimed 47.4%. |
| `assets/willamette_wrongly_rejected_ndvi_correct.png` | NDVI with the offset applied |
| `assets/willamette_wrongly_rejected_ndvi_no_offset.png` | NDVI without — visibly flatter |
| `cache/*_item.json` | The real STAC items behind those numbers (offline mode reads these) |
| `cache/evidence.json` | Every measured value in machine-readable form |
| `evidence.md` | Full methodology and provenance for §2 |
| `script.md` | The 3-minute pitch script, with Q&A prep and a pre-flight checklist |
| `the_old_way.py` | The "before" script — 80 lines, two silent bugs. Scroll it on stage. |
| `reproduce_scan.py` | Regenerates the cloud-disagreement scan across 4 AOIs |
| `reproduce_assets.py` | Regenerates the chips and `evidence.json` |
