# sectionize_to_slidespec.py
# Convert extracted pages (from extract_pages_to_disk.py) into slide specs (JSON).
# - Sections at H2/H3/H4
# - Images are assigned to the CURRENT section immediately (no look-ahead/pending)
# - Exact image→file mapping via images_map.json (uses <img data-x-img-idx="N">)
# - Drops social/feedback boilerplate
# - Ignores only semantic logos/headers (filename contains "logo" or "header"; no img_01 blanket)
# - Writes slidespec.json per page + slidespec_index.csv for the whole set
#
# Usage:
#   python sectionize_to_slidespec.py --in_dir out_2026
#   python sectionize_to_slidespec.py --in_dir out_2026 --out_dir out_2026   # (out_dir defaults to in_dir)

import argparse, csv, json, re, sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup

# ---------------- Config / Rules ----------------

SOCIAL_STRIP_VALUES = {"email","facebook","twitter","linkedin","yes","no"}
SOCIAL_STRIP_SUBSTRINGS = (
    "share","feedback","was this helpful",
    "teilen","bewertung","rückmeldung"
)

# ---------------- Utils ----------------

def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

def is_logo_filename(fn: str) -> bool:
    """Ignore obvious chrome; do NOT blanket-skip the first image anymore."""
    n = fn.lower()
    return ("logo" in n) or ("header" in n)

def clean_text_line(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())

def should_strip_line(t: str) -> bool:
    t0 = clean_text_line(t).lower()
    if not t0:
        return True
    if t0 in SOCIAL_STRIP_VALUES:
        return True
    if any(sub in t0 for sub in SOCIAL_STRIP_SUBSTRINGS):
        return True
    return False

def choose_container(soup: BeautifulSoup) -> BeautifulSoup:
    """Pick a dense content container by a simple heuristic."""
    cands = soup.find_all(["article","main","section","div"])
    if not cands:
        return soup
    def score(n):
        try:
            return len(n.find_all(["p","li"])) * 10 + len(n.get_text(strip=True))
        except Exception:
            return 0
    return max(cands, key=score)

def load_images_map(page_dir: Path) -> Dict[int, str]:
    """
    Read images_map.json produced by the extractor:
      [ {"idx": 2, "filename": "img_02.png", ...}, ... ]
    Returns { idx: filename }
    """
    m = page_dir / "images_map.json"
    out: Dict[int,str] = {}
    if m.exists():
        try:
            arr = json.loads(m.read_text(encoding="utf-8"))
            for item in arr:
                idx = int(item.get("idx", 0))
                fn  = item.get("filename")
                if idx and fn:
                    out[idx] = fn
        except Exception:
            pass
    return out

# ---------------- Sectionizing ----------------

def sectionize(container: BeautifulSoup) -> List[Dict[str, Any]]:
    """
    Walk the container in DOM order and split into sections at H2/H3/H4.
    Images are assigned to the CURRENT section immediately (no "forward-looking").
    If an image appears before any heading, we create an "Overview" section first.
    Each section: {title, level, paras, lists, tables, images:[{idx, alt, filename=None}]}
    """
    sections: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def start_section(title: str, level: str):
        nonlocal current
        title = clean_text_line(title) or "Section"
        current = {"title": title, "level": level, "paras": [], "lists": [], "tables": [], "images": []}
        sections.append(current)

    for el in container.descendants:
        if not getattr(el, "name", None):
            continue
        nm = el.name.lower()

        # New section on H2/H3/H4
        if nm in ("h2","h3","h4"):
            start_section(el.get_text(" ", strip=True), nm)
            continue

        # Ensure we have a section if content appears before any heading
        if current is None and nm in ("p","ul","ol","table","img"):
            start_section("Overview", "h2")

        if current is None:
            continue

        if nm == "p":
            txt = clean_text_line(el.get_text(" ", strip=True))
            if txt and not should_strip_line(txt):
                current["paras"].append(txt)

        elif nm in ("ul","ol"):
            items = []
            for li in el.select(":scope > li"):
                txt = clean_text_line(li.get_text(" ", strip=True))
                if txt and not should_strip_line(txt):
                    items.append(txt)
            if items:
                current["lists"].append(items)

        elif nm == "table":
            rows = []
            for tr in el.select("tr"):
                cells = [clean_text_line(td.get_text(" ", strip=True)) for td in tr.select("th,td")]
                if any(cells):
                    rows.append("| " + " | ".join(cells) + " |")
            if rows:
                current["tables"].append(rows)

        elif nm == "img":
            # Assign to current section immediately (no pending)
            idx_attr = el.get("data-x-img-idx")
            idx = int(idx_attr) if (idx_attr and idx_attr.isdigit()) else None
            alt = (el.get("alt") or "").strip()
            current["images"].append({"idx": idx, "alt": alt, "filename": None})

    # Remove sections that ended up empty after stripping
    sections = [s for s in sections if s["paras"] or s["lists"] or s["tables"] or s["images"]]
    return sections

# ---------------- Image mapping ----------------

def assign_images_to_sections(sections: List[Dict[str, Any]], page_dir: Path):
    """
    Resolve each image slot's filename.
    1) Prefer exact mapping via data-x-img-idx → images_map.json
    2) Fallback by remaining order (skipping obvious logos/headers)
    """
    img_dir = page_dir / "images"
    files = sorted([p for p in img_dir.glob("*") if p.is_file()], key=lambda x: x.name.lower())
    idx_map = load_images_map(page_dir)

    # First pass: idx-based binding
    for sec in sections:
        for img in sec["images"]:
            idx = img.get("idx")
            if idx and idx in idx_map:
                img["filename"] = idx_map[idx]

    # Second pass: order fallback for any unresolved
    unresolved = []
    for si, sec in enumerate(sections):
        for ii, img in enumerate(sec["images"]):
            if not img.get("filename"):
                unresolved.append((si, ii))

    # Available file queue (skip logos/headers)
    file_queue = [p.name for p in files if not is_logo_filename(p.name)]
    used = {img["filename"] for sec in sections for img in sec["images"] if img.get("filename")}
    file_queue = [fn for fn in file_queue if fn not in used]

    for (si, ii), fn in zip(unresolved, file_queue):
        sections[si]["images"][ii]["filename"] = fn

# ---------------- Bullets ----------------

def to_bullets(paras: List[str], lists: List[List[str]], tables: List[List[str]]) -> List[str]:
    bullets: List[str] = []

    # Lists first
    for lst in lists:
        for item in lst:
            if item and not should_strip_line(item):
                bullets.append(item)

    # Then paragraphs (split long ones at sentence boundaries)
    for p in paras:
        if p and not should_strip_line(p):
            if len(p) > 300:
                parts = re.split(r"(?<=[.!?])\s+", p)
                bullets.extend([clean_text_line(x) for x in parts if x.strip()])
            else:
                bullets.append(p)

    # Then tables (flattened rows)
    for rows in tables:
        for r in rows:
            r = clean_text_line(r)
            if r and not should_strip_line(r):
                bullets.append(r)

    # Dedup while preserving order
    seen = set(); out = []
    for b in bullets:
        k = b.lower()
        if k in seen:
            continue
        seen.add(k); out.append(b)
    return out

# ---------------- Build slidespec ----------------

def build_slidespec(page_dir: Path, page_url: str, page_title: str, container: BeautifulSoup) -> Dict[str, Any]:
    sections = sectionize(container)
    assign_images_to_sections(sections, page_dir)

    slides = []
    for sec in sections:
        bullets = to_bullets(sec["paras"], sec["lists"], sec["tables"])
        images = []
        for im in sec["images"]:
            fn = im.get("filename")
            if not fn:
                continue
            if is_logo_filename(fn):
                continue
            images.append({
                "path": f"images/{fn}",
                "caption": clean_text_line(im.get("alt") or "")
            })
        if not bullets and not images:
            continue
        slides.append({
            "title": sec["title"],
            "bullets": bullets,
            "images": images,
            "notes": f"Source: {page_url}" if page_url else ""
        })

    return {
        "page_title": page_title,
        "source_url": page_url,
        "slides": slides
    }

# ---------------- Main ----------------

def main():
    ap = argparse.ArgumentParser(description="Sectionize extracted pages into slide specs (JSON).")
    ap.add_argument("--in_dir", required=True, help="Input directory from extractor (e.g., out_2026).")
    ap.add_argument("--out_dir", required=False, help="Output directory (defaults to --in_dir).")
    args = ap.parse_args()

    in_root = Path(args.in_dir).resolve()
    out_root = Path(args.out_dir).resolve() if args.out_dir else in_root
    if not in_root.exists():
        print(f"[error] Input directory not found: {in_root}", file=sys.stderr)
        sys.exit(1)

    # Page folders contain raw.html
    page_dirs = sorted(
        [p for p in in_root.iterdir() if p.is_dir() and (p / "raw.html").exists()],
        key=lambda x: x.name.lower()
    )

    # Optional URL lookup from index.csv
    url_lookup = {}
    idx_csv = in_root / "index.csv"
    if idx_csv.exists():
        try:
            with idx_csv.open(newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    url_lookup[Path(r.get("folder","")).name] = r.get("url","") or ""
        except Exception:
            pass

    index_rows = []

    for i, page_dir in enumerate(page_dirs, start=1):
        html = read_text(page_dir / "raw.html")
        if not html.strip():
            print(f"[warn] empty raw.html for {page_dir.name}, skipping")
            continue

        soup = BeautifulSoup(html, "lxml")
        container = choose_container(soup)

        h1 = soup.find("h1") or container.find("h1")
        page_title = h1.get_text(" ", strip=True) if h1 else page_dir.name
        source_url = url_lookup.get(page_dir.name, "")

        spec = build_slidespec(page_dir, source_url, page_title, container)

        slidespec_path = page_dir / "slidespec.json"
        slidespec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

        index_rows.append({
            "folder": str(page_dir),
            "page_title": spec["page_title"],
            "source_url": spec["source_url"],
            "slidespec": str(slidespec_path),
            "num_slides": len(spec["slides"])
        })

        print(f"[{i}/{len(page_dirs)}] wrote {slidespec_path.name} ({len(spec['slides'])} slides) for {page_dir.name}")

    master_path = out_root / "slidespec_index.csv"
    with master_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["folder","page_title","source_url","slidespec","num_slides"])
        w.writeheader(); w.writerows(index_rows)

    print(f"[ok] Wrote {len(index_rows)} slide specs -> {master_path}")

if __name__ == "__main__":
    main()
