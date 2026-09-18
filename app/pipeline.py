"""Pipeline: wire all phases into one run() call with caching and offline mode."""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import rasterio

from app.chip import (
    cloudmask_png,
    ndvi,
    ndvi_png,
    read_window,
    true_color_png,
    upsample_mask,
    write_geotiff,
)
from app.models import Candidate, ChipResult, QuerySpec
from app.qa import run_checks
from app.search import aoi_cloud_fraction, rank_candidates, search_items

# In-process cache: key -> ChipResult
_CACHE: dict[str, ChipResult] = {}

# Hero case identifiers for offline mode
_OFFLINE_HEROES = {
    "S2A_43REQ_20240715_0_L2A": "punjab_false_clear",
    "S2B_10TDQ_20240802_0_L2A": "willamette_wrongly_rejected",
}


def _bbox_hash(bbox: list[float]) -> str:
    return hashlib.md5(str(bbox).encode()).hexdigest()[:8]


def _load_offline_hero(hero_key: str, spec: QuerySpec, out_dir: str) -> ChipResult:
    """Serve a hero case from demo/cache and demo/assets with no network calls."""
    cache_path = Path("demo") / "cache" / f"{hero_key}_item.json"
    with open(cache_path) as f:
        item = json.load(f)

    assets_dir = Path("demo") / "assets"
    os.makedirs(out_dir, exist_ok=True)

    # Copy pre-generated PNGs to outputs/
    import shutil

    preview_src = assets_dir / f"{hero_key}_rgb.png"
    ndvi_src = assets_dir / f"{hero_key}_ndvi_correct.png"
    cloudmask_src = assets_dir / f"{hero_key}_cloudmask.png"

    item_id = item["id"]
    preview_dst = str(Path(out_dir) / f"{item_id}_preview.png")
    ndvi_dst = str(Path(out_dir) / f"{item_id}_ndvi.png")
    cloudmask_dst = str(Path(out_dir) / f"{item_id}_cloudmask.png")

    if preview_src.exists():
        shutil.copy(preview_src, preview_dst)
    if ndvi_src.exists():
        shutil.copy(ndvi_src, ndvi_dst)
    if cloudmask_src.exists():
        shutil.copy(cloudmask_src, cloudmask_dst)

    # Build candidate from item
    scene_cloud = item["properties"].get("eo:cloud_cover", 0.0)
    # Use known values from evidence.json
    evidence_path = Path("demo") / "cache" / "evidence.json"
    aoi_cloud = 0.0
    valid_pct = 100.0
    if evidence_path.exists():
        with open(evidence_path) as f:
            ev = json.load(f)
        entry = ev.get(hero_key, {})
        aoi_cloud = entry.get("aoi_cloud_pct", 0.0)
        valid_pct = 100.0  # hero cases have full coverage

    candidate = Candidate(
        item_id=item_id,
        datetime=item["properties"].get("datetime", ""),
        scene_cloud_pct=float(scene_cloud),
        aoi_cloud_pct=aoi_cloud,
        valid_pct=valid_pct,
        selected=True,
    )

    from app.models import Check
    checks = [
        Check(id="offline_mode", status="pass", message="Served from offline cache — no network calls."),
    ]

    return ChipResult(
        spec=spec,
        selected=candidate,
        candidates=[candidate],
        checks=checks,
        crs="EPSG:32643",
        shape=[0, 0],
        preview_png=preview_dst if Path(preview_dst).exists() else "",
        product_png=ndvi_dst if Path(ndvi_dst).exists() else "",
        cloudmask_png=cloudmask_dst if Path(cloudmask_dst).exists() else "",
    )


def run(spec: QuerySpec, out_dir: str = "outputs") -> ChipResult:
    """Full pipeline: search → rank → chip → QA → ChipResult.

    Results are cached in-process; re-running the same query is instant.
    CHIP_OFFLINE=1 bypasses all network calls for the hero demo cases.
    """
    os.makedirs(out_dir, exist_ok=True)

    # --- Cache ---
    cache_key = f"{spec.aoi_name}_{_bbox_hash(spec.bbox)}_{spec.product}_{spec.start}_{spec.end}_{spec.max_aoi_cloud}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    # --- Offline mode ---
    offline = os.environ.get("CHIP_OFFLINE", "").strip() == "1"
    if offline:
        # Match hero case by AOI name
        hero_map = {
            "punjab": "punjab_false_clear",
            "willamette": "willamette_wrongly_rejected",
        }
        hero_key = hero_map.get(spec.aoi_name)
        if hero_key:
            result = _load_offline_hero(hero_key, spec, out_dir)
            _CACHE[cache_key] = result
            return result

    # --- Search + rank ---
    items = search_items(spec.bbox, spec.start, spec.end)
    candidates = rank_candidates(items, spec.bbox, spec.max_aoi_cloud)

    if not candidates:
        from app.models import Check
        return ChipResult(
            spec=spec,
            selected=None,
            candidates=[],
            checks=[Check(
                id="no_data",
                status="fail",
                message="No valid scenes found for this AOI and date range.",
            )],
            crs="",
            shape=[0, 0],
        )

    winner = candidates[0]
    item = next(i for i in items if i["id"] == winner.item_id)

    # --- Read bands ---
    bbox = spec.bbox
    red_dn, crs, nodata = read_window(item["assets"]["red"]["href"], bbox)
    green_dn, _, _ = read_window(item["assets"]["green"]["href"], bbox)
    blue_dn, _, _ = read_window(item["assets"]["blue"]["href"], bbox)
    nir_dn, _, _ = read_window(item["assets"]["nir"]["href"], bbox)

    red_dn = red_dn.astype("float32")
    green_dn = green_dn.astype("float32")
    blue_dn = blue_dn.astype("float32")
    nir_dn = nir_dn.astype("float32")

    # --- Build product PNG ---
    item_id = item["id"]
    preview_path = str(Path(out_dir) / f"{item_id}_preview.png")
    product_path = ""
    cloudmask_path = ""
    geotiff_path = ""

    from app.chip import stretch
    from PIL import Image as _Image
    import numpy as _np

    rgb = _np.dstack([stretch(red_dn), stretch(green_dn), stretch(blue_dn)])
    _Image.fromarray(rgb).save(preview_path)

    # SCL for cloud mask
    scl_dn, _, _ = read_window(item["assets"]["scl"]["href"], bbox)
    from app import SCL_CLOUD_CLASSES, SCL_NODATA
    cloud_mask_20m = _np.isin(scl_dn, SCL_CLOUD_CLASSES) & (scl_dn != SCL_NODATA)
    cloud_mask = upsample_mask(cloud_mask_20m, red_dn.shape)

    if spec.product == "ndvi":
        ndvi_arr = ndvi(nir_dn, red_dn, apply_offset=True)
        # Mask clouds out of stats but render full chip
        clear_ndvi = ndvi_arr.copy()
        clear_ndvi[cloud_mask] = _np.nan
        stats = {
            "mean_ndvi_all": float(_np.nanmean(ndvi_arr)),
            "mean_ndvi_clear": float(_np.nanmean(clear_ndvi)),
            "cloud_pct": winner.aoi_cloud_pct,
        }
        product_path = str(Path(out_dir) / f"{item_id}_ndvi.png")
        ndvi_png(ndvi_arr, product_path)
        cloudmask_path = str(Path(out_dir) / f"{item_id}_cloudmask.png")
        cloudmask_png(rgb, cloud_mask, cloudmask_path)

        # GeoTIFF
        geotiff_path = str(Path(out_dir) / f"{item_id}_ndvi.tif")
        with rasterio.open(item["assets"]["red"]["href"]) as src:
            from rasterio.warp import transform_bounds
            from rasterio.windows import from_bounds as _fb
            b = transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
            win = _fb(*b, transform=src.transform)
            profile = src.profile.copy()
            profile.update(
                driver="GTiff",
                height=ndvi_arr.shape[0],
                width=ndvi_arr.shape[1],
                count=1,
                dtype="float32",
                transform=src.window_transform(win),
            )
        write_geotiff(ndvi_arr, profile, geotiff_path)

    elif spec.product == "ndwi":
        # NDWI = (green - nir) / (green + nir)
        from app.chip import to_reflectance
        g_r = to_reflectance(green_dn)
        n_r = to_reflectance(nir_dn)
        denom = g_r + n_r
        ok = _np.abs(denom) > 1e-4
        ndwi_arr = _np.full_like(g_r, _np.nan)
        ndwi_arr[ok] = _np.clip((g_r[ok] - n_r[ok]) / denom[ok], -1, 1)
        product_path = str(Path(out_dir) / f"{item_id}_ndwi.png")
        ndvi_png(ndwi_arr, product_path)  # reuse the ramp renderer
        cloudmask_path = str(Path(out_dir) / f"{item_id}_cloudmask.png")
        cloudmask_png(rgb, cloud_mask, cloudmask_path)
        stats = {"mean_ndwi": float(_np.nanmean(ndwi_arr))}
    else:
        # true_color: preview IS the product
        product_path = preview_path
        cloudmask_path = str(Path(out_dir) / f"{item_id}_cloudmask.png")
        cloudmask_png(rgb, cloud_mask, cloudmask_path)
        stats = {}

    # --- QA ---
    checks = run_checks(
        item=item,
        candidate=winner,
        nir_dn=nir_dn,
        red_dn=red_dn,
        arr=red_dn,
        nodata=nodata,
        crs=crs,
    )

    result = ChipResult(
        spec=spec,
        selected=winner,
        candidates=candidates,
        checks=checks,
        crs=crs,
        shape=list(red_dn.shape),
        preview_png=preview_path,
        product_png=product_path,
        cloudmask_png=cloudmask_path,
        geotiff=geotiff_path,
        stats=stats,
    )
    _CACHE[cache_key] = result
    return result
