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
