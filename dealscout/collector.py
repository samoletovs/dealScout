"""Collector — turns a watch item into a Product snapshot.

Prefers schema.org ld+json on the product page (name, price, currency, material).
Falls back to parsing a fabric-composition string ("80% cotton, 20% polyester")
from the description or page text. Scrape only your own watch-list pages, politely;
prefer affiliate feeds where available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import aiohttp
from bs4 import BeautifulSoup

from .models import Product, WatchItem
from .spec import looks_like_eu, normalise_size
from .variants import extract_size_stock

logger = logging.getLogger(__name__)

# A bare custom User-Agent with no other headers is silently *tarpitted* by the CDN bot
# protection several European retailers sit behind (Akamai especially): TCP connects,
# TLS completes, the request goes out, and no response ever arrives — so it looks like a
# network fault rather than a refusal. A complete, ordinary browser header set is what
# any HTTP client should send anyway. Politeness is enforced where it actually matters:
# one request per watch page, `scrape.delay_seconds` between them, and robots.txt honoured
# (see `robots_allows`).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Accept-Encoding is deliberately absent: aiohttp sets it to what it can actually decode.
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9,lv;q=0.8",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Matches "80% cotton", "100% virgin wool", "55% linen 45% cotton".
_COMPOSITION_RE = re.compile(
    r"(\d{1,3})\s*%\s+([a-zA-Z][a-zA-Z ]*?)(?=\s*\d{1,3}\s*%|[,;./]|$)"
)


def parse_materials(text: str) -> dict[str, float]:
    """Extract a fibre composition from text like '80% cotton, 20% polyester'."""
    materials: dict[str, float] = {}
    for pct, name in _COMPOSITION_RE.findall(text):
        fibre = name.strip().lower()
        frac = int(pct) / 100.0
        if fibre and 0 < frac <= 1:
            materials[fibre] = materials.get(fibre, 0.0) + frac
    return materials


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _is_product(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return "Product" in node_type or "ProductGroup" in node_type
    return node_type in ("Product", "ProductGroup")


def _is_group(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return "ProductGroup" in node_type
    return node_type == "ProductGroup"


def _variants(node: dict) -> list[dict]:
    raw = node.get("hasVariant")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    return []


def _variant_size(group_name: str, variant_name: str) -> str:
    """The size a ProductGroup variant is for ('… F50 Elite FG - 4.5' -> '4.5')."""
    text = str(variant_name or "").strip()
    base = str(group_name or "").strip()
    if base and text.lower().startswith(base.lower()):
        text = text[len(base) :]
    return normalise_size(text.lstrip(" -–—:,").strip())


def _flatten_offers(raw: Any) -> list[dict]:
    """Offers from one ``offers`` value (schema.org allows one, a list, or nested)."""
    if isinstance(raw, dict):
        nested = raw.get("offers")
        if isinstance(nested, list):
            return [o for o in nested if isinstance(o, dict)] or [raw]
        return [raw]
    if isinstance(raw, list):
        return [o for o in raw if isinstance(o, dict)]
    return []


def _offers(node: dict) -> list[dict]:
    """Every Offer on a node, including a ProductGroup's per-size variants.

    A variant *is* one size, so its size is copied onto its Offer where the size reader
    will find it.
    """
    collected = _flatten_offers(node.get("offers"))
    group_name = str(node.get("name") or "")
    for variant in _variants(node):
        size = _variant_size(group_name, variant.get("name", "")) or normalise_size(
            variant.get("size")
        )
        for offer in _flatten_offers(variant.get("offers")):
            entry = dict(offer)
            if size:
                entry["size"] = size
            collected.append(entry)
    return collected


def _in_stock(offer: dict) -> bool:
    """True when an Offer's availability says it can be bought now."""
    availability = offer.get("availability") or offer.get("itemCondition") or ""
    if isinstance(availability, dict):
        availability = availability.get("@id") or availability.get("name") or ""
    text = str(availability).lower()
    if not text:
        return True  # no availability stated: assume buyable rather than invent stock-outs
    return "instock" in text.replace("_", "") or "limitedavailability" in text.replace("_", "")


def _offer_sizes(offers: list[dict]) -> tuple[frozenset[str], bool]:
    """In-stock sizes from per-size Offers, plus whether sizes were stated at all.

    Retailers commonly emit one Offer per size, with the size in ``name``, ``sku`` or
    a ``size`` property. Returning ``known=False`` matters: "we don't know the sizes"
    must never be confused with "no sizes are in stock".
    """
    labels: list[str] = []
    in_stock: list[str] = []
    for offer in offers:
        raw = offer.get("size") or offer.get("name") or offer.get("sku") or ""
        size = normalise_size(raw)
        if not size:
            continue
        labels.append(size)
        if _in_stock(offer):
            in_stock.append(size)
    if not looks_like_eu(labels):
        # A UK size table, say: stated, but not in a system we can compare against a
        # hunt's EU sizes. Unknown beats a confident wrong rejection.
        return frozenset(), False
    return frozenset(in_stock), bool(labels)


def _brand(node: dict) -> str:
    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    if isinstance(brand, list):
        brand = next((b.get("name") if isinstance(b, dict) else b for b in brand), "")
    return str(brand or "").strip()


def _reference_price(node: dict, offers: list[dict], price: float) -> float | None:
    """Best available RRP: an explicit high price, a listPrice, or the dearest offer."""
    for offer in offers:
        for key in ("highPrice", "listPrice", "priceSpecification"):
            value = offer.get(key)
            if isinstance(value, dict):
                value = value.get("price")
            found = _to_float(value)
            if found and found > price:
                return found
    candidates = [p for p in (_to_float(o.get("price")) for o in offers) if p]
    top = max(candidates, default=None)
    return top if top and top > price else None


def _product_from_node(node: dict, url: str, category: str, page_text: str) -> Product | None:
    offers = _offers(node)
    first = offers[0] if offers else {}

    prices = [p for p in (_to_float(o.get("price") or o.get("lowPrice")) for o in offers) if p]
    price = min(prices) if prices else _to_float(first.get("price") or first.get("lowPrice"))
    if price is None:
        return None

    material = node.get("material")
    if isinstance(material, list):
        material = " ".join(str(m) for m in material)
    materials = parse_materials(str(material)) if material else {}
    if not materials:
        materials = parse_materials(str(node.get("description", "")) or page_text)

    sizes, sizes_known = _offer_sizes(offers)
    link = str(node.get("url") or "").strip()
    if link.startswith("/"):
        link = urljoin(url, link)

    return Product(
        title=str(node.get("name", "")).strip() or url,
        category=category,
        price=price,
        reference_price=_reference_price(node, offers, price),
        currency=str(first.get("priceCurrency", "EUR")),
        url=link or url,
        materials=materials,
        brand=_brand(node),
        source=urlsplit(url).netloc.removeprefix("www."),
        sizes=sizes,
        sizes_known=sizes_known,
    )


def _ldjson_nodes(html: str) -> tuple[list[Any], str]:
    """Every parsed ld+json blob on a page, plus the page's visible text."""
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    blobs: list[Any] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            blobs.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return blobs, page_text


def _walk_products(data: Any) -> list[dict]:
    """Collect every Product node in a blob, including inside @graph and ItemList.

    A ProductGroup's ``hasVariant`` entries are sizes of one boot, not separate boots, so
    a node that is itself a product is never descended into for more products.
    """
    found: list[dict] = []
    if isinstance(data, list):
        for item in data:
            found.extend(_walk_products(item))
    elif isinstance(data, dict):
        keys = ("@graph", "itemListElement", "item", "mainEntity", "hasVariant")
        if _is_product(data):
            found.append(data)
            keys = ("@graph", "itemListElement", "item", "mainEntity")
        for key in keys:
            if key in data:
                found.extend(_walk_products(data[key]))
    return found


def parse_ldjson_product(html: str, url: str, category: str) -> Product | None:
    """Extract a single Product from schema.org ld+json in a page (pure, testable)."""
    products = parse_ldjson_products(html, url, category)
    return products[0] if products else None


def parse_ldjson_products(html: str, url: str, category: str) -> list[Product]:
    """Extract EVERY Product from a page — works for a listing page as well as a PDP."""
    blobs, page_text = _ldjson_nodes(html)
    products: list[Product] = []
    seen: set[str] = set()
    for blob in blobs:
        for node in _walk_products(blob):
            product = _product_from_node(node, url, category, page_text)
            if product is None:
                continue
            key = f"{product.url}|{product.title}"
            if key in seen:
                continue
            seen.add(key)
            products.append(product)
    return products


def _walk_listitems(node: Any, found: list[dict], in_breadcrumb: bool = False) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_listitems(item, found, in_breadcrumb)
    elif isinstance(node, dict):
        # A BreadcrumbList is also made of ListItems, but they point at categories, not
        # products. Following them costs a request per level and yields nothing.
        breadcrumb = in_breadcrumb or node.get("@type") == "BreadcrumbList"
        if node.get("@type") == "ListItem" and not breadcrumb:
            found.append(node)
        for value in node.values():
            _walk_listitems(value, found, breadcrumb)


def parse_ldjson_links(html: str, url: str) -> list[tuple[str, str]]:
    """Every ``(name, absolute URL)`` in a page's schema.org ItemList.

    Some retailers publish only a list of links on a listing page and keep price and
    stock on the product page. Returning the names as well as the URLs lets a caller
    discard the obvious non-candidates by title *before* spending a request on each one.
    """
    blobs, _ = _ldjson_nodes(html)
    items: list[dict] = []
    for blob in blobs:
        _walk_listitems(blob, items)

    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    here = urlsplit(url).path.rstrip("/")
    for item in items:
        target = item.get("url") or item.get("item")
        if isinstance(target, dict):
            target = target.get("url") or target.get("@id")
        if not isinstance(target, str) or not target.strip():
            continue
        absolute = urljoin(url, target.strip())
        if absolute in seen:
            continue
        # A link whose path is an ancestor of this page is a breadcrumb, not a product.
        # Some retailers publish breadcrumbs as a plain ItemList, and following them costs
        # one request per level to re-read pages we are already on.
        path = urlsplit(absolute).path.rstrip("/")
        if here.startswith(path) and urlsplit(absolute).netloc == urlsplit(url).netloc:
            continue
        seen.add(absolute)
        links.append((str(item.get("name") or "").strip(), absolute))
    return links


async def fetch(url: str, timeout: float = 20.0, retries: int = 1) -> str | None:
    """GET a page's HTML, or None on error/non-200.

    Does NOT consult robots.txt — that is the caller's job (see :func:`robots_allows`),
    so that fetching robots.txt itself cannot recurse.
    """
    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession(headers=BROWSER_HEADERS) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status != 200:
                        logger.warning("fetch %s -> HTTP %s", url, resp.status)
                        return None
                    return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt >= retries:
                logger.warning("fetch %s failed: %s", url, exc or type(exc).__name__)
                return None
            # A tarpitting CDN drops the first connection far more often than the second.
            logger.info("fetch %s failed (%s) — retrying", url, type(exc).__name__)
            await asyncio.sleep(2.0 * (attempt + 1))
    return None


_ROBOTS: dict[str, RobotFileParser | None] = {}


async def robots_allows(url: str, agent: str = "*") -> bool:
    """True when the host's robots.txt permits fetching ``url``.

    Cached per host, and **fail-open**: an unreadable robots.txt means "no stated rule",
    not "forbidden" — but it is logged, so a silent assumption never passes unnoticed.
    """
    parts = urlsplit(url)
    if not parts.netloc:
        return True
    root = f"{parts.scheme}://{parts.netloc}"
    if root not in _ROBOTS:
        text = await fetch(f"{root}/robots.txt", timeout=10.0, retries=0)
        if text is None:
            logger.info("robots.txt unreadable for %s — proceeding", root)
            _ROBOTS[root] = None
        else:
            parser = RobotFileParser()
            parser.parse(text.splitlines())
            _ROBOTS[root] = parser
    parser = _ROBOTS[root]
    if parser is None:
        return True
    if not parser.can_fetch(agent, url):
        logger.warning("robots.txt disallows %s — skipping", url)
        return False
    return True


async def collect(item: WatchItem) -> Product | None:
    """Fetch a watch item and parse it into a Product (None if unreadable/disallowed)."""
    if not await robots_allows(item.url):
        return None
    html = await fetch(item.url)
    if html is None:
        return None
    product = parse_ldjson_product(html, item.url, item.category)
    if product is None:
        logger.info("no ld+json Product found at %s", item.url)
    return product


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
    products = parse_ldjson_products(html, url, category)
    if not products:
        logger.info("no ld+json Product found at %s", url)
    else:
        logger.info("collected %d product(s) from %s", len(products), url)
    if delay > 0:
        await asyncio.sleep(delay)
    return products


async def collect_links(url: str, delay: float = 1.0) -> list[tuple[str, str]]:
    """Fetch a listing page and return the ``(name, URL)`` pairs it links to.

    For retailers whose listing pages carry an ItemList of links but no prices.
    """
    if not await robots_allows(url):
        return []
    html = await fetch(url)
    if delay > 0:
        await asyncio.sleep(delay)
    if html is None:
        return []
    links = parse_ldjson_links(html, url)
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
