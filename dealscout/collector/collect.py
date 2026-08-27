"""Collection orchestration — the entry points that tie the pure parsers to the network.

A watch item becomes a ``Product`` here: fetch the page (honouring robots.txt), then hand
the bytes to whichever parser reads that shop. Everything impure — the fetch, the polite
delay, the fallback ordering between readers — lives in this module; the readers it calls
are pure and tested without a network stub.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from ..models import Product, WatchItem
from ..variants import extract_size_stock
from .htmlproduct import parse_html_product
from .ldjson import parse_ldjson_links, parse_ldjson_product, parse_ldjson_products
from .links import parse_html_links
from .shopify import parse_shopify_products
from .tiles import parse_product_tiles

logger = logging.getLogger(__name__)


# ``fetch`` and ``robots_allows`` are resolved through the package namespace, not imported
# by value, so that a test which does ``monkeypatch.setattr("dealscout.collector.fetch",
# ...)`` — the way the whole test suite has always stubbed the network — reaches the call
# these orchestrators actually make. Binding the names here at import time would freeze the
# original functions and silently ignore the patch, so a "no network" test would hit the
# live network instead. This indirection preserves the exact seam the single-file module
# offered when ``fetch`` was one of its own globals.
def fetch(*args, **kwargs):
    from . import fetch as _fetch

    return _fetch(*args, **kwargs)


def robots_allows(*args, **kwargs):
    from . import robots_allows as _robots_allows

    return _robots_allows(*args, **kwargs)


async def collect(item: WatchItem, title_hint: str = "") -> Product | None:
    """Fetch a watch item and parse it into a Product (None if unreadable/disallowed).

    ``title_hint`` is the name the listing gave this link. Some retailers put the brand
    in the listing tile but leave it out of the product page's own title — Sports Direct
    renders "Ultra 5 Ultimate ... Juniors" on a page reached from a tile reading "Puma
    Ultra 5 Ultimate ... Juniors". The hint is used only when it strictly contains the
    page's own title, i.e. when it is the same name with more of it.
    """
    if not await robots_allows(item.url):
        return None
    html = await fetch(item.url)
    if html is None:
        return None
    product = parse_ldjson_product(html, item.url, item.category)
    if product is None:
        product = parse_html_product(html, item.url, item.category)
    if product is None:
        logger.info("no product found at %s", item.url)
        return None

    hint = title_hint.strip()
    if hint and product.title.strip() and hint != product.title.strip():
        if product.title.strip().lower() in hint.lower():
            product = replace(product, title=hint)
    return product


def read_listing(html: str, url: str, category: str) -> list[Product]:
    """Every Product a listing page declares (pure).

    A Shopify collection endpoint hands over structured data directly; no parsing of
    rendered markup, and one request covers a whole collection. Rendered tiles are the
    last resort, for a storefront that publishes no structured data anywhere.
    """
    return (
        parse_shopify_products(html, url, category)
        or parse_ldjson_products(html, url, category)
        or parse_product_tiles(html, url, category)
    )


def read_links(html: str, url: str) -> list[tuple[str, str]]:
    """Every ``(name, URL)`` a listing page links to (pure)."""
    return parse_ldjson_links(html, url) or parse_html_links(html, url)


async def collect_page(
    url: str, category: str, delay: float = 1.0
) -> tuple[list[Product], list[tuple[str, str]]]:
    """Fetch a listing page **once** and read it both ways.

    Callers that fall back from products to links previously called ``collect_listing``
    and then ``collect_links``, each of which fetches — so every page that declared no
    products was downloaded twice, once per run, per watch URL. That is wasted time and,
    more importantly, twice the load on a retailer we are a guest of.

    Links are only parsed when there are no products, since that is the only case a
    caller needs them for and the parse is not free on a megabyte of markup.
    """
    if not await robots_allows(url):
        return [], []
    html = await fetch(url)
    if html is None:
        return [], []

    products = read_listing(html, url, category)
    links: list[tuple[str, str]] = [] if products else read_links(html, url)
    if products:
        logger.info("collected %d product(s) from %s", len(products), url)
    elif links:
        logger.info("collected %d link(s) from %s", len(links), url)
    else:
        logger.info("no product found at %s", url)
    if delay > 0:
        await asyncio.sleep(delay)
    return products, links


async def collect_listing(url: str, category: str, delay: float = 1.0) -> list[Product]:
    """Fetch one listing (or product) page and return every Product it declares.

    Skips the page when robots.txt disallows it, and sleeps ``delay`` seconds after the
    request. Scrape only your own watch-list pages, politely — see AGENTS.md.
    """
    if not await robots_allows(url):
        return []
    html = await fetch(url)
    if html is None:
        return []
    products = read_listing(html, url, category)
    if not products:
        logger.info("no product found at %s", url)
    else:
        logger.info("collected %d product(s) from %s", len(products), url)
    if delay > 0:
        await asyncio.sleep(delay)
    return products


async def collect_links(url: str, delay: float = 1.0) -> list[tuple[str, str]]:
    """Fetch a listing page and return the ``(name, URL)`` pairs it links to.

    Prefers a schema.org ItemList; falls back to product-shaped anchors for retailers
    that publish no structured data on a listing at all.
    """
    if not await robots_allows(url):
        return []
    html = await fetch(url)
    if delay > 0:
        await asyncio.sleep(delay)
    if html is None:
        return []
    links = parse_ldjson_links(html, url) or parse_html_links(html, url)
    logger.info("collected %d link(s) from %s", len(links), url)
    return links


async def enrich(product: Product, delay: float = 1.0) -> Product:
    """Re-read a product's own page for the per-size stock and RRP a listing omits.

    A listing page almost never says which sizes are left, and that is the single fact
    that decides the purchase. This only ever *adds* knowledge: anything the page does
    not state is left exactly as it was, so enrichment can never turn a known value into
    a guess.
    """
    if not product.url or not await robots_allows(product.url):
        return product
    html = await fetch(product.url)
    if delay > 0:
        await asyncio.sleep(delay)
    if html is None:
        return product

    stock = extract_size_stock(html)
    updates: dict[str, Any] = {}
    if stock.known and not product.sizes_known:
        updates["sizes"] = stock.sizes
        updates["sizes_known"] = True
    if stock.reference_price and not product.reference_price:
        updates["reference_price"] = stock.reference_price
    if not updates:
        logger.info("no per-size stock found at %s", product.url)
        return product
    logger.info(
        "enriched %s: %d size(s) in stock, RRP %s",
        product.url,
        len(updates.get("sizes", product.sizes)),
        updates.get("reference_price", product.reference_price),
    )
    return replace(product, **updates)


async def enrich_all(products: list[Product], delay: float = 1.0) -> list[Product]:
    """Enrich products one at a time — deliberately serial, to stay a polite visitor."""
    return [await enrich(product, delay) for product in products]
