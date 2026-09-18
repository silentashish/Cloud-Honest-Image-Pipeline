"""Natural-language sentence → QuerySpec.

Rules parser handles all six demo queries without any API key.
LLM is an optional enhancement; it must never crash the request path.
"""
import calendar
import json
import os
import re
from datetime import date, timedelta

from app.models import QuerySpec

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}

# Also accept 3-letter abbreviations
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})


def load_aois(path: str = "demo/aois.geojson") -> dict[str, list[float]]:
    """Return {name: [w, s, e, n]} from the GeoJSON file."""
    with open(path) as f:
        gj = json.load(f)
    result = {}
    for feature in gj["features"]:
        name = feature["properties"]["name"].lower()
        coords = feature["geometry"]["coordinates"][0]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        result[name] = [min(xs), min(ys), max(xs), max(ys)]
    return result


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _parse_dates(text: str):
    """Extract (start, end) ISO date strings from text. Returns (None, None) on no match."""
    tl = text.lower()
    today = date.today()

    # "on 15 July 2024" / "on July 15 2024"
    m = re.search(
        r'\bon\s+(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})\b',
        tl,
    )
    if m:
        day, mon_str, year = int(m.group(1)), m.group(2), int(m.group(3))
        month = MONTHS[mon_str]
        d = f"{year}-{month:02d}-{day:02d}"
        return d, d

    # "on July 15 2024"
    m = re.search(
        r'\bon\s+(' + '|'.join(MONTHS) + r')\s+(\d{1,2})\s+(\d{4})\b',
        tl,
    )
    if m:
        mon_str, day, year = m.group(1), int(m.group(2)), int(m.group(3))
        month = MONTHS[mon_str]
        d = f"{year}-{month:02d}-{day:02d}"
        return d, d

    # "summer 2024"
    m = re.search(r'\bsummer\s+(\d{4})\b', tl)
    if m:
        year = int(m.group(1))
        return f"{year}-06-01", f"{year}-08-31"

    # "<month> <year>"
    m = re.search(r'\b(' + '|'.join(MONTHS) + r')\s+(\d{4})\b', tl)
    if m:
        mon_str, year = m.group(1), int(m.group(2))
        month = MONTHS[mon_str]
        last = _last_day(year, month)
        return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last:02d}"

    # "last <month>"
    m = re.search(r'\blast\s+(' + '|'.join(MONTHS) + r')\b', tl)
    if m:
        mon_str = m.group(1)
        month = MONTHS[mon_str]
        year = today.year if today.month > month else today.year - 1
        last = _last_day(year, month)
        return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last:02d}"

    # bare "<year>"
    m = re.search(r'\b(20\d{2})\b', tl)
    if m:
        year = int(m.group(1))
        return f"{year}-01-01", f"{year}-12-31"

    # default: last 90 days
    start = today - timedelta(days=90)
    return start.isoformat(), today.isoformat()


def parse_rules(text: str) -> QuerySpec:
    """~60-line regex parser. Handles all six demo queries without an API key."""
    aois = load_aois()
    tl = text.lower()

    # --- AOI ---
    aoi_name = None
    bbox = None

    # Literal bbox=[w,s,e,n]
    m = re.search(r'bbox=\[([^\]]+)\]', text)
    if m:
        parts = [float(x.strip()) for x in m.group(1).split(",")]
        bbox = parts
        aoi_name = "custom"
    else:
        for name, b in aois.items():
            if name in tl:
                aoi_name = name
                bbox = b
                break

    if aoi_name is None:
        aoi_name = "__unknown__"
        bbox = [-180, -90, 180, 90]

    # --- Dates ---
    start, end = _parse_dates(text)

    # --- Product ---
    if "ndwi" in tl:
        product = "ndwi"
    elif "ndvi" in tl:
        product = "ndvi"
    else:
        product = "true_color"

    # --- Clear intent ---
    clear_patterns = [
        "only clear", "clear day", "cloud-free", "cloud free",
        "clear sky", "no cloud",
    ]
    max_aoi_cloud = 10.0 if any(p in tl for p in clear_patterns) else 100.0

    return QuerySpec(
        aoi_name=aoi_name,
        bbox=bbox,
        start=start,
        end=end,
        product=product,
        max_aoi_cloud=max_aoi_cloud,
        source_text=text,
        parsed_by="rules",
    )


def parse_llm(text: str, aois: dict) -> "QuerySpec | None":
    """Optional LLM parse via watsonx/Granite. Returns None on any failure."""
    try:
        api_key = os.environ.get("WATSONX_API_KEY")
        if not api_key:
            return None

        project_id = os.environ.get("WATSONX_PROJECT_ID", "")
        url = os.environ.get(
            "WATSONX_URL", "https://us-south.ml.cloud.ibm.com"
        )

        import requests

        # Get IAM token
        iam_resp = requests.post(
            "https://iam.cloud.ibm.com/identity/token",
            data={"apikey": api_key, "grant_type": "urn:ibm:params:oauth:grant-type:apikey"},
            timeout=15,
        )
        iam_resp.raise_for_status()
        token = iam_resp.json()["access_token"]

        aoi_names = list(aois.keys())
        prompt = (
            f"Extract the search parameters from this remote-sensing query and return "
            f"strict JSON with these exact keys: aoi_name (one of {aoi_names} or 'custom'), "
            f"bbox ([w,s,e,n] as floats), start (ISO date), end (ISO date), "
            f"product (true_color|ndvi|ndwi), max_aoi_cloud (float 0-100).\n"
            f"Query: {text}\nJSON:"
        )

        gen_resp = requests.post(
            f"{url}/ml/v1/text/generation?version=2023-05-29",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "model_id": "ibm/granite-13b-instruct-v2",
                "input": prompt,
                "parameters": {"decoding_method": "greedy", "max_new_tokens": 200, "temperature": 0},
                "project_id": project_id,
            },
            timeout=30,
        )
        gen_resp.raise_for_status()
        raw = gen_resp.json()["results"][0]["generated_text"].strip()

        # Extract JSON block
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            return None
        parsed = json.loads(m.group(0))

        # Validate required fields
        required = {"aoi_name", "bbox", "start", "end", "product"}
        if not required.issubset(parsed.keys()):
            return None

        return QuerySpec(
            aoi_name=str(parsed["aoi_name"]),
            bbox=list(parsed["bbox"]),
            start=str(parsed["start"]),
            end=str(parsed["end"]),
            product=parsed.get("product", "true_color"),
            max_aoi_cloud=float(parsed.get("max_aoi_cloud", 100.0)),
            source_text=text,
            parsed_by="llm",
        )
    except Exception:
        return None


def parse(text: str) -> QuerySpec:
    """LLM if available and valid, else rules. Always returns a usable spec."""
    aois = load_aois()
    if os.environ.get("WATSONX_API_KEY"):
        result = parse_llm(text, aois)
        if result is not None:
            return result
    return parse_rules(text)
