# Evidence

Every number here came from real Sentinel-2 L2A COGs on AWS via Earth Search, computed
during preparation on 2026-09-17. Nothing is illustrative. Raw values in
`cache/evidence.json`, the STAC items that produced them in `cache/*_item.json`.

Method: read the `scl` (Scene Classification) asset over the AOI window only; cloud =
classes {3 shadow, 8 cloud medium, 9 cloud high, 10 cirrus}; valid = SCL != 0.

## Finding 1 — the catalog's cloud number is wrong in BOTH directions

| AOI | Item | `eo:cloud_cover` | True over AOI | Gap |
|---|---|---|---|---|
| Punjab | `S2A_43REQ_20240715_0_L2A` | **12.2%** | **82.7%** | +70.4 |
| Willamette | `S2B_10TDQ_20240802_0_L2A` | **47.4%** | **1.9%** | −45.5 |

**Punjab is the expensive one.** 12.2% passes every `cloud_cover < 20` filter ever
written. You download it, compute NDVI, and publish a number derived from the tops of
clouds. Look at `assets/punjab_false_clear_rgb.png` — it is solid cumulus.

**Willamette is the wasteful one.** 47.4% fails that same filter, so the pipeline skips
it and the analyst waits another week. `assets/willamette_wrongly_rejected_rgb.png` is a
flawless image of farmland.

Not cherry-picked: scanning 4 AOIs × 12 cloudy scenes, the scene-level number was off by
more than 15 points in **over half** of them (reproduce: `python demo/reproduce_scan.py`).

## Finding 2 — the reflectance offset silently moves NDVI by 40%

Sentinel-2 processing baseline ≥ 04.00 added `BOA_ADD_OFFSET = -1000`. Reflectance is
`(DN - 1000) / 10000`, not `DN / 10000`. Both demo scenes are baseline 05.10 / 05.11.

Mean NDVI over the clear Willamette AOI:

| | Mean NDVI |
|---|---|
| Naive `DN/10000` | **0.2989** |
| Correct, with offset | **0.4974** |

A 0.20 absolute error. The naive value is **40% low** — and it looks perfectly
reasonable. This is the "wrong but plausible" class of bug: no crash, no warning,
just a conclusion that is quietly false.

Third-order detail worth knowing: applying the offset can push `nir + red` through zero
in dark pixels. Unguarded, NDVI returned **2.6 × 10⁵** during prep. Hence the
`abs(denom) > 1e-4` guard in the plan.

## Finding 3 — cloud contamination poisons the statistic, not just the picture

Punjab AOI mean NDVI, correctly offset:

| | Mean NDVI |
|---|---|
| All pixels (82.7% cloud) | **−0.089** |
| Clear pixels only | **+0.034** |

Sign-flipped. Mid-July in Punjab is peak kharif season; both numbers are wrong for a
different reason, which is precisely why CHIP reports valid-pixel count next to any
statistic instead of quietly averaging.

## Assets

| File | Shows |
|---|---|
| `punjab_false_clear_rgb.png` | Solid cloud. Catalog: 12.2%. |
| `punjab_false_clear_cloudmask.png` | SCL cloud pixels tinted red |
| `willamette_wrongly_rejected_rgb.png` | Flawless farmland. Catalog: 47.4%. |
| `willamette_wrongly_rejected_ndvi_correct.png` | NDVI with offset |
| `willamette_wrongly_rejected_ndvi_no_offset.png` | NDVI without — visibly flatter |
