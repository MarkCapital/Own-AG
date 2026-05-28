#!/usr/bin/env python3
"""Update OwnAG generated price snapshot.

This script is designed for GitHub Actions and local use. It fetches live spot
metal prices from server-side sources, attempts safe dealer product lookups for
specific product URLs, falls back to manually curated premiums when lookups fail,
and writes data/prices.json for the static frontend to consume.
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PRICE_FILE = ROOT / "data" / "prices.json"

# V1 tracks exact product pages. Dealer sites change often, so every lookup is
# best-effort and manual-premium fallback remains the safe default.
DEFAULT_PREMIUMS: dict[str, dict[str, Any]] = {
    "jm": {
        "name": "JM Bullion",
        "eaglePremium": 16.74,
        "barPremium": 11.62,
        "url": "https://www.jmbullion.com",
        "products": {
            "eagle": {
                "name": "1 oz American Silver Eagle",
                "url": "https://www.jmbullion.com/2026-1-oz-american-silver-eagle-coin/",
            },
            "bar": {
                "name": "1 oz SilverTowne Silver Bar",
                "url": "https://www.jmbullion.com/1-oz-silvertowne-silver-bar/",
            },
        },
    },
    "mm": {
        "name": "Money Metals",
        "eaglePremium": 11.73,
        "barPremium": 7.63,
        "url": "https://www.awin1.com/cread.php?awinmid=88985&awinaffid=2837494",
        "products": {
            "eagle": {
                "name": "1 oz American Silver Eagle",
                "url": "https://www.moneymetals.com/american-silver-eagle/2",
            },
            "bar": {
                "name": "1 oz Silver Bar",
                "url": "https://www.moneymetals.com/silver-bars/1-oz-silver-bars",
            },
        },
    },
    "kitco": {
        "name": "Kitco",
        "eaglePremium": 7.91,
        "barPremium": 10.73,
        "url": "https://www.awin1.com/cread.php?awinmid=84579&awinaffid=2837494",
        "products": {},
    },
    "bgasc": {
        "name": "BGASC",
        "eaglePremium": 14.31,
        "barPremium": 10.02,
        "url": "https://www.bgasc.com",
        "products": {},
    },
}

USER_AGENT = (
    "Mozilla/5.0 (compatible; OwnAG price updater/1.1; "
    "+https://ownag.com; dealer lookup bot)"
)


def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    req = Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_text(url: str, headers: dict[str, str] | None = None, timeout: int = 25) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            **(headers or {}),
        },
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


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
        # Preserve manually curated premiums, but do not turn a previously
        # scraped live premium into the new fallback. If a lookup was bad or
        # later fails, fall back to the known manual baseline.
        existing_eagle = as_price(existing.get("eaglePremium")) if existing.get("eagleSource", "manual-premium") == "manual-premium" else None
        existing_bar = as_price(existing.get("barPremium")) if existing.get("barSource", "manual-premium") == "manual-premium" else None
        eagle_fallback = as_price(existing.get("eagleFallbackPremium"))
        bar_fallback = as_price(existing.get("barFallbackPremium"))
        merged[key] = {
            **default,
            "eaglePremium": existing_eagle if existing_eagle and existing_eagle >= 1 else default["eaglePremium"],
            "barPremium": existing_bar if existing_bar and existing_bar >= 1 else default["barPremium"],
            "eagleFallbackPremium": eagle_fallback,
            "eagleFallbackUpdatedAt": existing.get("eagleFallbackUpdatedAt") if eagle_fallback else None,
            "barFallbackPremium": bar_fallback,
            "barFallbackUpdatedAt": existing.get("barFallbackUpdatedAt") if bar_fallback else None,
            "products": default.get("products", {}),
        }
    return merged


def _walk_json_prices(value: Any) -> list[float]:
    prices: list[float] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"price", "lowprice", "highprice", "saleprice"}:
                price = as_price(str(child).replace(",", "") if child is not None else None)
                if price:
                    prices.append(price)
            prices.extend(_walk_json_prices(child))
    elif isinstance(value, list):
        for child in value:
            prices.extend(_walk_json_prices(child))
    return prices


def _json_ld_prices(page_html: str) -> list[float]:
    prices: list[float] = []
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page_html,
        flags=re.I | re.S,
    )
    for script in scripts:
        text = html_lib.unescape(script).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        prices.extend(_walk_json_prices(data))
    return prices


def _visible_price_candidates(page_html: str) -> list[float]:
    cleaned = page_html.replace("<!-- -->", "")
    cleaned = re.sub(r"<script\b.*?</script>", " ", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"<style\b.*?</style>", " ", cleaned, flags=re.I | re.S)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", cleaned))
    text = re.sub(r"\s+", " ", text)

    candidates: list[float] = []
    contextual_patterns = [
        r"(?:as\s+low\s+as|our\s+price|price\s+as\s+low\s+as|cash\s+price|check\s*/?\s*wire)\D{0,80}\$\s*([0-9][0-9,]*(?:\.[0-9]{2})?)",
        r"\$\s*([0-9][0-9,]*(?:\.[0-9]{2})?)\D{0,80}(?:as\s+low\s+as|each|per\s+coin|per\s+bar)",
    ]
    for pattern in contextual_patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            price = as_price(match.group(1).replace(",", ""))
            if price:
                candidates.append(price)

    # Last-resort fallback for dealer pages that render product data in escaped
    # Next.js/React payloads instead of clean HTML. Later validation filters out
    # spot quotes and absurd values.
    for match in re.finditer(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{2})?)", text):
        price = as_price(match.group(1).replace(",", ""))
        if price:
            candidates.append(price)

    return candidates


def extract_product_price(page_html: str, expected_spot: float, min_premium: float = 1.00) -> float | None:
    """Extract a likely 1 oz product price from a dealer product page.

    The chosen price must be above spot by at least ``min_premium`` and not
    absurdly high. This intentionally rejects spot quotes from site headers.
    """
    candidates = _json_ld_prices(page_html) + _visible_price_candidates(page_html)
    valid: list[float] = []
    for price in candidates:
        if expected_spot + min_premium <= price <= expected_spot + 100:
            valid.append(round(price, 2))
    if not valid:
        return None
    return min(valid)


def fetch_dealer_prices(
    dealers: dict[str, dict[str, Any]],
    silver_spot: float,
    fetch_html: Callable[[str], str] = http_text,
) -> tuple[dict[str, dict[str, float | None]], dict[str, str]]:
    results: dict[str, dict[str, float | None]] = {}
    errors: dict[str, str] = {}
    for dealer_key, dealer in dealers.items():
        products = dealer.get("products") or {}
        if not products:
            continue
        results[dealer_key] = {}
        for product_key, product in products.items():
            label = f"{dealer_key}.{product_key}"
            url = product.get("url")
            if not url:
                results[dealer_key][product_key] = None
                errors[label] = "missing product URL"
                continue
            try:
                page = fetch_html(url)
                price = extract_product_price(page, expected_spot=silver_spot)
                if price is None:
                    raise ValueError("no valid product price found")
                results[dealer_key][product_key] = price
            except Exception as exc:  # Keep the whole snapshot job alive.
                results[dealer_key][product_key] = None
                errors[label] = str(exc)
    return results, errors


def build_snapshot(
    spot: dict[str, Any],
    dealers: dict[str, dict[str, Any]],
    dealer_prices: dict[str, dict[str, float | None]] | None = None,
    dealer_errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    silver = float(spot["silver"])
    snapshot_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_dealers: dict[str, dict[str, Any]] = {}
    dealer_prices = dealer_prices or {}
    dealer_errors = dealer_errors or {}

    for key, d in dealers.items():
        manual_eagle_premium = float(d["eaglePremium"])
        manual_bar_premium = float(d["barPremium"])
        live = dealer_prices.get(key, {})
        eagle_live = as_price(live.get("eagle"))
        bar_live = as_price(live.get("bar"))

        prior_eagle_fallback = as_price(d.get("eagleFallbackPremium"))
        prior_bar_fallback = as_price(d.get("barFallbackPremium"))
        eagle_fallback_updated_at = d.get("eagleFallbackUpdatedAt") if prior_eagle_fallback else None
        bar_fallback_updated_at = d.get("barFallbackUpdatedAt") if prior_bar_fallback else None

        if eagle_live:
            eagle_price = eagle_live
            eagle_premium = round(eagle_price - silver, 2)
            eagle_fallback_premium = eagle_premium
            eagle_fallback_updated_at = snapshot_time
            eagle_source = "dealer-live"
        elif prior_eagle_fallback:
            eagle_premium = round(prior_eagle_fallback, 2)
            eagle_fallback_premium = eagle_premium
            eagle_price = round(silver + eagle_premium, 2)
            eagle_source = "last-known-premium"
        else:
            eagle_premium = round(manual_eagle_premium, 2)
            eagle_fallback_premium = eagle_premium
            eagle_price = round(silver + eagle_premium, 2)
            eagle_source = "manual-premium"

        if bar_live:
            bar_price = bar_live
            bar_premium = round(bar_price - silver, 2)
            bar_fallback_premium = bar_premium
            bar_fallback_updated_at = snapshot_time
            bar_source = "dealer-live"
        elif prior_bar_fallback:
            bar_premium = round(prior_bar_fallback, 2)
            bar_fallback_premium = bar_premium
            bar_price = round(silver + bar_premium, 2)
            bar_source = "last-known-premium"
        else:
            bar_premium = round(manual_bar_premium, 2)
            bar_fallback_premium = bar_premium
            bar_price = round(silver + bar_premium, 2)
            bar_source = "manual-premium"

        products = d.get("products") or {}
        out_dealers[key] = {
            "name": d["name"],
            "eagle": round(eagle_price, 2),
            "eaglePremium": eagle_premium,
            "eagleSource": eagle_source,
            "eagleFallbackPremium": eagle_fallback_premium,
            "eagleFallbackUpdatedAt": eagle_fallback_updated_at,
            "bar": round(bar_price, 2),
            "barPremium": bar_premium,
            "barSource": bar_source,
            "barFallbackPremium": bar_fallback_premium,
            "barFallbackUpdatedAt": bar_fallback_updated_at,
            "url": d.get("url"),
            "products": products,
            "source": "dealer-live" if eagle_live or bar_live else ("last-known-premium" if prior_eagle_fallback or prior_bar_fallback else "manual-premium"),
        }

    lookup_status = {
        "updatedAt": snapshot_time,
        "source": "dealer product pages + last-known premium fallback",
        "errors": dealer_errors,
    }

    return {
        "updatedAt": snapshot_time,
        "spot": {
            "silver": round(silver, 4),
            "gold": round(float(spot["gold"]), 4) if spot.get("gold") else None,
            "platinum": round(float(spot["platinum"]), 4) if spot.get("platinum") else None,
            "palladium": round(float(spot["palladium"]), 4) if spot.get("palladium") else None,
            "source": spot["source"],
        },
        "dealers": out_dealers,
        "dealerLookup": lookup_status,
        "notes": [
            "Spot prices are refreshed automatically by GitHub Actions.",
            "Dealer product pages are checked automatically when possible.",
            "Live dealer prices refresh the saved fallback premiums automatically.",
            "If a dealer lookup fails, OwnAG safely falls back to the last known good premium or the manual baseline.",
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
    dealer_prices, dealer_errors = fetch_dealer_prices(dealers, float(spot["silver"]))
    snapshot = build_snapshot(spot, dealers, dealer_prices=dealer_prices, dealer_errors=dealer_errors)
    PRICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRICE_FILE.write_text(json.dumps(snapshot, indent=2, sort_keys=False) + "\n")
    dealer_live_count = sum(
        1
        for dealer in snapshot["dealers"].values()
        for source_key in ("eagleSource", "barSource")
        if dealer.get(source_key) == "dealer-live"
    )
    print(
        f"updated {PRICE_FILE.relative_to(ROOT)} from {snapshot['spot']['source']} "
        f"with {dealer_live_count} live dealer product prices at {snapshot['updatedAt']}"
    )
    if dealer_errors:
        for label, message in dealer_errors.items():
            print(f"warning: dealer lookup failed for {label}: {message}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
