# clean_slidespecs_overview_and_dupes.py
# Purpose:
#   1) Delete true-duplicate slides across ALL pages (exact same displayed text),
#      while EXCLUDING "Overview" from this step (we handle those specially).
#   2) Collapse "Overview" slides: keep only bullets that are UNIQUE to that page
#      compared to the common boilerplate seen in ALL Overview slides.
#   3) If an Overview becomes empty (no unique bullets and no images), drop it.
#   4) Write cleaned slidespec.json into a new output directory and a cleaned index CSV.
#
# Usage:
#   python clean_slidespecs_overview_and_dupes.py --in_dir out_2026 --out_dir out_2026_clean
#
# Then build PPT from out_2026_clean/slidespec_index.csv

import argparse, csv, json, re, shutil
from pathlib import Path
from typing import List, Dict, Any, Tuple

# ----- normalization helpers (display text, language-agnostic) -----

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)

def norm_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = s.replace("\u2019","'").replace("\u2018","'").replace("\u201c",'"').replace("\u201d",'"')
    s = URL_RE.sub("", s)                 # drop URLs
    s = re.sub(r"\s+", " ", s).strip()
    return s

def slide_display_key(slide: Dict[str, Any]) -> str:
    """Displayed text only: title + bullets, normalized (images ignored)."""
    title = norm_text(slide.get("title", ""))
    bullets = [norm_text(b) for b in (slide.get("bullets") or []) if norm_text(b)]
    return " || ".join([title] + bullets) if bullets or title else ""

def is_overview(slide: Dict[str, Any]) -> bool:
    return norm_text(slide.get("title","")) == "overview"

# ----- IO helpers -----

def read_specs(in_root: Path) -> List[Tuple[Path, dict]]:
    """Return list of (page_dir, spec_dict) for all pages."""
    pages = sorted([p for p in in_root.iterdir() if p.is_dir() and (p/"slidespec.json").exists()],
                   key=lambda x: x.name.lower())
    out = []
    for p in pages:
        spec = json.loads((p/"slidespec.json").read_text(encoding="utf-8"))
        out.append((p, spec))
    return out

def write_cleaned_spec(out_page_dir: Path, spec: dict):
    out_page_dir.mkdir(parents=True, exist_ok=True)
    # Copy helpful files for traceability
    for fname in ["raw.html", "content.md", "images_map.json"]:
        src = out_page_dir.parent / fname  # WRONG: parent of out_page_dir is out_root; we need original page dir
        # we’ll fix in the caller (we need original page dir passed in). Placeholder here.
    # The caller writes files. This function only writes slidespec.json
    (out_page_dir / "slidespec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )

# ----- core cleaning -----

def compute_true_duplicate_keys(all_slides: List[Tuple[Path, Dict[str,Any]]]) -> set:
    """
    Build a set of slide keys that appear >= 2 times across ALL pages,
    excluding any slide whose title is 'overview' (handled separately).
    """
    counts = {}
    for _page_dir, slide in all_slides:
        if is_overview(slide):  # skip: handled by overview collapse
            continue
        key = slide_display_key(slide)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    # mark only keys with count >= 2
    dup_keys = {k for k, n in counts.items() if n >= 2}
    return dup_keys

def collapse_overview_boilerplate(all_overviews: List[List[str]]) -> set:
    """
    Given list of Overview bullet lists (normalized),
    compute the intersection = bullets present in ALL Overviews (global boilerplate).
    """
    if not all_overviews:
        return set()
    # Start with set of first, intersect through the rest
    common = set(all_overviews[0])
    for blist in all_overviews[1:]:
        common &= set(blist)
        if not common:
            break
    return common

def clean_specs(in_root: Path, out_root: Path):
    out_root.mkdir(parents=True, exist_ok=True)

    # copy top-level index.csv if present
    if (in_root / "index.csv").exists():
        shutil.copy2(in_root / "index.csv", out_root / "index.csv")

    # Load all specs
    specs = read_specs(in_root)

    # Flatten all slides for duplicate detection
    all_slides = []  # list of (page_dir, slide_dict)
    overview_bullets_norm = []  # list of normalized bullet lists for Overviews
    for page_dir, spec in specs:
        for slide in spec.get("slides", []):
            all_slides.append((page_dir, slide))
            if is_overview(slide):
                bullets_norm = [norm_text(b) for b in (slide.get("bullets") or []) if norm_text(b)]
                overview_bullets_norm.append(bullets_norm)

    # 1) Global true duplicates (non-Overview): delete ALL occurrences
    true_dupe_keys = compute_true_duplicate_keys(all_slides)

    # 2) Compute common boilerplate across all Overviews
    common_overview_core = collapse_overview_boilerplate(overview_bullets_norm)

    cleaned_rows = []

    for i, (page_dir, spec) in enumerate(specs, start=1):
        slides = spec.get("slides", [])
        cleaned = []

        for slide in slides:
            title_norm = norm_text(slide.get("title",""))

            if not is_overview(slide):
                # Drop if true duplicate across pages
                key = slide_display_key(slide)
                if key and key in true_dupe_keys:
                    continue
                # keep as-is
                cleaned.append(slide)
            else:
                # Collapse Overview: keep only bullets NOT in the global common core
                orig_bullets = slide.get("bullets", []) or []
                kept_bullets = []
                for b in orig_bullets:
                    if norm_text(b) not in common_overview_core:
                        kept_bullets.append(b)

                # If Overview ends up empty (no bullets + no images), drop it
                has_imgs = any(img.get("path") for img in (slide.get("images") or []))
                if kept_bullets or has_imgs or title_norm not in ("","overview"):
                    # replace bullets with the unique tail only
                    new_slide = dict(slide)
                    new_slide["bullets"] = kept_bullets
                    cleaned.append(new_slide)
                # else: drop

        # Write cleaned spec into mirrored folder under out_root
        out_page_dir = out_root / page_dir.name
        out_page_dir.mkdir(parents=True, exist_ok=True)

        # copy reference files/images for context
        for fname in ["raw.html", "content.md", "images_map.json"]:
            src = page_dir / fname
            if src.exists():
                shutil.copy2(src, out_page_dir / fname)
        img_dir = page_dir / "images"
        if img_dir.exists():
            dst_img_dir = out_page_dir / "images"
            if not dst_img_dir.exists():
                shutil.copytree(img_dir, dst_img_dir)

        cleaned_spec = {
            "page_title": spec.get("page_title",""),
            "source_url": spec.get("source_url",""),
            "slides": cleaned
        }
        (out_page_dir / "slidespec.json").write_text(
            json.dumps(cleaned_spec, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        cleaned_rows.append({
            "folder": str(out_page_dir),
            "page_title": cleaned_spec["page_title"],
            "source_url": cleaned_spec["source_url"],
            "slidespec": str(out_page_dir / "slidespec.json"),
            "num_slides": len(cleaned)
        })

        print(f"[{i}/{len(specs)}] {page_dir.name}: {len(slides)} -> {len(cleaned)} slides "
              f"(overview core={len(common_overview_core)}, dup-keys={len(true_dupe_keys)})")

    # Write cleaned index
    out_index = out_root / "slidespec_index.csv"
    with out_index.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["folder","page_title","source_url","slidespec","num_slides"])
        w.writeheader(); w.writerows(cleaned_rows)

    print(f"[ok] Wrote cleaned specs -> {out_index} (pages: {len(cleaned_rows)})")

def main():
    ap = argparse.ArgumentParser(description="Clean slidespecs: delete true duplicates; collapse Overview to unique bullets.")
    ap.add_argument("--in_dir", required=True, help="Input directory containing per-page slidespec.json (e.g., out_2026)")
    ap.add_argument("--out_dir", required=True, help="Output directory for cleaned specs (e.g., out_2026_clean)")
    args = ap.parse_args()

    in_root = Path(args.in_dir).resolve()
    out_root = Path(args.out_dir).resolve()
    if not in_root.exists():
        raise SystemExit(f"Input directory not found: {in_root}")

    clean_specs(in_root, out_root)

if __name__ == "__main__":
    main()
