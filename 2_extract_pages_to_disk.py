# extract_pages_to_disk.py
# Reads a CSV (url,title), renders each page (Playwright), extracts main text + images,
# writes out/ per-page folders: content.md, raw.html (with data-x-img-idx tags),
# images/, images_map.json, and a master manifest CSV.

import asyncio, csv, pathlib, re, os, urllib.parse, json
from typing import List, Dict, Any, Optional, Tuple
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

INPUT_CSV = "whatsnew_children.csv"
OUT_ROOT = pathlib.Path("out")
UA = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari (vault-extractor)"
REQUEST_TIMEOUT = 30

def abs_url(base: str, href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    return urllib.parse.urljoin(base, href.split("#")[0])

def mkdirs(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def polite_get(url: str, stream=False):
    return requests.get(url, headers={"User-Agent": UA}, timeout=REQUEST_TIMEOUT, stream=stream)

def choose_container_html(article_html: str) -> BeautifulSoup:
    s = BeautifulSoup(article_html, "lxml")
    cands = s.find_all(["article","main","section","div"])
    def score(node):
        return len(node.find_all(["p","li"])) * 10 + len(node.get_text(strip=True))
    return max(cands, key=score) if cands else s

def html_to_markdown_like(article_html: str) -> Tuple[str, List[str]]:
    s = BeautifulSoup(article_html, "lxml")
    for bad in s.select("nav, header, footer, aside, .breadcrumb, .breadcrumbs, .toc, .nav, .related"):
        bad.decompose()

    lines, bullets = [], []
    for h in s.select("h1, h2, h3"):
        level = {"h1":"#","h2":"##","h3":"###"}.get(h.name.lower(),"##")
        text = h.get_text(" ", strip=True)
        if text: lines.append(f"{level} {text}")

    for ul in s.select("ul, ol"):
        for li in ul.select(":scope > li"):
            t = li.get_text(" ", strip=True)
            if t: lines.append(f"- {t}"); bullets.append(t)

    for p in s.select("p"):
        t = p.get_text(" ", strip=True)
        if t: lines.append(t)

    for table in s.select("table"):
        for tr in table.select("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.select("th,td")]
            if any(cells): lines.append("| " + " | ".join(cells) + " |")

    md = "\n\n".join(lines).strip()
    return md, bullets

JS_GET_MAIN_AND_IMAGES = """
() => {
  function score(n){ const ps=n.querySelectorAll('p,li'); return ps.length*10 + (n.innerText||'').length; }
  const cands = Array.from(document.querySelectorAll('article,main,section,div'));
  let best=null, sc=-1;
  for (const n of cands){ const s=score(n); if(s>sc){sc=s; best=n;} }
  const container = best || document.body;

  // Assign stable indices to images in DOM order
  const imgEls = Array.from(container.querySelectorAll('img, figure img'));
  imgEls.forEach((im, i) => im.setAttribute('data-x-img-idx', String(i+1)));

  function pick(el){
    const attrs=['src','data-src','data-original','data-lazy','data-image','data-thumb'];
    for(const a of attrs){
      const v = el.getAttribute(a);
      if (v) return { url:v, alt:(el.getAttribute('alt')||'').trim() };
    }
    return null;
  }

  const imgs = [];
  imgEls.forEach((im, i) => {
    const got = pick(im);
    if (got) imgs.push({ idx: i+1, ...got });
  });

  // Title
  let title='';
  const h = container.querySelector('h1, h2') || document.querySelector('h1, h2');
  if (h) title=(h.innerText||h.textContent||'').trim();
  if (!title) title=document.title||'';

  return { title, html: container.innerHTML, images: imgs };
}
"""

async def render_and_extract(ctx, url: str) -> Dict[str, Any]:
    page = await ctx.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try: await page.wait_for_load_state("networkidle", timeout=8000)
    except: pass
    data = await page.evaluate(JS_GET_MAIN_AND_IMAGES)
    # absolutize and carry through idx
    for im in data["images"]:
        im["abs"] = abs_url(url, im.get("url"))
    await page.close()
    return data

def download_images(imgs: List[Dict[str, Any]], folder: pathlib.Path) -> List[Dict[str, Any]]:
    """
    Save each image as img_{idx:02d}.{ext} (idx from DOM).
    Return list with idx, filename, url, alt.
    """
    saved = []
    mkdirs(folder)
    for im in imgs:
        idx = im.get("idx")
        url = im.get("abs") or im.get("url")
        if not url or not idx:
            continue
        try:
            r = polite_get(url, stream=True)
            if r.status_code != 200:
                continue
            ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
            if ext not in (".png",".jpg",".jpeg",".gif",".bmp",".webp",".svg",".tif",".tiff"):
                ext = ".png"
            fn = f"img_{int(idx):02d}{ext}"
            out = folder / fn
            with open(out, "wb") as f:
                for chunk in r.iter_content(65536):
                    if chunk: f.write(chunk)
            saved.append({"idx": int(idx), "filename": out.name, "url": url, "alt": im.get("alt","")})
        except Exception:
            continue
    # sort by idx
    saved.sort(key=lambda x: x["idx"])
    return saved

async def main():
    import argparse
    ap = argparse.ArgumentParser(description="Extract text+images from a CSV of Autodesk What's New pages.")
    ap.add_argument("--in", dest="in_csv", default=INPUT_CSV, help="Input CSV with columns: url,title")
    ap.add_argument("--out", dest="out_dir", default=str(OUT_ROOT), help="Output directory (default: out)")
    args = ap.parse_args()

    in_csv = pathlib.Path(args.in_csv)
    out_root = pathlib.Path(args.out_dir)
    mkdirs(out_root)

    # read CSV
    pages = []
    with in_csv.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("url"):
                pages.append({"url": r["url"].strip(), "title": (r.get("title") or "").strip()})

    manifest = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width":1400,"height":1600})

        for i, item in enumerate(pages, start=1):
            url = item["url"]
            print(f"[{i}/{len(pages)}] extracting: {url}")

            data = await render_and_extract(ctx, url)
            title = (data.get("title") or item.get("title") or url).strip()

            # Folder slug
            from slugify import slugify
            slug = slugify(title)[:80] or f"page_{i:02d}"
            page_dir = out_root / slug
            imgs_dir = page_dir / "images"
            mkdirs(page_dir)

            # The container HTML already has data-x-img-idx attributes → save as raw.html
            raw_html_path = page_dir / "raw.html"
            raw_html_path.write_text(data.get("html") or "", encoding="utf-8")

            # Download images using stable idx-based filenames
            downloaded = download_images(data.get("images", []), imgs_dir)

            # Save images_map.json for exact mapping
            (page_dir / "images_map.json").write_text(json.dumps(downloaded, ensure_ascii=False, indent=2), encoding="utf-8")

            # Markdown for quick QA (optional)
            md_html = choose_container_html(data.get("html") or "").decode() if hasattr(bytes, 'decode') else (data.get("html") or "")
            md, _ = html_to_markdown_like(md_html)
            if downloaded:
                md += "\n\n## Images\n"
                for d in downloaded:
                    md += f"![{d.get('alt','')}]({('images/' + d['filename'])})\n"
            (page_dir / "content.md").write_text(md, encoding="utf-8")

            manifest.append({
                "title": title,
                "url": url,
                "folder": str(page_dir),
                "markdown": str(page_dir / "content.md"),
                "raw_html": str(raw_html_path),
                "images": ";".join(d["filename"] for d in downloaded) if downloaded else ""
            })

        await ctx.close(); await browser.close()

    # write manifest
    with (out_root / "index.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title","url","folder","markdown","raw_html","images"])
        w.writeheader(); w.writerows(manifest)

    print(f"[ok] extracted {len(manifest)} pages -> {out_root}/index.csv")

if __name__ == "__main__":
    asyncio.run(main())
