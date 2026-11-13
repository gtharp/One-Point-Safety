#!/usr/bin/env python3
"""
Keeps the site's SEO plumbing in one place: per-page <head> tags and
sitemap.xml.

Usage:
    python3 scripts/build_seo.py                 # rewrite <head> SEO blocks + sitemap.xml
    python3 scripts/build_seo.py --sitemap-only  # just sitemap.xml (what the daily Action runs)
    python3 scripts/build_seo.py --check         # verify everything is in sync; writes nothing

Local .css/.js links in every page also get a ?v=<content hash> suffix
(styles.css?v=1a2b3c4d), so browsers fetch the new file as soon as it
changes instead of reusing a stale cached copy. Re-run this script after
editing styles.css or any .js file; --check fails if a suffix is stale.

The site has no templating, so PAGES below is the "shared layout" for SEO
tags: each page's title/description/canonical/Open Graph/Twitter tags are
generated from it and written between the <!-- seo:start --> and
<!-- seo:end --> markers in that page's <head>. Edit PAGES, re-run this
script, commit -- don't hand-edit inside the markers.

Every *.html in the site root goes into sitemap.xml automatically. A page
missing from PAGES still gets a sitemap entry, but --check fails until it's
given a title/description here.

<lastmod> is per page: each page lists the files its content comes from
(deps). Their combined hash is stored in scripts/sitemap_state.json; when
it changes, that page's lastmod becomes today. data/meta.json's
last_updated timestamp is ignored, since it changes on every data run even
when nothing else does.
"""

import argparse
import datetime
import glob
import hashlib
import html
import json
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "scripts" / "sitemap_state.json"
SITEMAP_FILE = ROOT / "sitemap.xml"
SITE_NAME = "One-Point Safety"
OG_IMAGE = "og-image.png"
OG_IMAGE_ALT = "One-Point Safety — tracking the NFL's rarest plays"

PAGES = {
    "index.html": {
        "path": "/",
        "title": "One-Point Safety | Tracking the NFL's Rarest Plays",
        "description": (
            "Track the NFL's rarest plays as they happen: one-point safeties, "
            "rare penalties, wild finishes and extreme weather, updated daily "
            "from real play-by-play data."
        ),
        "og_type": "website",
        "changefreq": "daily",
        "priority": "1.0",
        "deps": [
            "index.html", "app.js", "styles.css",
            "data/meta.json", "data/games.json", "data/events.json",
            "data/oddities.json", "data/weather.json",
        ],
    },
    "college.html": {
        "path": "/college.html",
        "title": "One-Point Safety | Tracking College Football's Rarest Plays",
        "description": (
            "Tracking college football's rarest plays across every FBS game: "
            "one-point safeties, targeting ejections, overtime shootouts and "
            "rare penalties, updated daily."
        ),
        "og_type": "website",
        "changefreq": "daily",
        "priority": "0.9",
        "deps": [
            "college.html", "app.js", "styles.css",
            "data/ncaa/meta.json", "data/ncaa/games.json", "data/ncaa/events/*.json",
            "data/ncaa/oddities.json",
        ],
    },
    "history.html": {
        "path": "/history.html",
        "title": "Football History Oddities & Banned Plays | One-Point Safety",
        "description": (
            "A sourced history of football's strangest moments: banned plays "
            "like the Flying Wedge, on-field deaths, rule changes, firsts and "
            "all-time weather records."
        ),
        "og_type": "article",
        "changefreq": "weekly",
        "priority": "0.8",
        "deps": [
            "history.html", "history.js", "flying-wedge.js", "styles.css",
            "data/history.json", "data/weather_records.json",
        ],
    },
    "miami-ohio-video-board-goalposts-penalty.html": {
        "path": "/miami-ohio-video-board-goalposts-penalty.html",
        "title": "Miami (OH) Fake Goalposts Video Board Penalty: NCAA Rule 9-2-1",
        "description": (
            "Miami (OH)'s video board showed fake goalposts behind the uprights "
            "on an Ohio field goal try, drawing a rare unsportsmanlike flag "
            "under NCAA Rule 9-2-1."
        ),
        "og_type": "article",
        "changefreq": "monthly",
        "priority": "0.8",
        "deps": ["miami-ohio-video-board-goalposts-penalty.html", "styles.css"],
    },
}

# Files in the site root that are HTML but aren't public pages.
EXCLUDE_RE = re.compile(r"^(google[0-9a-f]+|404)\.html$")

SEO_BLOCK_RE = re.compile(r"[ \t]*<!-- seo:start -->.*?<!-- seo:end -->\n?", re.S)
TITLE_RE = re.compile(r"[ \t]*<title>.*?</title>\n?", re.S)
ASSET_RE = re.compile(r'((?:href|src)=")([\w.-]+\.(?:css|js))(?:\?v=[0-9a-f]*)?(")')


def base_url():
    cname = ROOT / "CNAME"
    if not cname.exists():
        sys.exit("No CNAME file in the repo root; can't determine the canonical domain.")
    domain = cname.read_text().strip().lower()
    domain = re.sub(r"^https?://", "", domain).rstrip("/")
    if domain.startswith("www."):
        domain = domain[4:]
    return f"https://{domain}"


def site_pages():
    return sorted(
        p.name for p in ROOT.glob("*.html") if not EXCLUDE_RE.match(p.name)
    )


def canonical(page, base):
    cfg = PAGES.get(page)
    path = cfg["path"] if cfg else "/" + page
    return base + path


# ---------- <head> tags ----------

def head_block(page, base):
    cfg = PAGES[page]
    url = canonical(page, base)
    img = f"{base}/{OG_IMAGE}"
    e = lambda s: html.escape(s, quote=True)
    lines = [
        "<!-- seo:start -->",
        f"<title>{e(cfg['title'])}</title>",
        f'<meta name="description" content="{e(cfg["description"])}">',
        f'<link rel="canonical" href="{e(url)}">',
        f'<meta property="og:site_name" content="{e(SITE_NAME)}">',
        f'<meta property="og:type" content="{e(cfg["og_type"])}">',
        f'<meta property="og:title" content="{e(cfg["title"])}">',
        f'<meta property="og:description" content="{e(cfg["description"])}">',
        f'<meta property="og:url" content="{e(url)}">',
        f'<meta property="og:image" content="{e(img)}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        f'<meta property="og:image:alt" content="{e(OG_IMAGE_ALT)}">',
        '<meta property="og:locale" content="en_US">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{e(cfg["title"])}">',
        f'<meta name="twitter:description" content="{e(cfg["description"])}">',
        f'<meta name="twitter:image" content="{e(img)}">',
        f'<meta name="twitter:image:alt" content="{e(OG_IMAGE_ALT)}">',
        "<!-- seo:end -->",
    ]
    return "\n".join(lines) + "\n"


def render_head(page, text, base):
    block = head_block(page, base)
    if SEO_BLOCK_RE.search(text):
        return SEO_BLOCK_RE.sub(lambda _: block, text, count=1)
    # First run: replace the existing <title> with the generated block.
    if not TITLE_RE.search(text):
        raise SystemExit(f"{page}: no <title> or seo markers to replace")
    return TITLE_RE.sub(lambda _: block, text, count=1)


# ---------- asset cache-busting ----------

def version_assets(text):
    """Stamp each local .css/.js reference with a hash of the file's contents."""
    def sub(m):
        f = ROOT / m.group(2)
        if not f.is_file():
            return m.group(0)
        v = hashlib.sha256(f.read_bytes()).hexdigest()[:8]
        return f"{m.group(1)}{m.group(2)}?v={v}{m.group(3)}"
    return ASSET_RE.sub(sub, text)


# ---------- sitemap ----------

def dep_hash(deps):
    h = hashlib.sha256()
    for pattern in deps:
        for f in sorted(glob.glob(str(ROOT / pattern))):
            p = pathlib.Path(f)
            data = p.read_bytes()
            if p.name == "meta.json":
                try:
                    meta = json.loads(data)
                    meta.pop("last_updated", None)
                    data = json.dumps(meta, sort_keys=True).encode()
                except ValueError:
                    pass
            h.update(p.relative_to(ROOT).as_posix().encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def git_lastmod(deps):
    """Last commit date touching any dep, for pages with no saved state yet."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", *deps],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return out or None
    except (OSError, subprocess.CalledProcessError):
        return None


def compute_sitemap(base, today):
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    new_state, entries = {}, []
    for page in site_pages():
        cfg = PAGES.get(page, {})
        deps = cfg.get("deps", [page])
        digest = dep_hash(deps)
        prev = state.get(page)
        if prev and prev.get("hash") == digest:
            lastmod = prev["lastmod"]
        elif prev:
            lastmod = today
        else:
            lastmod = git_lastmod(deps) or today
        new_state[page] = {"hash": digest, "lastmod": lastmod}
        entries.append((canonical(page, base), lastmod, cfg))

    # Homepage first, then alphabetical.
    entries.sort(key=lambda e: (e[0] != base + "/", e[0]))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, cfg in entries:
        out.append("  <url>")
        out.append(f"    <loc>{html.escape(loc)}</loc>")
        out.append(f"    <lastmod>{lastmod}</lastmod>")
        if cfg.get("changefreq"):
            out.append(f"    <changefreq>{cfg['changefreq']}</changefreq>")
        if cfg.get("priority"):
            out.append(f"    <priority>{cfg['priority']}</priority>")
        out.append("  </url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n", new_state


# ---------- checks ----------

def check(base):
    problems = []
    pages = site_pages()
    for page in pages:
        text = (ROOT / page).read_text()
        if page not in PAGES:
            problems.append(f"{page}: not in PAGES (add a title/description in scripts/build_seo.py)")
            continue
        cfg = PAGES[page]
        if render_head(page, text, base) != text:
            problems.append(f"{page}: <head> SEO block out of date (run scripts/build_seo.py)")
        if version_assets(text) != text:
            problems.append(f"{page}: .css/.js ?v= cache-busting suffix out of date (run scripts/build_seo.py)")
        if not re.search(r'<html[^>]*\blang="en"', text):
            problems.append(f'{page}: missing <html lang="en">')
        if len(re.findall(r'<meta name="viewport"', text)) != 1:
            problems.append(f"{page}: needs exactly one viewport meta")
        if len(re.findall(r"<title>", text)) != 1:
            problems.append(f"{page}: needs exactly one <title>")
        canon = re.findall(r'<link rel="canonical" href="([^"]+)"', text)
        ogurl = re.findall(r'<meta property="og:url" content="([^"]+)"', text)
        if len(canon) != 1:
            problems.append(f"{page}: {len(canon)} canonical tags (want 1)")
        elif ogurl != canon:
            problems.append(f"{page}: og:url {ogurl} != canonical {canon[0]}")
        n = len(cfg["description"])
        if not 140 <= n <= 160:
            problems.append(f"{page}: description is {n} chars (want 140-160)")
    titles = [PAGES[p]["title"] for p in pages if p in PAGES]
    descs = [PAGES[p]["description"] for p in pages if p in PAGES]
    if len(set(titles)) != len(titles):
        problems.append("duplicate <title> across pages")
    if len(set(descs)) != len(descs):
        problems.append("duplicate description across pages")
    if not (ROOT / OG_IMAGE).exists():
        problems.append(f"{OG_IMAGE} missing")

    # Sitemap: well-formed, and every <loc> is a real page we know about.
    if not SITEMAP_FILE.exists():
        problems.append("sitemap.xml missing")
    else:
        try:
            tree = ET.parse(SITEMAP_FILE)
            ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            locs = [u.findtext("s:loc", namespaces=ns) for u in tree.getroot().findall("s:url", ns)]
            mods = [u.findtext("s:lastmod", namespaces=ns) for u in tree.getroot().findall("s:url", ns)]
            expected = {canonical(p, base) for p in pages}
            if set(locs) != expected or len(locs) != len(expected):
                problems.append(f"sitemap.xml URLs {sorted(locs)} != pages {sorted(expected)}")
            for m in mods:
                if not m or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m):
                    problems.append(f"sitemap.xml: bad lastmod {m!r}")
        except ET.ParseError as err:
            problems.append(f"sitemap.xml is not well-formed XML: {err}")

    robots = ROOT / "robots.txt"
    if not robots.exists() or f"Sitemap: {base}/sitemap.xml" not in robots.read_text():
        problems.append("robots.txt missing or lacks the Sitemap line")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sitemap-only", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    base = base_url()

    if args.check:
        problems = check(base)
        for p in problems:
            print("FAIL:", p)
        if problems:
            sys.exit(1)
        print(f"OK: {len(site_pages())} pages, heads + sitemap.xml + robots.txt in sync")
        return

    if not args.sitemap_only:
        for page in site_pages():
            if page not in PAGES:
                print(f"warning: {page} has no PAGES entry; skipping its <head> tags", file=sys.stderr)
                continue
            f = ROOT / page
            text = f.read_text()
            new = version_assets(render_head(page, text, base))
            if new != text:
                f.write_text(new)
                print(f"updated <head> in {page}")

    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    sitemap, state = compute_sitemap(base, today)
    for page in site_pages():
        if page not in PAGES:
            print(f"warning: {page} is in sitemap.xml but has no PAGES entry", file=sys.stderr)
    if not SITEMAP_FILE.exists() or SITEMAP_FILE.read_text() != sitemap:
        SITEMAP_FILE.write_text(sitemap)
        print("updated sitemap.xml")
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
