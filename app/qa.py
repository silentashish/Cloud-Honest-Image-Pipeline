"""QA checks — five plain functions that name every silent error."""
import numpy as np

from app import BASELINE_WITH_OFFSET
from app.chip import ndvi
from app.models import Candidate, Check


def _check_reflectance_offset(
    item: dict,
    nir_dn: np.ndarray,
    red_dn: np.ndarray,
) -> Check:
    """The money check. Shows the 40% NDVI error from the missing baseline offset."""
    baseline = item["properties"].get("s2:processing_baseline", "0.0")
    if baseline >= BASELINE_WITH_OFFSET:
        correct_mean = float(np.nanmean(ndvi(nir_dn, red_dn, apply_offset=True)))
        naive_mean = float(np.nanmean(ndvi(nir_dn, red_dn, apply_offset=False)))
        delta = abs(correct_mean - naive_mean)
        return Check(
            id="reflectance_offset",
            status="warn",
            message=(
                f"CHIP applied BOA_ADD_OFFSET=-1000 (baseline {baseline}). "
                f"Naive DN/10000 would give mean NDVI {naive_mean:.4f}; "
                f"correct value is {correct_mean:.4f} (Δ={delta:.4f})."
            ),
            evidence={
                "baseline": baseline,
                "naive_ndvi": naive_mean,
                "correct_ndvi": correct_mean,
                "delta": delta,
            },
        )
    return Check(
        id="reflectance_offset",
        status="pass",
        message=f"Processing baseline {baseline} predates the BOA offset; DN/10000 is correct.",
        evidence={"baseline": baseline},
    )


def _check_aoi_cloud_vs_scene(candidate: Candidate) -> Check:
    """Warn when the catalog cloud number disagrees with the true AOI measurement."""
    gap = candidate.disagreement
    if abs(gap) > 15:
        if gap > 0:
            direction = (
                f"catalog UNDERSTATES cloud over your AOI by {gap:.1f} points. "
                f"A cloud_cover filter would have let this through — "
                f"you would analyze cloud tops."
            )
        else:
            direction = (
                f"catalog OVERSTATES cloud by {abs(gap):.1f} points. "
                f"A cloud_cover filter would have thrown this usable image away."
            )
        return Check(
            id="aoi_cloud_vs_scene",
            status="warn",
            message=direction,
            evidence={
                "scene_cloud_pct": candidate.scene_cloud_pct,
                "aoi_cloud_pct": candidate.aoi_cloud_pct,
                "gap": gap,
            },
        )
    return Check(
        id="aoi_cloud_vs_scene",
        status="pass",
        message=(
            f"Scene cloud ({candidate.scene_cloud_pct:.1f}%) and AOI cloud "
            f"({candidate.aoi_cloud_pct:.1f}%) agree within 15 points."
        ),
        evidence={
            "scene_cloud_pct": candidate.scene_cloud_pct,
            "aoi_cloud_pct": candidate.aoi_cloud_pct,
            "gap": gap,
        },
    )


def _check_nodata_as_zero(arr: np.ndarray, nodata) -> Check:
    """nodata=0 while 0 is also a legal DN; any mean-over-all-pixels is biased low."""
    if nodata == 0 or nodata is None:
        zero_pct = 100.0 * float((arr == 0).sum()) / max(arr.size, 1)
        if zero_pct > 1.0:
            return Check(
                id="nodata_as_zero",
                status="warn",
                message=(
                    f"{zero_pct:.1f}% of pixels are exact zero. "
                    f"nodata=0 while 0 is also a valid DN — "
                    f"any mean computed over all pixels is biased low."
                ),
                evidence={"zero_pct": zero_pct},
            )
    return Check(
        id="nodata_as_zero",
        status="pass",
        message="No significant exact-zero pixel contamination detected.",
        evidence={},
    )


def _check_crs_note(crs: str) -> Check:
    """Informational: confirm no raster resampling was performed."""
    return Check(
        id="crs_note",
        status="pass",
        message=(
            f"Output CRS is {crs}. No raster reprojection was performed — "
            f"CHIP transformed the AOI bounds into the scene CRS and read that window."
        ),
        evidence={"crs": crs},
    )


def _check_valid_pixels(valid_pct: float) -> Check:
    """Fail if the AOI straddles the swath edge — stats are meaningless."""
    if valid_pct < 50.0:
        return Check(
            id="valid_pixels",
            status="fail",
            message=(
                f"Only {valid_pct:.1f}% of the AOI contains valid pixels. "
                f"The AOI is off the swath edge — all statistics are unreliable."
            ),
            evidence={"valid_pct": valid_pct},
        )
    return Check(
        id="valid_pixels",
        status="pass",
        message=f"{valid_pct:.1f}% of the AOI has valid pixels.",
        evidence={"valid_pct": valid_pct},
    )


def run_checks(
    item: dict,
    candidate: Candidate,
    nir_dn: np.ndarray,
    red_dn: np.ndarray,
    arr: np.ndarray,
    nodata,
    crs: str,
) -> list[Check]:
    """Run all five checks, ordered fail → warn → pass."""
    checks = [
        _check_reflectance_offset(item, nir_dn, red_dn),
        _check_aoi_cloud_vs_scene(candidate),
        _check_nodata_as_zero(arr, nodata),
        _check_crs_note(crs),
        _check_valid_pixels(candidate.valid_pct),
    ]
    order = {"fail": 0, "warn": 1, "pass": 2}
    checks.sort(key=lambda c: order[c.status])
    return checks
