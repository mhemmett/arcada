#!/usr/bin/env python3
"""
Pulls parameter metadata from the OOI M2M API for each OOI instrument in
catalog/instruments.json and writes catalog/m2m-metadata.json.

Some M2M endpoints are public; set OOI_USERNAME / OOI_TOKEN env vars for
authenticated access if needed.

Run: python3 scripts/fetch-m2m-metadata.py
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
CATALOG = json.loads((ROOT / "catalog" / "instruments.json").read_text())
OUT = ROOT / "catalog" / "m2m-metadata.json"

BASE = "https://ooinet.oceanobservatories.org/api/m2m/12576/sensor/inv"

USERNAME = os.environ.get("OOI_USERNAME", "")
TOKEN    = os.environ.get("OOI_TOKEN", "")

if not USERNAME or not TOKEN:
    print("ERROR: set OOI_USERNAME and OOI_TOKEN environment variables", file=sys.stderr)
    print("  export OOI_USERNAME=your@email.com", file=sys.stderr)
    print("  export OOI_TOKEN=your-api-token", file=sys.stderr)
    sys.exit(1)

AUTH = (USERNAME, TOKEN)


def fetch(url: str) -> dict | None:
    try:
        r = requests.get(url, auth=AUTH, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        print(f"  HTTP {e.response.status_code}: {url}")
        return None
    except Exception as e:
        print(f"  ERROR: {url} → {e}")
        return None


def clean_params(raw) -> list:
    """Extract readable parameter summaries from the M2M metadata blob."""
    params = []
    for p in raw.get("parameters", []):
        entry = {
            "pid":         p.get("pid"),
            "name":        p.get("particleKey") or p.get("name", ""),
            "display_name": p.get("displayName") or p.get("name", ""),
            "units":       p.get("units", ""),
            "description": p.get("description", ""),
        }
        if entry["name"]:
            params.append(entry)
    return params


def main():
    results = {}

    for inst in CATALOG["instruments"]:
        if inst.get("source") != "ooi_api":
            continue

        site   = inst["site"]
        node   = inst["node"]
        sensor = inst["instrument"]
        iid    = inst["id"]

        print(f"Fetching {iid}...")

        url  = f"{BASE}/{site}/{node}/{sensor}/metadata"
        data = fetch(url)
        if not data:
            print(f"  Skipping {iid} — no metadata returned")
            continue

        params = clean_params(data)
        results[iid] = {
            "instrument_id": iid,
            "name":          inst["name"],
            "location":      inst.get("location", ""),
            "parameters":    params,
            "streams":       data.get("streams", []),
        }
        print(f"  {len(params)} parameters")
        time.sleep(0.5)

    OUT.write_text(json.dumps({"version": "1.0", "instruments": results}, indent=2))
    print(f"\nWrote M2M metadata for {len(results)} instruments → {OUT}")


if __name__ == "__main__":
    main()
