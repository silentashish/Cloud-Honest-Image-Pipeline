"""THE VILLAIN. Scroll this on stage, slowly, then show one sentence.

This is not a strawman. It is the honest minimum to get ONE cloud-masked NDVI chip
out of Sentinel-2, and it still contains two silent bugs (marked BUG:) that produce
a number that is wrong but entirely plausible.

Do not run this. It exists to be scrolled.
"""
import json
import requests
import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

BBOX = [-123.10, 44.52, -123.02, 44.58]
START, END = "2024-08-01", "2024-08-31"
MAX_CLOUD = 20

# ---- 1. find something ------------------------------------------------------
r = requests.post(
    "https://earth-search.aws.element84.com/v1/search",
    json={
        "collections": ["sentinel-2-l2a"],
        "bbox": BBOX,
        "datetime": f"{START}T00:00:00Z/{END}T23:59:59Z",
        # BUG: eo:cloud_cover describes a 110x110 km scene, not this 7 km AOI.
        # This line silently discards usable images AND admits unusable ones.
        "query": {"eo:cloud_cover": {"lt": MAX_CLOUD}},
        "limit": 10,
    },
    timeout=60,
)
items = r.json()["features"]
if not items:
    raise SystemExit("nothing found -- widen the date range? lower the cloud filter? who knows")
item = sorted(items, key=lambda f: f["properties"]["eo:cloud_cover"])[0]

# ---- 2. which asset is which? ----------------------------------------------
# Earth Search calls them "red"/"nir". Planetary Computer calls them "B04"/"B08".
# Other catalogues use "band4". You will get this wrong at least once.
assets = item["assets"]
red_href = assets["red"]["href"]
nir_href = assets["nir"]["href"]
scl_href = assets["scl"]["href"]

# ---- 3. read windows, praying the CRS lines up -----------------------------
def read(href, bbox):
    with rasterio.open(href) as src:
        # densify_pts matters here and nobody tells you that either
        b = transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
        win = from_bounds(*b, transform=src.transform)
        arr = src.read(1, window=win, boundless=True, fill_value=0)
        return arr.astype("float32"), src.crs, src.nodata

red, crs, nodata = read(red_href, BBOX)
nir, _, _ = read(nir_href, BBOX)
scl, _, _ = read(scl_href, BBOX)

# ---- 4. SCL is 20 m, bands are 10 m ----------------------------------------
# np.kron is the obvious move and it gives you a 670x671 shape mismatch.
cloud20 = np.isin(scl, [3, 8, 9, 10])
cloud = np.kron(cloud20, np.ones((2, 2), bool))[: red.shape[0], : red.shape[1]]
if cloud.shape != red.shape:
    cloud = np.resize(cloud, red.shape)  # wrong, but it runs, which is worse

# ---- 5. scale factor ---------------------------------------------------------
# BUG: baseline >= 04.00 requires BOA_ADD_OFFSET = -1000 before dividing.
# This is documented in a PDF. Omitting it moves mean NDVI from 0.497 to 0.299
# and raises no error of any kind.
red_r = red / 10000.0
nir_r = nir / 10000.0

# ---- 6. finally --------------------------------------------------------------
ndvi = (nir_r - red_r) / (nir_r + red_r)   # and division by ~zero, somewhere in here
ndvi[cloud] = np.nan
# nodata is 0, which is also a legal DN, so this mean is biased low by an unknown amount
print("mean NDVI:", np.nanmean(ndvi))

# ---- 7. and next week you write it again ------------------------------------
