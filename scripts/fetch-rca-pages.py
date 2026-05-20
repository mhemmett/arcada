#!/usr/bin/env python3
"""
Scrapes OOI Regional Cabled Array site pages for science context and
writes catalog/rca-context.json.

Pages scraped:
  - /regional-cabled-array/          (overview)
  - /array/cabled-axial-seamount-array/
  - /array/cabled-continental-margin-array/

Run: python3 scripts/fetch-rca-pages.py
"""

import json
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent.parent / "catalog" / "rca-context.json"

BASE = "https://oceanobservatories.org"

PAGES = [
    {
        "id":       "rca-overview",
        "url":      f"{BASE}/regional-cabled-array/",
        "title":    "OOI Regional Cabled Array Overview",
        "location": "Northeast Pacific Ocean",
        "instruments": [],  # applies to all instruments
    },
    {
        "id":       "axial-seamount-array",
        "url":      f"{BASE}/array/cabled-axial-seamount-array/",
        "title":    "Cabled Axial Seamount Array",
        "location": "Axial Seamount, Juan de Fuca Ridge",
        "instruments": [
            "EARTHSCOPE-OO-AXCC1", "EARTHSCOPE-OO-AXEC2", "EARTHSCOPE-OO-AXID1",
            "RS03AXBS-LJ03A-12-HYDLFA301", "RS03AXBS-LJ03A-14-BOTPTA301",
            "RS03AXPS-PC03A-4B-CTDPFK301", "RS03ASHS-MJ03B-15-OBSSPA301",
            "RS03ASHS-MJ03B-10-THSPHD000", "PI-MASSP-ASHES",
        ],
    },
    {
        "id":       "continental-margin-array",
        "url":      f"{BASE}/array/cabled-continental-margin-array/",
        "title":    "Cabled Continental Margin Array",
        "location": "Southern Hydrate Ridge, Oregon Margin",
        "instruments": [
            "RS01SUM2-MJ01B-12-HYDMGA000", "RS01SUM2-MJ01B-14-BOTPTA301",
            "RS01SUM2-MJ01B-15-OBSBBA102", "EARTHSCOPE-OO-HYS14",
            "RS01SUM1-LJ01B-10-PCO2WA101", "RS01SUM2-MJ01B-09-THSPHD000",
            "PI-OVRSRA101",
        ],
    },
]

# Tags we strip from raw HTML
_TAG_RE  = re.compile(r"<[^>]+>")
_WS_RE   = re.compile(r"\s{2,}")
_AMP_RE  = re.compile(r"&amp;|&#\d+;|&[a-z]+;")


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 aRCADA-indexer"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", errors="replace")

    # Extract <main> or <article> content to skip nav/footer noise
    main_match = re.search(r"<main[^>]*>(.*?)</main>", html, re.S | re.I)
    body = main_match.group(1) if main_match else html

    # Strip scripts and style blocks
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", body, flags=re.S | re.I)

    text = _TAG_RE.sub(" ", body)
    text = _AMP_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def extract_paragraphs(text: str, min_len: int = 80) -> list:
    """Return sentences / short paragraphs that are informative."""
    # Split on double-space runs (already collapsed) or period-then-space at sentence end
    parts = re.split(r"(?<=[.!?])\s+", text)
    paras = []
    buf = ""
    for part in parts:
        buf = (buf + " " + part).strip()
        if len(buf) >= min_len:
            paras.append(buf)
            buf = ""
    if buf and len(buf) >= min_len:
        paras.append(buf)
    return paras[:60]  # cap at 60 excerpts per page


def main():
    contexts = []
    for page in PAGES:
        print(f"Fetching {page['url']}...")
        try:
            text = fetch_text(page["url"])
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        paras = extract_paragraphs(text)
        # Build a single coherent description block (first 3000 chars of clean text)
        description = " ".join(paras)[:3000].strip()

        contexts.append({
            "id":          page["id"],
            "title":       page["title"],
            "url":         page["url"],
            "location":    page["location"],
            "instruments": page["instruments"],
            "description": description,
            "paragraphs":  paras,
        })
        print(f"  {len(paras)} paragraphs, {len(description)} chars")

    out = {"version": "1.0", "source": "OOI Regional Cabled Array website", "pages": contexts}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(contexts)} pages → {OUT}")


if __name__ == "__main__":
    main()
