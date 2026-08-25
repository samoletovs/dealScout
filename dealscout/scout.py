"""Scout — gather candidate products for a Hunt from every configured source.

A hunt declares *what* it wants; the scout decides *where* to look. Two sources ship
today and both are optional, so a hunt works with either or both:

* ``watch:`` — listing or product pages parsed via schema.org ld+json. Best for a
  retailer you already buy from (their "sort by discount" page is a free deal feed).
* ``queries:`` — Google Shopping via SerpApi, which finds retailers you'd never think
  to check. Dormant unless ``SERPAPI_KEY`` is set.

Adding a source (an affiliate feed, a retailer API, an LLM-driven agent) means adding
a function here — the judge and the monitor do not change.
"""

from __future__ import annotations

import asyncio
import logging
import os

from .collector import collect_listing
from .models import Hunt, Product
from .serpsearch import _allowed_source, _condition, _old_price, _search

logger = logging.getLogger(__name__)


def _match_brand(title: str, brands: tuple[str, ...]) -> str:
    """First hunt brand named in the title ('' when none is)."""
    low = title.lower()
    return next((b for b in brands if str(b).strip().lower() in low), "")


def shopping_products(
    results: list[dict], hunt: Hunt
) -> list[Product]:
    """Map SerpApi ``google_shopping`` results into candidate Products (pure).

    Shopping never states which sizes are in stock, so ``sizes_known`` stays False and
    the judge flags size as unverified rather than guessing.
    """
    products: list[Product] = []
    for item in results:
        try:
            price = float(item["extracted_price"])
        except (KeyError, TypeError, ValueError):
            continue
        title = str(item.get("title", ""))
        products.append(
            Product(
                title=title,
                category=hunt.category,
                price=price,
                reference_price=_old_price(item),
                currency=hunt.currency,
                url=item.get("product_link") or item.get("link") or "",
                brand=_match_brand(title, hunt.brands),
                source=str(item.get("source") or "").strip(),
                condition=_condition(item, title),
            )
        )
    return products


async def _from_queries(hunt: Hunt, config: dict, api_key: str | None) -> list[Product]:
    sconf = config.get("serpapi") or {}
    if not hunt.queries:
        return []
    if not api_key:
        logger.info("hunt %s: %d query(ies) skipped — no SERPAPI_KEY", hunt.id, len(hunt.queries))
        return []

    gl = str(sconf.get("country") or "de").lower()
    limit = int(sconf.get("max_results") or 20)
    block = list(sconf.get("exclude_sources") or []) + list(hunt.exclude_sources)
    allow = list(sconf.get("preferred_stores") or [])

    found: list[Product] = []
    for query in hunt.queries:
        results = (await _search(query, api_key, gl))[:limit]
        products = shopping_products(results[:limit], hunt)
        found.extend(p for p in products if _allowed_source(p.source, allow, block))
    logger.info("hunt %s: %d candidate(s) from %d query(ies)", hunt.id, len(found), len(hunt.queries))
    return found


async def _from_watch(hunt: Hunt, delay: float) -> list[Product]:
    found: list[Product] = []
    for url in hunt.watch:
        found.extend(await collect_listing(url, hunt.category, delay=delay))
    if hunt.watch:
        logger.info("hunt %s: %d candidate(s) from %d page(s)", hunt.id, len(found), len(hunt.watch))
    return found


async def scout(hunt: Hunt, config: dict, api_key: str | None = None) -> list[Product]:
    """Gather every candidate product for a hunt, de-duplicated by URL."""
    api_key = api_key or os.getenv("SERPAPI_KEY")
    delay = float((config.get("scrape") or {}).get("delay_seconds", 1.0))

    watched, searched = await asyncio.gather(
        _from_watch(hunt, delay),
        _from_queries(hunt, config, api_key),
    )

    seen: set[str] = set()
    unique: list[Product] = []
    for product in [*watched, *searched]:
        if not product.url or product.url in seen:
            continue
        seen.add(product.url)
        unique.append(product)
    logger.info("hunt %s: %d unique candidate(s)", hunt.id, len(unique))
    return unique
