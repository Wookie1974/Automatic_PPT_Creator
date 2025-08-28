# probe_children_shadow.py
# Discover "down-only" child pages from an Autodesk Help "What's New" hub.
# Works for any hub URL (2026, 2026.1, 2026.2, 2027, …).
# Requires: pip install playwright && playwright install chromium

import asyncio, csv, urllib.parse, argparse, re
from playwright.async_api import async_playwright

# -------------------------- helpers --------------------------

def abs_url(base, href):
    if not href: return None
    return urllib.parse.urljoin(base, href.split("#")[0])

def hub_prefix(hub_url: str) -> str:
    """
    Keep scheme/host and path up to .../view/PRODUCT/VERSION/LOCALE/
    so we don't wander to other versions/locales.
    """
    p = urllib.parse.urlparse(hub_url)
    parts = p.path.strip("/").split("/")
    if "view" in parts:
        i = parts.index("view")
        if len(parts) >= i + 4:
            prefix_path = "/" + "/".join(parts[:i+4]) + "/"
            return f"{p.scheme}://{p.netloc}{prefix_path}"
    return f"{p.scheme}://{p.netloc}/"

def get_guid(u: str) -> str | None:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
    return q.get("guid", [None])[0]

# Hub pages typically have GUID like WN-<stuff>-UPDATES
HUB_GUID_RE = re.compile(r"^WN-[A-Z0-9._-]+-UPDATES$", re.I)

def is_hub_like_title(title: str) -> bool:
    """
    True for titles that are hub pages, e.g.:
      - "What's New in Vault 2026"
      - "2026.1 Updates and Enhancements"
      - "Updates and Enhancements" (no year)
    NOT true for child pages like:
      - "Workflow Enhancements (What's New in 2026.1)"
    """
    t = (title or "").strip()
    return bool(
        re.match(r"(?i)^\s*what'?s\s+new\b", t) or
        re.match(r"(?i)^\s*\d{4}(?:\.\d+)?\s+updates?\s+and\s+enhancements?\s*$", t) or
        re.match(r"(?i)^\s*updates?\s+and\s+enhancements?\s*$", t)
    )

SHADOW_SCRAPER = """
() => {
  const out = [];
  const seen = new Set();
  function push(href, text){
    if(!href) return;
    if(seen.has(href)) return;
    seen.add(href);
    out.push({href, text: (text||"").trim()});
  }
  function walk(root){
    root.querySelectorAll('a[href]').forEach(a => push(a.href || a.getAttribute('href'), a.innerText || a.textContent));
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let node;
    while((node = tw.nextNode())){
      if(node.shadowRoot) walk(node.shadowRoot);
      if(node.tagName === 'IFRAME' && node.contentDocument){
        try { walk(node.contentDocument); } catch(e){}
      }
    }
  }
  walk(document);
  return out;
}
"""

async def render(ctx, url):
    page = await ctx.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except:
        pass
    return page

async def get_links_from_hub(ctx, hub, allow_prefix):
    """Return viewer links (with guid=) in DOM order from the hub page (shadow-aware)."""
    page = await render(ctx, hub)
    links = await page.evaluate(SHADOW_SCRAPER)
    await page.close()

    kept, seen = [], set()
    for l in links:
        href = abs_url(hub, l.get("href"))
        if not href: 
            continue
        if not href.startswith(allow_prefix):
            continue
        if "guid=" not in href:
            continue
        if href == hub:
            continue
        if href in seen:
            continue
        seen.add(href)
        kept.append({"href": href, "text": (l.get("text","") or "").strip()})
    return kept  # DOM order preserved

async def page_title_and_backlink(ctx, child_url, hub_url):
    """Return (title, has_backlink_to_hub)."""
    page = await render(ctx, child_url)
    title = await page.evaluate("""() => {
        const h = document.querySelector('h1, h2');
        if (h && h.innerText) return h.innerText.trim();
        return document.title || "";
    }""")
    links = await page.evaluate(SHADOW_SCRAPER)
    await page.close()
    has_backlink = any(abs_url(child_url, x.get("href")) == hub_url for x in links)
    return (title or "").strip(), has_backlink

async def ok_status(ctx, url):
    resp = await ctx.request.get(url)
    return 200 <= resp.status < 400

# -------------------------- main --------------------------

async def main():
    ap = argparse.ArgumentParser(description="Discover 'down-only' child pages from an Autodesk Help hub (shadow-aware).")
    ap.add_argument("--hub", required=False, help="Hub URL (What's New page)")
    ap.add_argument("--out", required=False, default="whatsnew_children.csv", help="Output CSV filename")
    args = ap.parse_args()

    HUB = args.hub
    if not HUB:
        HUB = input("Please enter the Autodesk Help hub URL: ").strip()
        if not HUB:
            print("Error: Hub URL is required.")
            return

    ALLOW_PREFIX = hub_prefix(HUB)
    HUB_GUID = get_guid(HUB)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width":1280,"height":1600})

        candidates = await get_links_from_hub(ctx, HUB, ALLOW_PREFIX)
        rows, seen = [], set()
        
        # 🔧 Mickey-filter: the last link on the page is a sideways/parent link — drop it.
        if candidates:
            candidates = candidates[:-1]

        for c in candidates:
            url = c["href"]
            if url in seen: 
                continue
            seen.add(url)

            guid = get_guid(url)

            # drop hub itself
            if url == HUB:
                continue

            # drop *other* hub pages (e.g., WN-2026-1-UPDATES when current hub is WN-2026-UPDATES)
            if guid and HUB_GUID_RE.match(guid) and guid != HUB_GUID:
                continue

            # quick 200-check
            if not await ok_status(ctx, url):
                continue

            title, has_backlink = await page_title_and_backlink(ctx, url, HUB)
            if not has_backlink:
                continue

            # HARD RULE: any page whose title looks like a hub ("What's New …" OR "... Updates and Enhancements")
            # is excluded unless it's the exact hub URL we started from.
            if is_hub_like_title(title) and url != HUB:
                continue

            rows.append({"url": url, "title": title})

        await ctx.close(); await browser.close()

    # Output in hub DOM order (no sort)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["url","title"])
        w.writeheader(); w.writerows(rows)

    print(f"[ok] Hub: {HUB}")
    print(f"[ok] Allowed prefix: {ALLOW_PREFIX}")
    print(f"[ok] Found {len(rows)} child links -> {args.out}")
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['title']}\n   {r['url']}")

if __name__ == "__main__":
    asyncio.run(main())

