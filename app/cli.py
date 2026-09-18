"""CHIP CLI entry point."""
import argparse
import os
import sys

from app.search import search_items
from app.chip import true_color_png


def smoke_test():
    """Phase 1 check: download Willamette true-colour chip for August 2024."""
    bbox = [-123.10, 44.52, -123.02, 44.58]
    start = "2024-08-01"
    end = "2024-08-31"
    print(f"Searching Willamette AOI {bbox} from {start} to {end}...")
    items = search_items(bbox, start, end, limit=20)
    if not items:
        print("ERROR: no items returned. Widen the date range.", file=sys.stderr)
        sys.exit(1)
    item = items[0]
    print(f"Selected item: {item['id']}  datetime={item['properties'].get('datetime','?')}")
    os.makedirs("outputs", exist_ok=True)
    out = true_color_png(item, bbox, "outputs/smoke_test.png")
    print(f"Written: {out}")


def run_query(text: str):
    """Full pipeline run for a natural-language query."""
    from app.parse import parse
    from app.pipeline import run

    spec = parse(text)
    print(f"\nParsed spec:")
    print(f"  aoi_name   : {spec.aoi_name}")
    print(f"  bbox       : {spec.bbox}")
    print(f"  start      : {spec.start}")
    print(f"  end        : {spec.end}")
    print(f"  product    : {spec.product}")
    print(f"  max_cloud  : {spec.max_aoi_cloud}")
    print(f"  parsed_by  : {spec.parsed_by}")

    result = run(spec)

    print(f"\nCandidates ({len(result.candidates)} scored):")
    print(f"  {'id':<45} {'scene%':>7} {'aoi%':>7} {'gap':>7} {'sel':>4}")
    print(f"  {'-'*45} {'-'*7} {'-'*7} {'-'*7} {'-'*4}")
    for c in result.candidates:
        sel = "✓" if c.selected else ""
        gap = c.aoi_cloud_pct - c.scene_cloud_pct
        print(f"  {c.item_id:<45} {c.scene_cloud_pct:>7.1f} {c.aoi_cloud_pct:>7.1f} {gap:>+7.1f} {sel:>4}")

    print(f"\nQA Checks:")
    status_icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}
    for ch in result.checks:
        icon = status_icon.get(ch.status, "?")
        print(f"  [{icon}] {ch.id:<25} {ch.message}")

    print(f"\nOutputs:")
    for label, path in [
        ("preview_png   ", result.preview_png),
        ("product_png   ", result.product_png),
        ("cloudmask_png ", result.cloudmask_png),
        ("geotiff       ", result.geotiff),
    ]:
        if path:
            print(f"  {label}: {path}")


def main():
    parser = argparse.ArgumentParser(description="CHIP — Cloud-Honest Image Pipeline")
    parser.add_argument("query", nargs="?", help="Natural-language query")
    parser.add_argument("--smoke", action="store_true", help="Run smoke test (Willamette Aug 2024)")
    args = parser.parse_args()

    if args.smoke:
        smoke_test()
    elif args.query:
        run_query(args.query)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
