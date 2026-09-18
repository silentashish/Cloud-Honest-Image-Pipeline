"""The contract between modules. Keep these dataclasses boring and stable."""
from dataclasses import dataclass, field
from typing import Literal, Optional

Product = Literal["true_color", "ndvi", "ndwi"]
Status = Literal["pass", "warn", "fail"]


@dataclass
class QuerySpec:
    """The parsed intent of a sentence. Output of parse.py."""
    aoi_name: str
    bbox: list[float]                 # [w, s, e, n] in EPSG:4326
    start: str                        # ISO date, inclusive
    end: str                          # ISO date, inclusive
    product: Product = "true_color"
    max_aoi_cloud: float = 100.0      # the HONEST threshold, applied over the AOI
    source_text: str = ""
    parsed_by: Literal["rules", "llm"] = "rules"


@dataclass
class Candidate:
    """One scene considered for the answer. Both cloud numbers, always."""
    item_id: str
    datetime: str
    scene_cloud_pct: float            # what the catalog claims (eo:cloud_cover)
    aoi_cloud_pct: float              # what is actually true over the AOI
    valid_pct: float                  # AOI coverage; < 50 means swath edge
    selected: bool = False

    @property
    def disagreement(self) -> float:
        """Signed gap. Positive = catalog understates cloud over the AOI."""
        return self.aoi_cloud_pct - self.scene_cloud_pct


@dataclass
class Check:
    """One QA finding. Plain data — do not build a rules engine."""
    id: str
    status: Status
    message: str
    evidence: dict = field(default_factory=dict)


@dataclass
class ChipResult:
    spec: QuerySpec
    selected: Optional[Candidate]
    candidates: list[Candidate]
    checks: list[Check]
    crs: str
    shape: list[int]
    preview_png: str = ""
    product_png: str = ""
    cloudmask_png: str = ""
    geotiff: str = ""
    stats: dict = field(default_factory=dict)
