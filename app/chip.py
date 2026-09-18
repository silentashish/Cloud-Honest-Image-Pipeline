"""COG window reads, band processing, and PNG/GeoTIFF output."""
import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from app import BOA_ADD_OFFSET, REFLECTANCE_SCALE


def read_window(href: str, bbox_4326: list[float]):
    """Read a COG window for the given bbox without reprojecting the raster.

    Transforms the AOI bounds into the scene's CRS and reads that window.
    densify_pts=21 is mandatory — UTM edges curve and a 4-corner transform clips the AOI.
    Returns (array, crs_str, nodata).
    """
    with rasterio.open(href) as src:
        b = transform_bounds("EPSG:4326", src.crs, *bbox_4326, densify_pts=21)
        win = from_bounds(*b, transform=src.transform)
        arr = src.read(1, window=win, boundless=True, fill_value=0)
    return arr, str(src.crs), src.nodata


def stretch(band: np.ndarray, lo: float = 2, hi: float = 98) -> np.ndarray:
    """Percentile stretch to uint8, ignoring zeros (nodata/fill)."""
    v = band[band > 0]
    if v.size == 0:
        return np.zeros_like(band, dtype="uint8")
    p1, p2 = np.percentile(v, [lo, hi])
    return np.clip((band - p1) / max(p2 - p1, 1e-6) * 255, 0, 255).astype("uint8")


def true_color_png(item: dict, bbox: list[float], out_path: str) -> str:
    """Read red/green/blue assets, stretch, and write a true-colour PNG."""
    red, _, _ = read_window(item["assets"]["red"]["href"], bbox)
    green, _, _ = read_window(item["assets"]["green"]["href"], bbox)
    blue, _, _ = read_window(item["assets"]["blue"]["href"], bbox)
    rgb = np.dstack([stretch(red), stretch(green), stretch(blue)])
    Image.fromarray(rgb).save(out_path)
    return out_path


def to_reflectance(dn: np.ndarray, apply_offset: bool = True) -> np.ndarray:
    """Convert Sentinel-2 DN to surface reflectance.

    Processing baseline >= 04.00 requires BOA_ADD_OFFSET = -1000.
    Skipping this is the most common silent NDVI error; it shifts mean NDVI by ~0.20.
    """
    if apply_offset:
        return (dn + BOA_ADD_OFFSET) / REFLECTANCE_SCALE
    return dn / REFLECTANCE_SCALE


def ndvi(nir_dn: np.ndarray, red_dn: np.ndarray, apply_offset: bool = True) -> np.ndarray:
    """Compute NDVI with mandatory zero-denominator guard.

    Without the abs(denom) > 1e-4 guard, applying the offset can push
    nir + red through zero and return ~2.6e5. This happened during prep.
    """
    nir_r = to_reflectance(nir_dn, apply_offset)
    red_r = to_reflectance(red_dn, apply_offset)
    denom = nir_r + red_r
    ok = np.abs(denom) > 1e-4      # applying the offset CAN drive this through zero
    out = np.full_like(nir_r, np.nan)
    out[ok] = np.clip((nir_r[ok] - red_r[ok]) / denom[ok], -1, 1)
    return out


def upsample_mask(mask: np.ndarray, shape: tuple) -> np.ndarray:
    """Upsample a 20 m SCL mask to a 10 m band shape.

    np.kron gives an off-by-one (670 vs 671) on real data. Use PIL NEAREST resize.
    """
    im = Image.fromarray(mask.astype("uint8") * 255).resize(
        (shape[1], shape[0]), Image.NEAREST
    )
    return np.array(im) > 127


def ndvi_png(arr: np.ndarray, out_path: str) -> str:
    """Render NDVI array to a brown→yellow→green PNG. NaN pixels render grey."""
    nan_mask = np.isnan(arr)
    # Replace NaN with 0 for colour ramp computation; overwrite with grey after
    safe = np.where(nan_mask, 0.0, arr)
    t = np.clip((safe + 0.2) / 1.1, 0, 1)
    r = np.interp(t, [0, .5, 1], [140, 240, 30])
    g = np.interp(t, [0, .5, 1], [100, 230, 120])
    b = np.interp(t, [0, .5, 1], [60, 140, 40])
    rgb = np.dstack([r, g, b]).astype("uint8")
    # NaN → grey
    rgb[nan_mask] = [128, 128, 128]
    Image.fromarray(rgb).save(out_path)
    return out_path


def cloudmask_png(rgb: np.ndarray, cloud_mask: np.ndarray, out_path: str) -> str:
    """True-colour chip with cloud pixels tinted red [255, 60, 60]."""
    out = rgb.copy()
    out[cloud_mask] = [255, 60, 60]
    Image.fromarray(out).save(out_path)
    return out_path


def write_geotiff(arr: np.ndarray, src_profile: dict, out_path: str) -> str:
    """Write a single-band GeoTIFF preserving CRS and transform from the window."""
    profile = src_profile.copy()
    profile.update(
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype=arr.dtype,
    )
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(arr, 1)
    return out_path
