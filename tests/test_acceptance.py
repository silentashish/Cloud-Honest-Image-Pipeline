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
