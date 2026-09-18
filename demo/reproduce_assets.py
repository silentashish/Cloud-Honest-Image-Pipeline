import json, requests, numpy as np, rasterio, os
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from PIL import Image

os.environ["GDAL_DISABLE_READDIR_ON_OPEN"]="EMPTY_DIR"; os.environ["AWS_NO_SIGN_REQUEST"]="YES"
OUT=os.path.join(os.path.dirname(os.path.abspath(__file__)))
SEARCH="https://earth-search.aws.element84.com/v1/search"
CLOUD=[3,8,9,10]

CASES={
 "punjab_false_clear": {"id":"S2A_43REQ_20240715_0_L2A","bbox":[75.80,30.88,75.88,30.94],"date":"2024-07-15"},
 "willamette_wrongly_rejected": {"id":"S2B_10TDQ_20240802_0_L2A","bbox":[-123.10,44.52,-123.02,44.58],"date":"2024-08-02"},
}

def read(href,bbox):
    with rasterio.open(href) as s:
        b=transform_bounds("EPSG:4326",s.crs,*bbox,densify_pts=21)
        w=from_bounds(*b,transform=s.transform)
        return s.read(1,window=w,boundless=True,fill_value=0).astype("float32"), str(s.crs), s.nodata

def norm(a,lo=2,hi=98):
    v=a[a>0]
    if v.size==0: return np.zeros_like(a,dtype="uint8")
    p1,p2=np.percentile(v,[lo,hi])
    return np.clip((a-p1)/max(p2-p1,1e-6)*255,0,255).astype("uint8")

def up(mask, shape):
    im=Image.fromarray(mask.astype("uint8")*255).resize((shape[1],shape[0]), Image.NEAREST)
    return np.array(im)>127

def ramp(ndvi):
    # simple brown->yellow->green ramp over -0.2..0.9
    t=np.clip((ndvi+0.2)/1.1,0,1)
    r=np.interp(t,[0,.5,1],[140,240,30]); g=np.interp(t,[0,.5,1],[100,230,120]); b=np.interp(t,[0,.5,1],[60,140,40])
    return np.dstack([r,g,b]).astype("uint8")

summary={}
for name,c in CASES.items():
    it=requests.post(SEARCH,json={"collections":["sentinel-2-l2a"],"bbox":c["bbox"],
        "datetime":f'{c["date"]}T00:00:00Z/{c["date"]}T23:59:59Z',"limit":20},timeout=60).json()
    f=[x for x in it["features"] if x["id"]==c["id"]][0]
    json.dump(f,open(f"{OUT}/cache/{name}_item.json","w"),indent=2)
    a=f["assets"]; P=f["properties"]
    scl,crs,nd=read(a["scl"]["href"],c["bbox"])
    valid=scl!=0
    cl=np.isin(scl,CLOUD)&valid
    aoi_cloud=100*cl.sum()/max(valid.sum(),1)
    red,_,_=read(a["red"]["href"],c["bbox"]); nir,_,_=read(a["nir"]["href"],c["bbox"])
    grn,_,_=read(a["green"]["href"],c["bbox"]); blu,_,_=read(a["blue"]["href"],c["bbox"])
    Image.fromarray(np.dstack([norm(red),norm(grn),norm(blu)])).save(f"{OUT}/assets/{name}_rgb.png")
    # cloud mask overlay (red where cloud)
    ov=np.dstack([norm(red),norm(grn),norm(blu)]).copy()
    clu=up(cl, ov.shape[:2])
    ov[clu]=[255,60,60]
    Image.fromarray(ov).save(f"{OUT}/assets/{name}_cloudmask.png")
    # NDVI right vs wrong (BOA offset -1000 for baseline >= 04.00)
    off=-1000.0
    def ndvi_of(n,r):
        d=n+r
        out=np.zeros_like(n); ok=np.abs(d)>1e-4
        out[ok]=np.clip((n[ok]-r[ok])/d[ok],-1,1)
        return out, ok
    nirR=(nir+off)/10000.0; redR=(red+off)/10000.0
    ndvi_right,ok_r=ndvi_of(nirR,redR)
    ndvi_wrong,ok_w=ndvi_of(nir/10000.0,red/10000.0)
    Image.fromarray(ramp(ndvi_right)).save(f"{OUT}/assets/{name}_ndvi_correct.png")
    Image.fromarray(ramp(ndvi_wrong)).save(f"{OUT}/assets/{name}_ndvi_no_offset.png")
    mm=up(valid, ndvi_right.shape)
    summary[name]={"item_id":f["id"],"datetime":P["datetime"],"crs":crs,
        "scene_cloud_pct":round(P["eo:cloud_cover"],2),"aoi_cloud_pct":round(float(aoi_cloud),2),
        "baseline":P.get("s2:processing_baseline"),"nodata":nd,"shape":list(red.shape),
        "ndvi_mean_correct":round(float(np.nanmean(ndvi_right[mm&ok_r])),4),
        "ndvi_mean_no_offset":round(float(np.nanmean(ndvi_wrong[mm&ok_w])),4),
        "ndvi_mean_correct_clearonly":round(float(np.nanmean(ndvi_right[mm&ok_r&~up(cl,ndvi_right.shape)])),4) if (mm&ok_r&~up(cl,ndvi_right.shape)).sum()>100 else None,
        "negative_reflectance_pct":round(100*float((nirR<0).sum())/nirR.size,2),
        "zero_pixels_pct":round(100*float((red==0).sum())/red.size,2)}
    print(name,json.dumps(summary[name],indent=2))
json.dump(summary,open(f"{OUT}/cache/evidence.json","w"),indent=2)
