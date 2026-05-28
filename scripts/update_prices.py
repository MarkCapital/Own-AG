#!/usr/bin/env python3
"""Update OwnAG generated price snapshot.

This script is designed for GitHub Actions and local use. It fetches live spot
metal prices from server-side sources, preserves the current manually curated
dealer premiums, and writes data/prices.json for the static frontend to consume.
"""
from __future__ import annotations

import csv
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PRICE_FILE = ROOT / "data" / "prices.json"

DEFAULT_PREMIUMS: dict[str, dict[str, Any]] = {
    "jm": {
        "name": "JM Bullion",
        "eaglePremium": 16.74,
        "barPremium": 11.62,
        "url": "https://www.jmbullion.com",
    },
    "mm": {
        "name": "Money Metals",
        "eaglePremium": 11.73,
        "barPremium": 7.63,
        "url": "https://www.awin1.com/cread.php?awinmid=88985&awinaffid=2837494",
    },
    "kitco": {
        "name": "Kitco",
        "eaglePremium": 7.91,
        "barPremium": 10.73,
        "url": "https://www.awin1.com/cread.php?awinmid=84579&awinaffid=2837494",
    },
    "bgasc": {
        "name": "BGASC",
        "eaglePremium": 14.31,
        "barPremium": 10.02,
        "url": "https://www.bgasc.com",
    },
}


def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    req = Request(url, headers={"User-Agent": "OwnAG price updater/1.0", **(headers or {})})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_metals_dev() -> dict[str, Any] | None:
    key = os.getenv("METALS_DEV_API_KEY", "").strip()
    if not key:
        return None
    url = "https://api.metals.dev/v1/latest?" + urlencode(
        {"api_key": key, "currency": "USD", "unit": "toz"}
    )
    data = http_json(url)
    metals = data.get("metals") or {}
    silver = float(metals.get("silver") or 0)
    if not 5 < silver < 500:
        raise ValueError("metals.dev returned invalid silver price")
    return {
        "source": "metals.dev",
        "silver": silver,
        "gold": as_price(metals.get("gold")),
        "platinum": as_price(metals.get("platinum")),
        "palladium": as_price(metals.get("palladium")),
    }


def fetch_swissquote() -> dict[str, Any] | None:
    data = http_json("https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAG/USD")
    price = data[0]["spreadProfilePrices"][0].get("ask")
    silver = float(price)
    if not 5 < silver < 500:
        raise ValueError("Swissquote returned invalid silver price")
    return {
        "source": "Swissquote XAG/USD",
        "silver": silver,
        "gold": None,
        "platinum": None,
        "palladium": None,
    }


def fetch_goldapi_symbol(symbol: str) -> float | None:
    key = os.getenv("GOLDAPI_KEY", "").strip()
    if not key:
        return None
    data = http_json(f"https://www.goldapi.io/api/{symbol}/USD", headers={"x-access-token": key})
    price = as_price(data.get("price"))
    return price


def enrich_with_goldapi(spot: dict[str, Any]) -> dict[str, Any]:
    # Optional enrichment if a GitHub Actions secret is configured.
    if spot.get("gold") is None:
        spot["gold"] = fetch_goldapi_symbol("XAU")
    if spot.get("platinum") is None:
        spot["platinum"] = fetch_goldapi_symbol("XPT")
    if spot.get("palladium") is None:
        spot["palladium"] = fetch_goldapi_symbol("XPD")
    return spot


def as_price(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def load_existing_dealers() -> dict[str, dict[str, Any]]:
    if not PRICE_FILE.exists():
        return DEFAULT_PREMIUMS
    try:
        current = json.loads(PRICE_FILE.read_text())
        dealers = current.get("dealers") or {}
    except Exception:
        return DEFAULT_PREMIUMS

    merged: dict[str, dict[str, Any]] = {}
    for key, default in DEFAULT_PREMIUMS.items():
        existing = dealers.get(key) or {}
        merged[key] = {
            **default,
            "eaglePremium": as_price(existing.get("eaglePremium")) or default["eaglePremium"],
            "barPremium": as_price(existing.get("barPremium")) or default["barPremium"],
        }
    return merged


def build_snapshot(spot: dict[str, Any], dealers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    silver = float(spot["silver"])
    out_dealers: dict[str, dict[str, Any]] = {}
    for key, d in dealers.items():
        eagle_premium = float(d["eaglePremium"])
        bar_premium = float(d["barPremium"])
        out_dealers[key] = {
            "name": d["name"],
            "eagle": round(silver + eagle_premium, 2),
            "eaglePremium": round(eagle_premium, 2),
            "bar": round(silver + bar_premium, 2),
            "barPremium": round(bar_premium, 2),
            "url": d.get("url"),
            "source": "manual-premium",
        }

    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spot": {
            "silver": round(silver, 4),
            "gold": round(float(spot["gold"]), 4) if spot.get("gold") else None,
            "platinum": round(float(spot["platinum"]), 4) if spot.get("platinum") else None,
            "palladium": round(float(spot["palladium"]), 4) if spot.get("palladium") else None,
            "source": spot["source"],
        },
        "dealers": out_dealers,
        "notes": [
            "Spot prices are refreshed automatically by GitHub Actions.",
            "Dealer premiums are currently manually curated and applied above live silver spot.",
        ],
    }


def fetch_spot() -> dict[str, Any]:
    errors: list[str] = []
    for name, fn in (("metals.dev", fetch_metals_dev), ("Swissquote", fetch_swissquote)):
        try:
            spot = fn()
            if spot:
                try:
                    return enrich_with_goldapi(spot)
                except Exception as enrich_error:
                    print(f"warning: optional GoldAPI enrichment failed: {enrich_error}", file=sys.stderr)
                    return spot
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            errors.append(f"{name}: {exc}")

    raise RuntimeError("all spot sources failed: " + "; ".join(errors))


def main() -> int:
    spot = fetch_spot()
    dealers = load_existing_dealers()
    snapshot = build_snapshot(spot, dealers)
    PRICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRICE_FILE.write_text(json.dumps(snapshot, indent=2, sort_keys=False) + "\n")
    print(f"updated {PRICE_FILE.relative_to(ROOT)} from {snapshot['spot']['source']} at {snapshot['updatedAt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
