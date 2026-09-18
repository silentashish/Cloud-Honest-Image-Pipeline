"""STAC search and cloud scoring."""
import numpy as np
import requests

from app import COLLECTION, SCL_CLOUD_CLASSES, SCL_NODATA, STAC_API
from app.chip import read_window
from app.models import Candidate


def search_items(bbox: list[float], start: str, end: str, limit: int = 20) -> list[dict]:
    """POST to STAC API. No cloud_cover filter — that is the bug being fixed."""
    payload = {
        "collections": [COLLECTION],
        "bbox": bbox,
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "limit": limit,
    }
    resp = requests.post(f"{STAC_API}/search", json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["features"]


def aoi_cloud_fraction(item: dict, bbox: list[float]) -> tuple[float, float]:
    """Measure actual cloud fraction over the AOI using SCL band.

    Returns (cloud_pct, valid_pct). This is the core differentiator —
    it uses the real pixel data, not the scene-level eo:cloud_cover.
    """
    scl, _, _ = read_window(item["assets"]["scl"]["href"], bbox)
    valid = scl != SCL_NODATA
    cloud = np.isin(scl, SCL_CLOUD_CLASSES) & valid
    cloud_pct = 100.0 * cloud.sum() / max(valid.sum(), 1)
    valid_pct = 100.0 * valid.sum() / valid.size
    return float(cloud_pct), float(valid_pct)


def rank_candidates(
    items: list[dict],
    bbox: list[float],
    max_aoi_cloud: float = 100.0,
    min_valid_pct: float = 50.0,
) -> list[Candidate]:
    """Score up to 8 items honestly and return all candidates, winner first.

    Never hides the scene vs AOI gap — the comparison is the demo.
    """
    candidates = []
    for item in items[:8]:
        cloud_pct, valid_pct = aoi_cloud_fraction(item, bbox)
        scene_cloud = item["properties"].get("eo:cloud_cover", 0.0)
        c = Candidate(
            item_id=item["id"],
            datetime=item["properties"].get("datetime", ""),
            scene_cloud_pct=float(scene_cloud),
            aoi_cloud_pct=cloud_pct,
            valid_pct=valid_pct,
        )
        # Drop AOIs off the swath edge — stats are meaningless
        if valid_pct >= min_valid_pct:
            candidates.append(c)

    # Sort by honest AOI cloud, best first
    candidates.sort(key=lambda c: c.aoi_cloud_pct)

    # Mark winner
    if candidates:
        candidates[0].selected = True

    return candidates
