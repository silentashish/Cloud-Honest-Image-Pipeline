# CHIP — Handoff Document

**From:** Bob (Build Agent, Session 1)  
**To:** Next agent (polish / demo run agent)  
**Status at handoff:** **ALL 7 TICKETS COMPLETE — fully working product**

---

## What was built

CHIP (Cloud-Honest Image Pipeline) is a complete, working application. Every module is implemented, all 7 acceptance tests pass, and the demo path is operational.

```
uv run python -m app.cli "NDVI over willamette, August 2024, only clear days"
uv run uvicorn app.main:app
```

---

## Repo state at handoff

```
app/__init__.py       ✓  env + constants (exact from BOB.md §5)
app/models.py         ✓  QuerySpec, Candidate, Check, ChipResult (exact from BOB.md §6)
app/search.py         ✓  search_items, aoi_cloud_fraction, rank_candidates
app/chip.py           ✓  read_window, stretch, true_color_png, to_reflectance, ndvi,
                          upsample_mask, ndvi_png, cloudmask_png, write_geotiff
app/qa.py             ✓  run_checks + 5 QA checks (fail→warn→pass order)
app/parse.py          ✓  load_aois, parse_rules, parse_llm, parse
app/pipeline.py       ✓  run() with in-process cache + CHIP_OFFLINE=1 mode
app/main.py           ✓  FastAPI: GET /api/aois, POST /api/parse, POST /api/chip
app/cli.py            ✓  --smoke and full query runner
web/index.html        ✓  dark, monospace, comparison table hero, 6 example chips
tests/test_acceptance.py  ✓  7 golden-number tests
demo/                 ✓  copied from problem-data/ (assets, cache, scripts)
requirements.txt      ✓  7 packages
.python-version       ✓  3.12
```

---

## Test results

```bash
uv run pytest tests/ -v
# 7 passed, 0 failed, 8 warnings (PendingDeprecationWarning from rasterio internals)
```

All 4 acceptance categories verified:
1. Punjab scene=12.23 aoi=82.65 (false-clear)
2. Willamette scene=47.44 aoi=1.89 (wrongly rejected)
3. NDVI correct=0.4974 naive=0.2989 delta=0.2073
4. Zero-denominator guard: all values NaN or ≤1.0
5-7. Rules parser: 3 query patterns

---

## How to demo

### CLI demo (works with no server)

```bash
# Hero case 1: Punjab — catalog says 12% cloud, reality is 83%
uv run python -m app.cli "true color over punjab on 15 July 2024"

# Hero case 2: Willamette — catalog says 47% cloud, reality is 2%
uv run python -m app.cli "NDVI over willamette, August 2024, only clear days"
```

### Web demo

```bash
uv run uvicorn app.main:app
# Open http://localhost:8000
# Click example chips or type a query
```

### Offline demo (conference wifi backup)

```bash
CHIP_OFFLINE=1 uv run python -m app.cli "true color over punjab on 15 July 2024"
CHIP_OFFLINE=1 uv run python -m app.cli "NDVI over willamette, August 2024, only clear days"
# → correct numbers from demo/cache/, zero network calls
```

---

## Key numbers for the pitch (verified, do not change)

| AOI | Item | Catalog says | True AOI | Gap |
|---|---|---|---|---|
| Punjab | S2A_43REQ_20240715_0_L2A | 12.23% | 82.65% | +70.4 pts |
| Willamette | S2B_10TDQ_20240802_0_L2A | 47.44% | 1.89% | −45.5 pts |

NDVI offset impact on Willamette:
- Naive `DN/10000` → mean NDVI 0.2989
- Correct `(DN-1000)/10000` → mean NDVI 0.4974 (40% higher)

---

## Known issues (minor, non-blocking)

1. **rasterio PendingDeprecationWarning** — `*` vs `@` operator in `rasterio.windows`. Internal to rasterio 1.5.1, not our code. Doesn't affect results.

2. **NDWI renderer uses NDVI colour ramp** — functional but could use a dedicated blue→teal ramp for better visual. Mark as `# TODO(polish)`.

3. **`parse_llm` not tested live** — requires real `WATSONX_API_KEY` + `WATSONX_PROJECT_ID` env vars. The stub is correct and fully wrapped in try/except. Rules parser is the primary path.

4. **Image URLs in web UI** — served from `/outputs/` mount. If the server restarts and `outputs/` is empty, images show broken. Warm the cache first.

---

## Remaining polish tasks (optional, only if time permits)

These are NOT required for the demo to work. They are quality-of-life improvements:

1. **Warm both hero caches before presenting:**
   ```bash
   uv run python -m app.cli "true color over punjab on 15 July 2024"
   uv run python -m app.cli "NDVI over willamette, August 2024, only clear days"
   ```

2. **Optional: NDWI colour ramp** — replace `ndvi_png` reuse in `pipeline.py` with a dedicated blue ramp for NDWI.

3. **Optional: watsonx LLM integration** — set `WATSONX_API_KEY` + `WATSONX_PROJECT_ID` env vars. The `parse_llm` function in `app/parse.py` is already wired.

4. **Optional: add `demo/script.md` dry run** — run through the 3-minute pitch in `demo/script.md` out loud.

---

## Architecture summary

```
sentence
  → app/parse.py::parse()          # rules or watsonx/Granite
  → QuerySpec
  → app/pipeline.py::run()
      → app/search.py::search_items()         # STAC POST, no cloud filter
      → app/search.py::rank_candidates()      # aoi_cloud_fraction per item (≤8)
      → app/chip.py::read_window()            # COG window, densify_pts=21
      → app/chip.py::{ndvi, ndvi_png, etc.}  # product rendering
      → app/qa.py::run_checks()              # 5 QA checks
  → ChipResult
  → CLI print / FastAPI /api/chip JSON / web/index.html
```

No database. No Docker. No frontend build step. No async workers. No raster reprojection.

---

## Critical traps (already handled — do not undo these)

| Trap | How it's handled |
|---|---|
| sortby in STAC POST | Removed — Earth Search doesn't support it |
| rasterio won't install | `.python-version` pins 3.12 |
| COG 403 from S3 | `AWS_NO_SIGN_REQUEST=YES` in `app/__init__.py` |
| SCL 20m vs bands 10m | PIL NEAREST resize in `upsample_mask`, not `np.kron` |
| NDVI denominator → 0 | `abs(denom) > 1e-4` guard in `ndvi()` |
| AOI clipped at edges | `densify_pts=21` in `read_window()` |
| NaN in ndvi_png | Replace with 0 before ramp, overwrite with grey after |
| evidence.json structure | Flat dict by hero key, not list of findings |
