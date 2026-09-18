# Imagery not loading in the web UI

**Status:** diagnosed, reproduced, not fixed (no code changed).
**Symptom:** After running a query (e.g. `NDVI over willamette, August 2024, only clear days`),
the STATISTICS, QA REPORT and comparison table all render correctly, but all three cards under
IMAGERY show the browser's broken-image icon — True Colour, NDVI, and Cloud Mask Overlay.

---

## Root cause

The frontend builds the image `src` by splitting the backend path on the literal string `"/outputs/"`,
but the backend returns a **relative** path that starts with `outputs/` and has no leading slash.

**Frontend** — [`web/index.html:348`](web/index.html:348):
```js
const url = "/outputs/" + path.split("/outputs/")[1];
```

**Backend** — [`app/pipeline.py:168`](app/pipeline.py:168) and friends:
```python
preview_path = str(Path(out_dir) / f"{item_id}_preview.png")   # out_dir defaults to "outputs"
```

`Path("outputs") / "x.png"` stringifies to `outputs/x.png`. In JavaScript:

```js
"outputs/S2B_10TDQ_20240802_0_L2A_ndvi.png".split("/outputs/")
// → ["outputs/S2B_10TDQ_20240802_0_L2A_ndvi.png"]   (no match, length 1)
// [1] → undefined
```

So every card gets `src="/outputs/undefined"`, which the static mount at
[`app/main.py:22`](app/main.py:22) answers with **404**.

The same bug affects the GeoTIFF download link at [`web/index.html:370`](web/index.html:370)
(`result.geotiff.split("/outputs/")[1]`) — that link points at `/outputs/undefined` too whenever
a GeoTIFF is produced.

### Reproduction

```
$ CHIP_OFFLINE=1 python -m uvicorn app.main:app --port 8931
$ SPEC=$(curl -s -X POST localhost:8931/api/parse -H 'Content-Type: application/json' \
    -d '{"text":"NDVI over willamette, August 2024, only clear days"}')
$ curl -s -X POST localhost:8931/api/chip -H 'Content-Type: application/json' -d "{\"spec\":$SPEC}"
{"preview_png": "outputs/S2B_10TDQ_20240802_0_L2A_preview.png",
 "product_png": "outputs/S2B_10TDQ_20240802_0_L2A_ndvi.png",
 "cloudmask_png": "outputs/S2B_10TDQ_20240802_0_L2A_cloudmask.png",
 "geotiff": ""}

$ curl -o /dev/null -w "%{http_code}\n" localhost:8931/outputs/undefined
404
$ curl -o /dev/null -w "%{http_code}\n" localhost:8931/outputs/S2B_10TDQ_20240802_0_L2A_ndvi.png
200
```

The files exist and are served correctly — only the URL the page constructs is wrong.

### Note on "after using the filter"

The filter is **not** the trigger. The path shape returned by the pipeline is identical with and
without `only clear days` (verified for both `max_aoi_cloud=100.0` and `max_aoi_cloud=10.0`), so
the images are broken on *every* query. The clear-days query is just where it was noticed.

---

## Proposed fix

### Primary: make the frontend derive the URL from the basename

The path is always a filename inside `outputs/`, so take the last segment rather than splitting on
a separator that may or may not be there. This is tolerant of relative paths, absolute paths, and
Windows-style separators.

**`web/index.html:348`** — replace:
```js
const url = "/outputs/" + path.split("/outputs/")[1];
```
with:
```js
const url = "/outputs/" + path.split(/[\\/]/).pop();
```

**`web/index.html:370`** — same change for the GeoTIFF link:
```js
const url = "/outputs/" + result.geotiff.split(/[\\/]/).pop();
```

Since both sites do the same thing, prefer a single helper near `setStatus`:
```js
function outputUrl(path) {
  return "/outputs/" + path.split(/[\\/]/).pop();
}
```
and call `outputUrl(path)` / `outputUrl(result.geotiff)` at both sites.

### Recommended companion: have the API return the URL, not the filesystem path

The deeper issue is that `ChipResult` leaks a *filesystem* path into a JSON response and the
browser has to reverse-engineer a URL from it. That coupling is what broke. Two options:

- **Low-risk (do this now):** keep `ChipResult` as-is and do the basename conversion once in
  `app/main.py::api_chip`, rewriting `preview_png` / `product_png` / `cloudmask_png` / `geotiff`
  to `/outputs/<basename>` before returning. The frontend then uses the values verbatim and the
  split disappears entirely.
- **Cleaner (a v2 item):** add explicit `*_url` fields to `ChipResult` alongside the existing path
  fields, so the disk path stays available to the CLI and tests while the web UI consumes URLs.

### Guard against silent recurrence

Add a defensive check in the image loop so a bad path fails loudly instead of rendering a broken
icon — e.g. skip the card and log, or set `onerror` on the `<img>` to swap in a "render failed"
placeholder with the attempted URL. A regression test asserting that every non-empty image field
in the `/api/chip` response returns **200** from `/outputs/...` would have caught this.

---

## Unrelated issues noticed while tracing this (not causing the broken images)

1. **The cloud filter does nothing.** `rank_candidates` in [`app/search.py:37`](app/search.py:37)
   accepts `max_aoi_cloud` but never references it — candidates are only dropped on
   `min_valid_pct`. `only clear days` parses to `max_aoi_cloud=10.0` and then has no effect on
   which scenes are returned or selected.
2. **The pipeline cache key omits `max_aoi_cloud`.**
   [`app/pipeline.py:114`](app/pipeline.py:114) keys on AOI/bbox/product/start/end only, so once
   the filter in (1) is implemented, running the same query with and without `only clear days`
   would return the first result from cache.
3. **`stats` is assigned twice in the NDWI branch** ([`app/pipeline.py:228`](app/pipeline.py:228)
   then [`app/pipeline.py:233`](app/pipeline.py:233)), so `mean_ndwi_all` is discarded.
