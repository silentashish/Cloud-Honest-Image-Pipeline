"""FastAPI app — three endpoints, static files, no auth, no database."""
import dataclasses
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.models import QuerySpec
from app.parse import load_aois, parse

app = FastAPI(title="CHIP — Cloud-Honest Image Pipeline")

# Ensure static dirs exist
os.makedirs("outputs", exist_ok=True)
os.makedirs("web", exist_ok=True)

# Mount static dirs
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")


class TextRequest(BaseModel):
    text: str


class QuerySpecRequest(BaseModel):
    aoi_name: str
    bbox: list[float]
    start: str
    end: str
    product: str = "true_color"
    max_aoi_cloud: float = 100.0
    source_text: str = ""
    parsed_by: str = "rules"


class SpecRequest(BaseModel):
    spec: QuerySpecRequest


def _dc_to_dict(obj):
    """Recursively convert dataclasses to dicts for JSON serialization."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _dc_to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_dc_to_dict(i) for i in obj]
    return obj


@app.get("/api/aois")
def get_aois():
    """Return AOI list with names and bboxes for the dropdown."""
    try:
        aois = load_aois()
        with open("demo/aois.geojson") as f:
            gj = json.load(f)
        labels = {f["properties"]["name"]: f["properties"].get("label", f["properties"]["name"])
                  for f in gj["features"]}
        return [
            {"name": name, "bbox": bbox, "label": labels.get(name, name)}
            for name, bbox in aois.items()
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/parse")
def api_parse(req: TextRequest):
    """Parse a sentence and return the QuerySpec so the UI can show it before running."""
    try:
        spec = parse(req.text)
        return _dc_to_dict(spec)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/chip")
def api_chip(req: SpecRequest):
    """Run the full pipeline and return ChipResult."""
    from app.pipeline import run

    try:
        s = req.spec
        spec = QuerySpec(
            aoi_name=s.aoi_name,
            bbox=list(s.bbox),
            start=s.start,
            end=s.end,
            product=s.product,
            max_aoi_cloud=s.max_aoi_cloud,
            source_text=s.source_text,
            parsed_by=s.parsed_by,
        )
        result = run(spec)
        return _dc_to_dict(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def index():
    """Serve the single-page web UI."""
    html_path = Path("web/index.html")
    if html_path.exists():
        return FileResponse(str(html_path))
    return HTMLResponse("<h1>CHIP</h1><p>web/index.html not found</p>")
