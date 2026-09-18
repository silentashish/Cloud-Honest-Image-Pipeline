import json, requests, numpy as np, rasterio, os
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

os.environ["GDAL_DISABLE_READDIR_ON_OPEN"]="EMPTY_DIR"
os.environ["AWS_NO_SIGN_REQUEST"]="YES"
SEARCH="https://earth-search.aws.element84.com/v1/search"
CLOUD={3,8,9,10}

AOIS={
 "iowa_farm":[-93.72,41.98,-93.66,42.03],
 "willamette":[-123.10,44.52,-123.02,44.58],
 "po_valley":[9.10,45.30,9.18,45.36],
 "punjab":[75.80,30.88,75.88,30.94],
}

def aoi_cloud(item, bbox):
    href=item["assets"]["scl"]["href"]
    with rasterio.open(href) as src:
        b=transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
        w=from_bounds(*b, transform=src.transform)
        arr=src.read(1, window=w, boundless=True, fill_value=0)
    if arr.size==0: return None,0
    valid=arr!=0
    if valid.sum()==0: return None,0
    cl=np.isin(arr, list(CLOUD)) & valid
    return 100.0*cl.sum()/valid.sum(), arr.size

for name,bbox in AOIS.items():
    r=requests.post(SEARCH,json={"collections":["sentinel-2-l2a"],"bbox":bbox,
        "datetime":"2024-04-01T00:00:00Z/2024-09-30T23:59:59Z",
        "query":{"eo:cloud_cover":{"gte":35}},"limit":12},timeout=60).json()
    print(f"\n=== {name} ({len(r.get('features',[]))} cloudy scenes) ===")
    for f in r.get("features",[]):
        try:
            ac,n=aoi_cloud(f,bbox)
        except Exception as e:
            print("  err",f["id"],str(e)[:60]); continue
        if ac is None: continue
        sc=f["properties"]["eo:cloud_cover"]
        flag="  <<< LIAR" if sc>=35 and ac<10 else ""
        print(f"  {f['id']}  scene={sc:5.1f}%  aoi={ac:5.1f}%  px={n}{flag}")
