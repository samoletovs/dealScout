"""Scan Google Shopping (via SerpApi) for on-sale items from favourite brands.

A dormant, opt-in deal source: active only when ``SERPAPI_KEY`` is set AND the config
has ``serpapi.enabled: true``. Google Shopping gives price, an old_price and an on-sale
flag, but NOT fabric composition — so these are *candidates*: the judge is run with the
fibre gate off and the human verifies fabric/logo on click (co-pilot, not autopilot).

The HTTP call is isolated in ``_search``; ``build_products`` and ``_match_brand`` are
pure and unit-tested.
"""

from __future__ import annotations

import logging
import os

import aiohttp

from .models import Product

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"


def _match_brand(title: str, brands: dict) -> str:
    """Best-effort brand from a product title, matched against the configured tiers."""
    low = title.lower()
    for tier in ("better", "basket", "local", "worse"):
        for name in brands.get(tier, []):
            if str(name).lower() in low:
                return str(name)
    return ""


def _old_price(item: dict) -> float | None:
    old = item.get("extracted_old_price")
    try:
        return float(old) if old else None
    except (TypeError, ValueError):
        return None


def build_products(
    results: list[dict], category: str, currency: str, brands: dict
) -> list[Product]:
    """Map SerpApi ``google_shopping`` results into candidate Products (pure).

    Items without a usable price are skipped. Fabric is unknown from Shopping, so
    ``materials`` is left empty and verified on click.
    """
    products: list[Product] = []
    for item in results:
        price = item.get("extracted_price")
        if not price:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        title = item.get("title", "")
        products.append(
            Product(
                title=title,
                category=category,
                price=price,
                reference_price=_old_price(item),
                currency=currency,
                url=item.get("product_link") or item.get("link") or "",
                materials={},  # unknown from Shopping — fabric verified on click
                brand=_match_brand(title, brands),
            )
        )
    return products


async def _search(query: str, api_key: str, gl: str) -> list[dict]:
    """Call SerpApi's google_shopping engine for on-sale items. Never raises."""
    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": api_key,
        "gl": gl,
        "on_sale": "true",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                SERPAPI_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status >= 400:
                    logger.error("SerpApi search failed for %r: HTTP %s", query, resp.status)
                    return []
                data = await resp.json()
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        logger.error("SerpApi search failed for %r: %s", query, exc)
        return []
    return data.get("shopping_results", []) or []


async def scan(config: dict, api_key: str | None = None) -> list[Product]:
    """Run the configured Google Shopping queries and return candidate Products.

    Returns [] (a no-op) unless ``SERPAPI_KEY`` is available and ``serpapi.enabled``
    is true, so the whole feature stays dormant until deliberately switched on.
    """
    api_key = api_key or os.getenv("SERPAPI_KEY")
    sconf = config.get("serpapi") or {}
    if not api_key or not sconf.get("enabled"):
        logger.info("SerpApi scan skipped (no SERPAPI_KEY or serpapi.enabled is false)")
        return []

    gl = str(sconf.get("country") or config.get("deliver_to") or "lv").lower()
    limit = int(sconf.get("max_results") or 20)
    require_brand = bool(sconf.get("require_known_brand", True))
    currency = config.get("currency", "EUR")
    brands = config.get("brands", {})
    queries = sconf.get("queries") or []

    out: list[Product] = []
    for entry in queries:
        query = entry.get("q")
        if not query:
            continue
        results = (await _search(query, api_key, gl))[:limit]
        products = build_products(results, entry.get("category", ""), currency, brands)
        if require_brand:
            products = [p for p in products if p.brand]
        out.extend(products)

    logger.info(
        "SerpApi scan: %d candidate product(s) from %d query(ies)", len(out), len(queries)
    )
    return out
